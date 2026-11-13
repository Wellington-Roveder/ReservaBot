import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from repository.database import db

# --- Regras do restaurante ---
FUSO_RESTAURANTE = ZoneInfo("America/Sao_Paulo")
ABERTURA = time(18, 0)
FECHAMENTO = time(2, 0)
DIAS_ABERTOS = {1, 2, 3, 4, 5, 6}  # dias que funcionam
JANELA = timedelta(hours=2)

# Dia da reserva = dia em que o expediente começou. Horários antes das 18:00
# (00:00-01:59) são madrugada: pertencem ao dia informado, mas acontecem no dia seguinte.

_RE_DATA = re.compile(r"\d{4}-\d{2}-\d{2}")
_RE_HORA = re.compile(r"\d{2}:\d{2}")


# helpers
def _agora() -> datetime:
    """Agora no fuso do restaurante, sem tzinfo (permite comparar com os instantes das reservas)."""
    return datetime.now(FUSO_RESTAURANTE).replace(tzinfo=None)


def _erro(mensagem: str) -> dict:
    return {"status": "erro", "mensagem": mensagem}


def _normalizar_telefone(telefone) -> str:
    """Mantém só dígitos e remove o código do país (55), se houver."""
    digitos = re.sub(r"\D", "", str(telefone or ""))
    if len(digitos) in (12, 13) and digitos.startswith("55"):
        digitos = digitos[2:]
    return digitos


def _pessoas_validas(numero_pessoas) -> bool:
    return (
        isinstance(numero_pessoas, int)
        and not isinstance(numero_pessoas, bool)
        and numero_pessoas > 0
    )


def _interpretar(data, horario):
    """Valida o formato estrito AAAA-MM-DD / HH:MM. Levanta ValueError se inválido."""
    if not (
        isinstance(data, str)
        and isinstance(horario, str)
        and _RE_DATA.fullmatch(data)
        and _RE_HORA.fullmatch(horario)
    ):
        raise ValueError("formato inválido")
    dia = datetime.strptime(data, "%Y-%m-%d").date()
    hora = datetime.strptime(horario, "%H:%M").time()
    return dia, hora


def _instante(data: str, horario: str) -> datetime:
    """Momento real da reserva: madrugada (antes das 18:00) acontece no dia seguinte."""
    dia, hora = _interpretar(data, horario)
    instante = datetime.combine(dia, hora)
    if hora < ABERTURA:
        instante += timedelta(days=1)
    return instante


def _validar_slot(data, horario):
    """Retorna (instante, None) se válido ou (None, dict_de_erro)."""
    try:
        dia, hora = _interpretar(data, horario)
    except ValueError:
        return None, _erro(
            "Data ou horário inválido. Use AAAA-MM-DD e HH:MM (ex: 2026-11-20 e 19:30)."
        )

    aberto = dia.weekday() in DIAS_ABERTOS and (hora >= ABERTURA or hora < FECHAMENTO)
    if not aberto:
        return None, _erro("Funcionamos de terça a domingo, das 18:00 às 02:00.")

    instante = _instante(data, horario)
    if instante < _agora():
        return None, _erro("Essa data e horário já passaram.")
    return instante, None


def _mesas_ocupadas(cur, data: str, instante: datetime) -> set:
    """IDs das mesas com reserva a menos de JANELA (inclusive) do instante informado."""
    cur.execute("SELECT mesa_id, data, horario FROM reservas WHERE data = ?", (data,))
    ocupadas = set()
    for r in cur.fetchall():
        try:
            outro = _instante(r["data"], r["horario"])
        except ValueError:
            continue  # linha legada com formato inválido
        if abs(outro - instante) < JANELA:
            ocupadas.add(r["mesa_id"])
    return ocupadas


##queries de criacao
def criar_tabela_mesas():
    with db.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS mesas(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    capacidade INTEGER NOT NULL,
                    status TEXT NOT NULL)""")


def popular_mesas():
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM mesas")
        quantidade_atual = cur.fetchone()[0]

        if quantidade_atual > 0:
            print(f"Seed ignorado: já existem {quantidade_atual} mesas cadastradas.")
            return

        mesas_iniciais = []

        mesas_iniciais.extend([(2, "disponivel")] * 5)

        mesas_iniciais.extend([(4, "disponivel")] * 5)

        mesas_iniciais.extend([(6, "disponivel")] * 3)

        cur.executemany(
            "INSERT INTO mesas (capacidade, status) VALUES (?, ?)", mesas_iniciais
        )


def criar_tabela_reservas():
    with db.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS reservas(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mesa_id INTEGER NOT NULL,
                    nome_cliente TEXT NOT NULL,
                    telefone TEXT NOT NULL,
                    data TEXT NOT NULL,
                    horario TEXT NOT NULL,
                    numero_pessoas INTEGER NOT NULL,
                    FOREIGN KEY (mesa_id) REFERENCES mesas(id) ON DELETE CASCADE)""")


# verificar mesas
def listar_mesas():
    with db.cursor() as cur:
        cur.execute("SELECT id, capacidade, status FROM mesas ORDER BY id ASC")
        mesas = cur.fetchall()

        # Converte para lista de dicionários comuns (facilita o uso em APIs/JSON)
        return [dict(mesa) for mesa in mesas]


# --- LEITURA (READ) ---
def buscar_mesas_livres(
    data: str, horario: str, numero_pessoas: int
) -> list[dict] | dict:
    """Lista as mesas livres para o slot. Retorna dict de erro se os dados forem inválidos."""
    if not _pessoas_validas(numero_pessoas):
        return _erro("O número de pessoas deve ser um inteiro maior que zero.")

    instante, erro = _validar_slot(data, horario)
    if erro:
        return erro

    with db.cursor() as cur:
        ocupadas = _mesas_ocupadas(cur, data, instante)
        cur.execute(
            """SELECT id, capacidade FROM mesas
               WHERE status = 'disponivel' AND capacidade >= ?
               ORDER BY capacidade ASC, id ASC""",
            (numero_pessoas,),
        )
        return [dict(m) for m in cur.fetchall() if m["id"] not in ocupadas]


# --- ESCRITA (CREATE) ---
def criar_reserva(
    mesa_id: int,
    nome_cliente: str,
    telefone: str,
    data: str,
    horario: str,
    numero_pessoas: int,
) -> dict:
    """Registrar a reserva no banco e devolver os dados confirmados ao agente."""
    if not _pessoas_validas(numero_pessoas):
        return _erro("O número de pessoas deve ser um inteiro maior que zero.")

    telefone = _normalizar_telefone(telefone)
    if len(telefone) not in (10, 11):
        return _erro("Telefone inválido. Informe DDD + número (apenas números).")

    instante, erro = _validar_slot(data, horario)
    if erro:
        return erro

    with db.cursor() as cur:
        cur.execute(
            "SELECT capacidade, status FROM mesas WHERE id = ?",
            (mesa_id,),
        )
        mesa = cur.fetchone()
        if not mesa:
            return _erro(f"A mesa {mesa_id} não foi encontrada.")
        if mesa["status"] != "disponivel":
            return _erro(
                f"A mesa {mesa_id} não está disponível para reservas no momento."
            )
        if mesa["capacidade"] < numero_pessoas:
            return _erro(
                f"A mesa {mesa_id} comporta no máximo {mesa['capacidade']} pessoas."
            )
        if mesa_id in _mesas_ocupadas(cur, data, instante):
            return _erro(
                f"A mesa {mesa_id} já possui uma reserva próxima a {horario} no dia {data}."
            )

        # Verifica se o mesmo telefone já tem reserva ativa dentro da JANELA
        cur.execute(
            "SELECT mesa_id, data, horario FROM reservas WHERE telefone = ? AND data = ?",
            (telefone, data),
        )
        for r in cur.fetchall():
            try:
                outro = _instante(r["data"], r["horario"])
            except ValueError:
                continue
            if abs(outro - instante) < JANELA:
                return _erro(
                    f"Você já possui uma reserva na mesa {r['mesa_id']} às {r['horario']} neste dia. "
                    "Não é permitido reservar duas mesas no mesmo horário."
                )

        cur.execute(
            "INSERT INTO reservas (mesa_id, nome_cliente, telefone, data, horario, numero_pessoas) VALUES (?, ?, ?, ?, ?, ?)",
            (mesa_id, nome_cliente, telefone, data, horario, numero_pessoas),
        )
        reserva_id = cur.lastrowid

    return {
        "status": "confirmado",
        "reserva_id": reserva_id,
        "mesa_id": mesa_id,
        "nome_cliente": nome_cliente,
        "telefone": telefone,
        "data": data,
        "horario": horario,
        "numero_pessoas": numero_pessoas,
    }


# --- LEITURA DE RESERVAS (READ) ---
def buscar_reservas_cliente(telefone: str) -> list[dict]:
    """
    Objetivo: Listar as reservas ativas de um cliente pelo telefone para consulta ou cancelamento.
    Ativa = ainda não terminou (início + JANELA >= agora). Ordenadas pelo momento real.
    """
    telefone = _normalizar_telefone(telefone)

    with db.cursor() as cur:
        cur.execute(
            """SELECT id, mesa_id, nome_cliente, data, horario, numero_pessoas
               FROM reservas
               WHERE telefone = ?""",
            (telefone,),
        )
        linhas = [dict(r) for r in cur.fetchall()]

    agora = _agora()
    ativas = []
    for r in linhas:
        try:
            instante = _instante(r["data"], r["horario"])
        except ValueError:
            ativas.append((datetime.max, r))  # linha legada: mantém, no fim da lista
            continue
        if instante + JANELA >= agora:
            ativas.append((instante, r))

    ativas.sort(key=lambda par: par[0])
    return [r for _, r in ativas]


# --- EXCLUSÃO / CANCELAMENTO (DELETE) ---
def cancelar_reserva(reserva_id: int, telefone: str) -> dict:
    """
    Objetivo: Cancelar uma reserva existente validando o telefone do titular por segurança.
    """
    telefone = _normalizar_telefone(telefone)

    with db.cursor() as cur:
        cur.execute(
            "DELETE FROM reservas WHERE id = ? AND telefone = ?",
            (reserva_id, telefone),
        )

        if cur.rowcount == 0:
            return _erro("Reserva não encontrada ou telefone não confere.")

    return {
        "status": "cancelado",
        "mensagem": f"Reserva {reserva_id} cancelada com sucesso.",
    }
