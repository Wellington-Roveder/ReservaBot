import json
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

FUSO_RESTAURANTE = ZoneInfo("America/Sao_Paulo")

# Tabela de referência (USD por 1 milhão de tokens: input, output).
TABELA_PRECOS_POR_MILHAO: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "openai/gpt-4o-mini": (0.15, 0.60),
    "google/gemini-2.0-flash-001": (0.10, 0.40),
    "meta-llama/llama-3.3-70b-instruct": (0.12, 0.30),
}


def calcular_custo_estimado(
    modelo: str | None, prompt_tokens: int, completion_tokens: int
) -> float:
    """
    Calcula o custo aproximado da interação em USD.
    Prioridade:
      1. Variáveis PRICE_INPUT_PER_M e PRICE_OUTPUT_PER_M no .env (flexibilidade sem deploy)
      2. Sufixo ':free' do OpenRouter -> $0.00
      3. Tabela interna de referência -> fallback seguro
    """
    modelo_norm = (modelo or "").strip().lower()

    env_in = (os.getenv("PRICE_INPUT_PER_M") or "").strip()
    env_out = (os.getenv("PRICE_OUTPUT_PER_M") or "").strip()

    if env_in and env_out:
        preco_in, preco_out = float(env_in), float(env_out)
    elif modelo_norm.endswith(":free"):
        preco_in, preco_out = 0.0, 0.0
    else:
        preco_in, preco_out = TABELA_PRECOS_POR_MILHAO.get(modelo_norm, (0.15, 0.60))

    custo = (prompt_tokens / 1_000_000) * preco_in + (
        completion_tokens / 1_000_000
    ) * preco_out
    return round(custo, 6)


def _obter_trace_logger() -> logging.Logger:
    """
    Cria um logger isolado ('reservabot.traces') que não polui o console
    (propagate=False) e escreve apenas a string JSON pura em logs/traces.jsonl.
    """
    logger = logging.getLogger("reservabot.traces")
    if any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        return logger  # Evita duplicar handlers a cada rerun do Streamlit!

    logger.setLevel(logging.INFO)
    logger.propagate = False

    raiz = Path(__file__).resolve().parent.parent
    pasta_logs = raiz / "logs"
    pasta_logs.mkdir(parents=True, exist_ok=True)

    handler = logging.FileHandler(pasta_logs / "traces.jsonl", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def registrar_trace(evento: dict) -> None:
    """Adiciona timestamp no fuso do restaurante e persiste o evento no JSONL."""
    payload = {
        "timestamp": datetime.now(FUSO_RESTAURANTE).isoformat(),
        **evento,
    }
    _obter_trace_logger().info(json.dumps(payload, ensure_ascii=False))
