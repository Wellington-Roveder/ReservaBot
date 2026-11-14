import inspect
import logging
import os
import time

import streamlit as st

CAMINHO_ICONE = "assets/icone.png"
ICONE = CAMINHO_ICONE if os.path.exists(CAMINHO_ICONE) else None

st.set_page_config(
    page_title="ReservaBot",
    page_icon=ICONE,
    layout="wide",
)

import pandas as pd

from repository.queries import (
    criar_tabela_mesas,
    criar_tabela_reservas,
    listar_todas_reservas,
    popular_mesas,
)
from services.nlu_config.nlu import SemanticNLU
from services.openai_service import OpenAISystem

logger = logging.getLogger(__name__)

# ---------- Constantes ----------
AVATARES = {"assistant": ICONE or ":material/smart_toy:", "user": ":material/person:"}
MENSAGEM_ERRO = (
    "Desculpe, ocorreu uma instabilidade ao consultar o sistema. "
    "Tente novamente em instantes."
)

MENSAGEM_FORA_ESCOPO = (
    "Olá! Eu sou o ReservaBot e só consigo ajudar com reservas do restaurante: "
    "criar, consultar ou cancelar uma reserva. Como posso te ajudar com isso?"
)

# Mensagem fixa de apresentação: exibida na tela, mas NUNCA enviada ao LLM
MENSAGEM_BOAS_VINDAS = (
    "Olá! Eu sou o **ReservaBot**, assistente de reservas do restaurante.\n\n"
    "**Funcionamento:** terça a domingo, das 18h às 02h.\n\n"
    "**Posso te ajudar a:**\n"
    "- Reservar uma mesa\n"
    "- Consultar suas reservas\n"
    "- Cancelar uma reserva\n\n"
    "Clique em um exemplo acima ou digite sua mensagem para começar."
)

EXEMPLOS_RAPIDOS = [
    "Quero reservar para 4 pessoas amanhã às 20h",
    "Consultar minhas reservas",
    "Qual o horário de funcionamento?",
]

# Colunas reais da tabela "reservas" (queries.listar_todas_reservas)
COLUNAS_AMIGAVEIS = {
    "nome_cliente": "Cliente",
    "mesa_id": "Mesa",
    "data": "Data",
    "horario": "Horário",
    "numero_pessoas": "Pessoas",
    "telefone": "Telefone",
}

CONFIG_COLUNAS = {
    "Cliente": st.column_config.TextColumn(width="medium"),
    "Mesa": st.column_config.NumberColumn(width="small"),
    "Data": st.column_config.TextColumn(width="small"),
    "Horário": st.column_config.TextColumn(width="small"),
    "Pessoas": st.column_config.NumberColumn(width="small"),
    "Telefone": st.column_config.TextColumn(width="small"),
}

CSS = """
<style>
    #MainMenu, footer {visibility: hidden;}
    .block-container {padding-top: 2.2rem; max-width: 980px;}
    [data-testid="stSidebar"][aria-expanded="true"] {min-width: 340px;}
    [data-testid="stSidebar"] h3 {font-size: 0.8rem; text-transform: uppercase;
        letter-spacing: 0.08em; opacity: 0.65; margin-bottom: 0.4rem;}
    [data-testid="stChatMessage"] {border-radius: 14px; padding: 0.9rem 1rem;}
    .rb-title {font-size: 2rem; font-weight: 700; line-height: 1.1; margin: 0;}
    .rb-sub {opacity: 0.65; margin: 0.15rem 0 0 0;}
</style>
"""


# ---------- Inicialização ----------
@st.cache_resource
def inicializar_sistema():
    """Roda uma única vez: evita recarregar o fastembed a cada mensagem."""
    criar_tabela_mesas()
    criar_tabela_reservas()
    popular_mesas()
    nlu, llm = SemanticNLU(), OpenAISystem()
    # Se gerar_resposta aceitar o callback on_step, as etapas reais aparecem na UI
    aceita_on_step = "on_step" in inspect.signature(llm.gerar_resposta).parameters
    return nlu, llm, aceita_on_step


# ---------- Helpers ----------
def formatar_intencao(telemetria: dict) -> tuple[str, float]:
    intencao = str(telemetria.get("intent", "-")).replace("_", " ")
    return intencao, float(telemetria.get("confidence", 0.0))


def montar_tabela_reservas(reservas: list[dict]) -> pd.DataFrame:
    """Prepara a tabela para exibição pública: formata a data e mascara o telefone."""
    df = pd.DataFrame(reservas)
    df["telefone"] = "***" + df["telefone"].astype(str).str[-4:]
    datas = pd.to_datetime(df["data"], format="%Y-%m-%d", errors="coerce")
    df["data"] = datas.dt.strftime("%d/%m").fillna(df["data"])
    return df[list(COLUNAS_AMIGAVEIS)].rename(columns=COLUNAS_AMIGAVEIS)


# ---------- Componentes de UI ----------
def painel_telemetria(telemetria: dict | None) -> None:
    if not telemetria:
        st.caption("Envie uma mensagem para ver a telemetria do roteador.")
        return

    intencao, score = formatar_intencao(telemetria)
    latencia = telemetria.get("latencia_s")

    col_confianca, col_tempo = st.columns(2)
    col_confianca.metric("Confiança", f"{score:.0%}")
    if latencia is not None:
        col_tempo.metric("Tempo", f"{latencia:.1f} s")
    st.progress(min(max(score, 0.0), 1.0))

    if telemetria.get("in_scope"):
        st.success("Passou no gatekeeper (dentro do escopo).")
    elif telemetria.get("bloqueada"):
        st.warning("Bloqueada pelo gatekeeper (sem chamada à API).")
    else:
        st.info("Fluxo multi-turno ativo (bypass do gatekeeper).")

    st.caption("Intenção detectada")
    st.badge(intencao, color="blue")


def painel_reservas() -> None:
    reservas = listar_todas_reservas()
    if not reservas:
        st.caption("Nenhuma reserva cadastrada.")
        return
    st.caption(f"{len(reservas)} reserva(s) cadastrada(s)")
    st.dataframe(
        montar_tabela_reservas(reservas),
        hide_index=True,
        width="stretch",
        column_config=CONFIG_COLUNAS,
    )


def renderizar_sidebar() -> None:
    sb = st.sidebar
    col_logo, col_nome = sb.columns([1, 3], vertical_alignment="center")
    if ICONE:
        col_logo.image(ICONE, width=52)
    col_nome.markdown("**ReservaBot**  \nPainel de observabilidade")

    if sb.button("Limpar conversa", icon=":material/delete:", width="stretch"):
        st.session_state.messages = []
        st.session_state.ultima_telemetria = None
        st.rerun()

    sb.divider()
    sb.markdown("### Roteador semântico")
    with sb.container(border=True):
        painel_telemetria(st.session_state.ultima_telemetria)

    sb.markdown("### Banco de reservas")
    with sb.container(border=True):
        painel_reservas()

    with sb.expander("Sobre a arquitetura"):
        st.markdown(
            "- **Gatekeeper local:** embeddings MiniLM-L12 (`fastembed`) "
            "com threshold 0.50.\n"
            "- **Orquestração:** OpenAI SDK com Function Calling.\n"
            "- **Persistência:** SQLite com validação de fuso "
            "(`America/Sao_Paulo`) e janela de 2h."
        )

    sb.divider()
    sb.caption("Desenvolvido por Wellington Roveder")


def renderizar_cabecalho() -> None:
    col_icone, col_titulo = st.columns([1, 12], vertical_alignment="center")
    if ICONE:
        col_icone.image(ICONE, width=64)
    col_titulo.markdown(
        '<p class="rb-title">ReservaBot</p>'
        '<p class="rb-sub">Reservas de mesa por conversa, com IA</p>',
        unsafe_allow_html=True,
    )


def renderizar_acoes_rapidas() -> str | None:
    """Mostra os botões de exemplo e devolve o texto do botão clicado (se houver)."""
    clicado = None
    for i, (coluna, exemplo) in enumerate(zip(st.columns(3), EXEMPLOS_RAPIDOS)):
        if coluna.button(exemplo, key=f"exemplo_{i}", width="stretch"):
            clicado = exemplo
    return clicado


def renderizar_mensagem(role: str, conteudo: str) -> None:
    with st.chat_message(role, avatar=AVATARES[role]):
        st.markdown(conteudo)


# ---------- Fluxo de resposta ----------
def responder(prompt: str, nlu, llm, aceita_on_step: bool) -> str:
    """Classifica a mensagem, decide o caminho e devolve a resposta do assistente."""
    primeira_mensagem = len(st.session_state.messages) == 1
    inicio = time.perf_counter()

    # Um único `with` para o balão e o painel de etapas (sem aninhamento)
    with (
        st.chat_message("assistant", avatar=AVATARES["assistant"]),
        st.status("Processando solicitação...", expanded=True) as status,
    ):
        st.write("Classificando intenção...")
        telemetria = dict(nlu.classificar(prompt))
        bloqueada = not telemetria["in_scope"] and primeira_mensagem
        telemetria["bloqueada"] = bloqueada
        st.session_state.ultima_telemetria = telemetria

        intencao, score = formatar_intencao(telemetria)
        st.write(f"Intenção detectada: **{intencao}** (confiança {score:.0%})")

        estado, rotulo = "complete", "Resposta pronta"

        if bloqueada:
            # Sem gastar chamada de API
            st.write("Fora do escopo: usando resposta padrão, sem chamar a API.")
            resposta = MENSAGEM_FORA_ESCOPO
        else:
            if not telemetria["in_scope"]:
                st.write("Fluxo já em andamento: gatekeeper ignorado nesta mensagem.")
            st.write("Gerando resposta...")
            extra = {"on_step": st.write} if aceita_on_step else {}
            try:
                resposta = llm.gerar_resposta(st.session_state.messages, **extra)
            except Exception:
                # Detalhes só no log do servidor: não expõe erro técnico ao público
                logger.exception("Falha ao gerar resposta")
                resposta = MENSAGEM_ERRO
                estado, rotulo = "error", "Falha ao consultar o sistema"

        telemetria["latencia_s"] = time.perf_counter() - inicio
        status.update(label=rotulo, state=estado, expanded=estado == "error")

    return resposta


# ---------- App ----------
st.markdown(CSS, unsafe_allow_html=True)

nlu, llm, aceita_on_step = inicializar_sistema()

st.session_state.setdefault("messages", [])
st.session_state.setdefault("ultima_telemetria", None)

renderizar_sidebar()
renderizar_cabecalho()
acao_rapida = renderizar_acoes_rapidas()

prompt = st.chat_input("Ex.: Quero reservar uma mesa para 4 pessoas amanhã às 20h")
prompt = prompt or acao_rapida

# Boas-vindas fixas (não entram em st.session_state.messages)
renderizar_mensagem("assistant", MENSAGEM_BOAS_VINDAS)
for msg in st.session_state.messages:
    renderizar_mensagem(msg["role"], msg["content"])

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    renderizar_mensagem("user", prompt)

    resposta = responder(prompt, nlu, llm, aceita_on_step)

    st.session_state.messages.append({"role": "assistant", "content": resposta})
    st.rerun()  # atualiza a sidebar imediatamente
