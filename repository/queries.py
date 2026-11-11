from repository.database import db


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
def buscar_mesas_livres(data: str, horario: str, numero_pessoas: int) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            """SELECT id, capacidade FROM mesas
               WHERE status = 'disponivel'
                 AND capacidade >= ?
                 AND id NOT IN (
                     SELECT mesa_id FROM reservas
                     WHERE data = ?
                       AND time(horario) BETWEEN time(?, '-2 hours') AND time(?, '+2 hours')
                 )
               ORDER BY capacidade ASC""",
            (numero_pessoas, data, horario, horario),
        )
        mesas = cur.fetchall()

    return [dict(mesa) for mesa in mesas]


# --- ESCRITA (CREATE) ---
def criar_reserva(
    mesa_id: int,
    nome_cliente: str,
    telefone: str,
    data: str,
    horario: str,
    numero_pessoas: int,
) -> dict:
    """
    Objetivo: Registrar a reserva no banco e devolver os dados confirmados ao agente.
    """
    with db.cursor() as cur:
        cur.execute(
            "SELECT capacidade, status FROM mesas WHERE id = ?",
            (mesa_id,),
        )
        mesa = cur.fetchone()

        if not mesa:
            return {
                "status": "erro",
                "mensagem": f"A mesa {mesa_id} não foi encontrada.",
            }

        if mesa["status"] != "disponivel":
            return {
                "status": "erro",
                "mensagem": f"A mesa {mesa_id} não está disponível para reservas no momento.",
            }

        if mesa["capacidade"] < numero_pessoas:
            return {
                "status": "erro",
                "mensagem": f"A mesa {mesa_id} comporta no máximo {mesa['capacidade']} pessoas.",
            }

        cur.execute(
            """SELECT id FROM reservas
               WHERE mesa_id = ?
                 AND data = ?
                 AND time(horario) BETWEEN time(?, '-2 hours') AND time(?, '+2 hours')""",
            (mesa_id, data, horario, horario),
        )
        conflito = cur.fetchone()

        if conflito:
            return {
                "status": "erro",
                "mensagem": f"A mesa {mesa_id} já possui uma reserva próxima a {horario} no dia {data}.",
            }

        cur.execute(
            """INSERT INTO reservas (mesa_id, nome_cliente, telefone, data, horario, numero_pessoas)
               VALUES (?, ?, ?, ?, ?, ?)""",
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
    """
    with db.cursor() as cur:
        cur.execute(
            """SELECT id, mesa_id, nome_cliente, data, horario, numero_pessoas
               FROM reservas
               WHERE telefone = ?
               ORDER BY data ASC, horario ASC""",
            (telefone,),
        )
        reservas = cur.fetchall()

    return [dict(r) for r in reservas]


# --- EXCLUSÃO / CANCELAMENTO (DELETE) ---
def cancelar_reserva(reserva_id: int, telefone: str) -> dict:
    """
    Objetivo: Cancelar uma reserva existente validando o telefone do titular por segurança.
    """
    with db.cursor() as cur:
        cur.execute(
            "DELETE FROM reservas WHERE id = ? AND telefone = ?",
            (reserva_id, telefone),
        )

        if cur.rowcount == 0:
            return {
                "status": "erro",
                "mensagem": "Reserva não encontrada ou telefone não confere.",
            }

    return {
        "status": "cancelado",
        "mensagem": f"Reserva {reserva_id} cancelada com sucesso.",
    }
