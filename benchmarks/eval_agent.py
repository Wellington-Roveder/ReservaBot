import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from repository import queries
from repository.database import db
from services.openai_service import OpenAISystem

TZ_SP = ZoneInfo("America/Sao_Paulo")

TOOLS_MONITORADAS = [
    "listar_mesas",
    "buscar_mesas_livres",
    "criar_reserva",
    "buscar_reservas_cliente",
    "cancelar_reserva",
]


def resolver_data_relativa(chave: str) -> str:
    """Converte chaves relativas ('hoje', 'amanha', 'proxima_sexta') em AAAA-MM-DD."""
    hoje = datetime.now(TZ_SP).date()
    if chave == "hoje":
        # Se hoje for segunda-feira (weekday == 0), avança para terça para testes válidos
        alvo = hoje + timedelta(days=1) if hoje.weekday() == 0 else hoje
    elif chave == "amanha":
        alvo = hoje + timedelta(days=1)
        if alvo.weekday() == 0:
            alvo += timedelta(days=1)
    elif chave == "proxima_sexta":
        dias_ate_sexta = (4 - hoje.weekday()) % 7
        dias_ate_sexta = 7 if dias_ate_sexta == 0 else dias_ate_sexta
        alvo = hoje + timedelta(days=dias_ate_sexta)
    elif chave == "segunda_fechada":
        dias_ate_segunda = (0 - hoje.weekday()) % 7
        dias_ate_segunda = 7 if dias_ate_segunda == 0 else dias_ate_segunda
        alvo = hoje + timedelta(days=dias_ate_segunda)
    else:
        raise ValueError(f"Data relativa desconhecida: {chave}")
    return alvo.strftime("%Y-%m-%d")


def normalizar_telefone(tel: str) -> str:
    """Mantém só dígitos e remove o código do país (55), se houver."""
    digitos = re.sub(r"\D", "", str(tel or ""))
    if len(digitos) in (12, 13) and digitos.startswith("55"):
        digitos = digitos[2:]
    return digitos


def resetar_banco_memoria(seed_reservas: list[dict]):
    """
    Configura o SqlitePool global para usar ':memory:' limpo a cada cenário,
    cria as tabelas, popula as mesas padrão e insere o seed do teste.
    """
    if hasattr(db._local, "conn") and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None

    db.db_path = ":memory:"

    queries.criar_tabela_mesas()
    queries.criar_tabela_reservas()
    queries.popular_mesas()

    for res in seed_reservas:
        data_str = (
            resolver_data_relativa(res["data_relativa"])
            if "data_relativa" in res
            else res["data"]
        )
        resultado = queries.criar_reserva(
            mesa_id=res["mesa_id"],
            nome_cliente=res["nome_cliente"],
            telefone=res["telefone"],
            data=data_str,
            horario=res["horario"],
            numero_pessoas=res["numero_pessoas"],
        )
        if resultado.get("status") != "confirmado":
            raise RuntimeError(f"Falha ao inserir seed_reserva: {resultado}")


def contar_reservas_banco() -> int:
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM reservas")
        return cur.fetchone()[0]


@contextmanager
def interceptar_tools(log_chamadas: list[dict]):
    """
    Envolve temporariamente as funções de repository.queries usadas pelo OpenAISystem,
    registrando nome, argumentos e retorno sem alterar a execução no banco.
    """
    originais = {}
    for nome_fn in TOOLS_MONITORADAS:
        fn_original = getattr(queries, nome_fn)
        originais[nome_fn] = fn_original

        def criar_wrapper(nome: str, func):
            def wrapper(**kwargs):
                saida = func(**kwargs)
                log_chamadas.append({"tool": nome, "args": kwargs, "output": saida})
                return saida

            return wrapper

        setattr(queries, nome_fn, criar_wrapper(nome_fn, fn_original))

    try:
        yield
    finally:
        for nome_fn, fn_original in originais.items():
            setattr(queries, nome_fn, fn_original)


def avaliar_selecao_tools(caso: dict, log_chamadas: list[dict]) -> tuple[bool, str]:
    chamadas = [c["tool"] for c in log_chamadas]
    esperadas = caso.get("tools_esperadas", [])
    proibidas = caso.get("tools_proibidas", [])

    for proibida in proibidas:
        if proibida in chamadas:
            return False, f"Chamou tool proibida '{proibida}' (sequência: {chamadas})"

    idx = 0
    for tool_esp in esperadas:
        try:
            pos = chamadas.index(tool_esp, idx)
            idx = pos + 1
        except ValueError:
            return False, f"Esperava {esperadas}, mas chamou {chamadas}"

    return True, f"OK ({chamadas})"


def avaliar_argumentos(caso: dict, log_chamadas: list[dict]) -> tuple[bool | None, str]:
    spec = caso.get("argumentos_esperados")
    if not spec:
        return None, "N/A (sem extração esperada)"

    tool_alvo = spec["tool"]
    chamadas_alvo = [c for c in log_chamadas if c["tool"] == tool_alvo]
    if not chamadas_alvo:
        return False, f"Tool '{tool_alvo}' não foi chamada"

    args = chamadas_alvo[-1]["args"]
    erros = []

    # 1. Data
    data_esperada = None
    if "data_relativa" in spec:
        data_esperada = resolver_data_relativa(spec["data_relativa"])
    elif "data_fixa" in spec:
        data_esperada = spec["data_fixa"]

    if data_esperada:
        data_obtida = str(args.get("data", ""))
        if data_obtida != data_esperada:
            erros.append(f"data={data_obtida!r} (esperado {data_esperada!r})")

    # 2. Horário
    if "horario" in spec:
        hora_obtida = str(args.get("horario", ""))
        if hora_obtida != spec["horario"]:
            erros.append(f"horario={hora_obtida!r} (esperado {spec['horario']!r})")

    # 3. Quantidade de pessoas
    if "numero_pessoas" in spec:
        pessoas_obtidas = args.get("numero_pessoas")
        if int(pessoas_obtidas or -1) != int(spec["numero_pessoas"]):
            erros.append(
                f"numero_pessoas={pessoas_obtidas!r} (esperado {spec['numero_pessoas']!r})"
            )

    # 4. Telefone normalizado
    if "telefone" in spec:
        tel_obtido = normalizar_telefone(args.get("telefone", ""))
        if tel_obtido != spec["telefone"]:
            erros.append(f"telefone={tel_obtido!r} (esperado {spec['telefone']!r})")

    # 5. Nome do cliente
    if "nome_contem" in spec:
        nome_obtido = str(args.get("nome_cliente", ""))
        if spec["nome_contem"].lower() not in nome_obtido.lower():
            erros.append(
                f"nome_cliente={nome_obtido!r} (esperado conter {spec['nome_contem']!r})"
            )

    if erros:
        return False, "; ".join(erros)
    return True, "OK"


def avaliar_persistencia(
    caso: dict, total_inicial: int, total_final: int
) -> tuple[bool | None, str]:
    if caso.get("deve_persistir_reserva"):
        ok = total_final == total_inicial + 1
        return ok, f"Reservas: {total_inicial} -> {total_final} (esperado +1)"

    if caso.get("deve_cancelar_reserva"):
        ok = total_final == total_inicial - 1
        return ok, f"Reservas: {total_inicial} -> {total_final} (esperado -1)"

    if "total_reservas_final_esperado" in caso:
        esperado = caso["total_reservas_final_esperado"]
        ok = total_final == esperado
        return ok, f"Reservas finais: {total_final} (esperado {esperado})"

    return None, "N/A"


def executar_avaliacao(caminho_dataset: Path):
    with open(caminho_dataset, "r", encoding="utf-8") as f:
        casos = json.load(f)

    total_casos = len(casos)
    acertos_tools = 0

    total_com_args = 0
    acertos_args = 0

    total_tarefas_criacao = 0
    acertos_tarefas_criacao = 0

    agente = OpenAISystem()

    relatorio_linhas = [
        "=" * 80,
        "AVALIAÇÃO PONTA A PONTA DO AGENTE (LLM EVAL) - RESERVABOT",
        f"Modelo: {os.getenv('MODEL_NAME', 'não definido')} | Casos: {total_casos}",
        "=" * 80,
    ]

    for caso in casos:
        resetar_banco_memoria(caso.get("seed_reservas", []))
        total_inicial = contar_reservas_banco()

        log_chamadas: list[dict[str, Any]] = []
        historico_mensagem: list[dict[str, str]] = []
        erro_execucao = None

        with interceptar_tools(log_chamadas):
            for msg in caso["mensagens"]:
                historico_mensagem.append({"role": "user", "content": msg})
                try:
                    resposta = agente.gerar_resposta(historico_mensagem)
                    historico_mensagem.append(
                        {"role": "assistant", "content": resposta}
                    )
                except Exception as exc:
                    erro_execucao = str(exc)
                    break

        total_final = contar_reservas_banco()

        if erro_execucao:
            relatorio_linhas.append(f"[{caso['id']}] ERRO DE EXECUÇÃO: {erro_execucao}")
            continue

        # 1. Tool Selection Accuracy
        tool_ok, tool_msg = avaliar_selecao_tools(caso, log_chamadas)
        if tool_ok:
            acertos_tools += 1

        # 2. Argument Extraction Accuracy
        args_ok, args_msg = avaliar_argumentos(caso, log_chamadas)
        if args_ok is not None:
            total_com_args += 1
            if args_ok:
                acertos_args += 1

        # 3. Task Completion Rate
        persist_ok, persist_msg = avaliar_persistencia(caso, total_inicial, total_final)
        if caso.get("deve_persistir_reserva"):
            total_tarefas_criacao += 1
            if persist_ok:
                acertos_tarefas_criacao += 1

        status_geral = (
            "PASS"
            if (tool_ok and (args_ok in (True, None)) and (persist_ok in (True, None)))
            else "FAIL"
        )
        relatorio_linhas.append(
            f"[{caso['id']}] {status_geral} | {caso['descricao']}\n"
            f"   - Tools: {tool_msg}\n"
            f"   - Args : {args_msg}\n"
            f"   - Banco: {persist_msg}"
        )

    # Fecha conexão em memória ao final
    if hasattr(db._local, "conn") and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None

    tool_acc = (acertos_tools / total_casos * 100) if total_casos else 0.0
    arg_acc = (acertos_args / total_com_args * 100) if total_com_args else 0.0
    task_rate = (
        (acertos_tarefas_criacao / total_tarefas_criacao * 100)
        if total_tarefas_criacao
        else 0.0
    )

    relatorio_linhas.extend(
        [
            "",
            "=" * 80,
            "RESUMO DAS MÉTRICAS (LLM EVAL)",
            "=" * 80,
            f"Tool Selection Accuracy      : {tool_acc:6.2f}% ({acertos_tools}/{total_casos} casos)",
            f"Argument Extraction Accuracy : {arg_acc:6.2f}% ({acertos_args}/{total_com_args} casos com extração)",
            f"Task Completion Rate         : {task_rate:6.2f}% ({acertos_tarefas_criacao}/{total_tarefas_criacao} reservas gravadas)",
            "=" * 80,
        ]
    )

    saida_completa = "\n".join(relatorio_linhas)
    print(saida_completa)

    caminho_saida = ROOT_DIR / "benchmarks" / "resultados_agent.txt"
    with open(caminho_saida, "w", encoding="utf-8") as f:
        f.write(saida_completa + "\n")


if __name__ == "__main__":
    dataset = ROOT_DIR / "benchmarks" / "eval_agent_cases.json"
    executar_avaliacao(dataset)
