import json
import logging
from datetime import datetime

import pytest

from services import telemetry


class TestCalcularCustoEstimado:
    def test_override_via_env_tem_prioridade_maxima(self, monkeypatch):
        """Variáveis PRICE_INPUT_PER_M e PRICE_OUTPUT_PER_M sobrescrevem até modelos :free."""
        monkeypatch.setenv("PRICE_INPUT_PER_M", "2.00")
        monkeypatch.setenv("PRICE_OUTPUT_PER_M", "8.00")

        custo = telemetry.calcular_custo_estimado(
            modelo="nvidia/nemotron-3-ultra-550b-a55b:free",
            prompt_tokens=500_000,
            completion_tokens=250_000,
        )

        # (0.5M * 2.00) + (0.25M * 8.00) = 1.00 + 2.00 = 3.00
        assert custo == 3.0

    def test_env_vazio_ignora_override_e_usa_regras_seguintes(self, monkeypatch):
        """Strings vazias no .env (ex: PRICE_INPUT_PER_M=) não devem quebrar float()."""
        monkeypatch.setenv("PRICE_INPUT_PER_M", "   ")
        monkeypatch.setenv("PRICE_OUTPUT_PER_M", "")

        custo = telemetry.calcular_custo_estimado(
            modelo="nvidia/nemotron-3-ultra-550b-a55b:free",
            prompt_tokens=10_000,
            completion_tokens=5_000,
        )

        assert custo == 0.0

    def test_modelo_free_retorna_custo_zero(self, monkeypatch):
        """Modelos terminados em ':free' sem override no .env devem custar $0.00."""
        monkeypatch.delenv("PRICE_INPUT_PER_M", raising=False)
        monkeypatch.delenv("PRICE_OUTPUT_PER_M", raising=False)

        custo = telemetry.calcular_custo_estimado(
            modelo="meta-llama/llama-3.3-70b-instruct:free",
            prompt_tokens=4_000,
            completion_tokens=600,
        )

        assert custo == 0.0

    def test_modelo_presente_na_tabela_interna(self, monkeypatch):
        """Modelos mapeados em TABELA_PRECOS_POR_MILHAO usam suas tarifas específicas."""
        monkeypatch.delenv("PRICE_INPUT_PER_M", raising=False)
        monkeypatch.delenv("PRICE_OUTPUT_PER_M", raising=False)

        # gpt-4o na tabela: ($2.50 input, $10.00 output)
        custo = telemetry.calcular_custo_estimado(
            modelo="GPT-4O",  # valida normalização case-insensitive
            prompt_tokens=1_000_000,
            completion_tokens=500_000,
        )

        # (1M * 2.50) + (0.5M * 10.00) = 2.50 + 5.00 = 7.50
        assert custo == 7.5

    def test_modelo_desconhecido_usa_fallback_padrao(self, monkeypatch):
        """Modelos fora da tabela e sem .env usam o fallback ($0.15 in / $0.60 out)."""
        monkeypatch.delenv("PRICE_INPUT_PER_M", raising=False)
        monkeypatch.delenv("PRICE_OUTPUT_PER_M", raising=False)

        custo = telemetry.calcular_custo_estimado(
            modelo="provedor/modelo-custom-desconhecido",
            prompt_tokens=3_966,
            completion_tokens=434,
        )

        assert custo == 0.000855


class TestRegistrarTrace:
    @pytest.fixture
    def arquivo_log_isolado(self, tmp_path, monkeypatch):
        """Redireciona logs/traces.jsonl para tmp_path e reseta os handlers do logger."""
        logger = logging.getLogger("reservabot.traces")
        for h in list(logger.handlers):
            h.close()
            logger.removeHandler(h)

        # Simula services/telemetry.py dentro de tmp_path para que parent.parent seja tmp_path
        fake_module_path = tmp_path / "services" / "telemetry.py"
        fake_module_path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(telemetry, "__file__", str(fake_module_path))

        yield tmp_path / "logs" / "traces.jsonl"

        for h in list(logger.handlers):
            h.close()
            logger.removeHandler(h)

    def test_registrar_trace_cria_jsonl_valido_com_timestamp(self, arquivo_log_isolado):
        evento_1 = {
            "trace_id": "abc12345",
            "intencao_detectada": "fazer reserva",
            "tools_acionadas": [
                {"nome": "buscar_mesas_livres", "duracao_ms": 1.2, "sucesso": True}
            ],
            "total_tokens": 1500,
            "custo_estimado_usd": 0.0003,
            "latencia_total_ms": 4200.5,
        }
        evento_2 = {
            "trace_id": "def67890",
            "intencao_detectada": "fora de escopo",
            "tools_acionadas": [],
            "total_tokens": 0,
            "custo_estimado_usd": 0.0,
            "latencia_total_ms": 85.1,
        }

        telemetry.registrar_trace(evento_1)
        telemetry.registrar_trace(evento_2)

        assert arquivo_log_isolado.exists()

        linhas = arquivo_log_isolado.read_text(encoding="utf-8").strip().splitlines()
        assert len(linhas) == 2

        registro_1 = json.loads(linhas[0])
        registro_2 = json.loads(linhas[1])

        assert registro_1["trace_id"] == "abc12345"
        assert registro_1["tools_acionadas"][0]["nome"] == "buscar_mesas_livres"
        assert registro_2["trace_id"] == "def67890"
        assert registro_2["total_tokens"] == 0

        # Garante que o timestamp gerado é ISO-8601 válido com fuso horário
        dt_parsed = datetime.fromisoformat(registro_1["timestamp"])
        assert dt_parsed.tzinfo is not None

    def test_logger_nao_duplica_handlers_em_multiplas_chamadas(
        self, arquivo_log_isolado
    ):
        """Garante que reruns do Streamlit não dupliquem registros no arquivo JSONL."""
        telemetry.registrar_trace({"trace_id": "unico001"})
        telemetry.registrar_trace({"trace_id": "unico002"})

        logger = logging.getLogger("reservabot.traces")
        file_handlers = [
            h for h in logger.handlers if isinstance(h, logging.FileHandler)
        ]
        assert len(file_handlers) == 1

        linhas = arquivo_log_isolado.read_text(encoding="utf-8").strip().splitlines()
        assert len(linhas) == 2
