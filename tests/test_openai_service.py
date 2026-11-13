import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from services import openai_service


class FakeOpenAI:
    """Cliente OpenAI falso: devolve, a cada chamada, o próximo passo do roteiro.

    Passos: ("tool", nome, argumentos_json) ou ("text", conteudo).
    Repete o último passo se o roteiro acabar.
    """

    def __init__(self, roteiro):
        self.roteiro = roteiro
        self.chamadas = []  # snapshot das mensagens enviadas em cada chamada
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.chamadas.append(list(kwargs["messages"]))
        passo = self.roteiro[min(len(self.chamadas) - 1, len(self.roteiro) - 1)]
        if passo[0] == "tool":
            chamada = SimpleNamespace(
                id=f"call_{len(self.chamadas)}",
                function=SimpleNamespace(name=passo[1], arguments=passo[2]),
            )
            msg = SimpleNamespace(content=None, tool_calls=[chamada])
        else:
            msg = SimpleNamespace(content=passo[1], tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


@pytest.fixture
def montar(monkeypatch, queries):
    """Cria (OpenAISystem, FakeOpenAI) com um roteiro. Usa o banco temporário da fixture `queries`."""

    def _montar(roteiro):
        fake = FakeOpenAI(roteiro)
        monkeypatch.setattr(openai_service, "OpenAI", lambda **kw: fake)
        return openai_service.OpenAISystem(), fake

    return _montar


HISTORICO = [{"role": "user", "content": "quero uma mesa"}]


def _resultados_de_tools(chamada):
    """Conteúdo (já como dict) das mensagens de tool enviadas ao modelo numa chamada."""
    return [
        json.loads(m["content"])
        for m in chamada
        if isinstance(m, dict) and m.get("role") == "tool"
    ]


class TestLoopDeTools:
    def test_devolve_resultado_da_tool_ao_modelo_e_retorna_resposta_final(self, montar):
        ia, fake = montar(
            [("tool", "listar_mesas", "{}"), ("text", "Temos mesas livres!")]
        )
        assert ia.gerar_resposta(HISTORICO) == "Temos mesas livres!"
        assert len(fake.chamadas) == 2
        mesas = _resultados_de_tools(fake.chamadas[1])[0]
        assert len(mesas) == 13  # o modelo recebeu o resultado real da tool

    def test_resposta_sem_tool_faz_uma_unica_chamada(self, montar):
        ia, fake = montar([("text", "Olá!")])
        assert ia.gerar_resposta(HISTORICO) == "Olá!"
        assert len(fake.chamadas) == 1

    def test_limite_de_iteracoes(self, montar):
        ia, fake = montar([("tool", "listar_mesas", "{}")])  # nunca para de pedir tool
        resposta = ia.gerar_resposta(HISTORICO)
        assert "limite de tentativas" in resposta
        assert len(fake.chamadas) == 5

    def test_fluxo_completo_cria_reserva_de_verdade(self, montar, queries):
        args = json.dumps(
            {
                "mesa_id": 1,
                "nome_cliente": "Ana",
                "telefone": "11988887777",
                "data": "2026-01-15",
                "horario": "19:00",
                "numero_pessoas": 2,
            }
        )
        ia, fake = montar(
            [("tool", "criar_reserva", args), ("text", "Reserva confirmada!")]
        )
        assert ia.gerar_resposta(HISTORICO) == "Reserva confirmada!"
        assert _resultados_de_tools(fake.chamadas[1])[0]["status"] == "confirmado"
        assert len(queries.buscar_reservas_cliente("11988887777")) == 1


class TestFalhasDeTool:
    """Falhas viram {"erro": ...} devolvido ao modelo, sem derrubar o chat."""

    def test_argumentos_faltando(self, montar):
        ia, fake = montar([("tool", "criar_reserva", '{"mesa_id": 1}'), ("text", "ok")])
        assert ia.gerar_resposta(HISTORICO) == "ok"
        erro = _resultados_de_tools(fake.chamadas[1])[0]["erro"]
        assert "criar_reserva" in erro

    def test_json_de_argumentos_truncado(self, montar):
        ia, fake = montar(
            [("tool", "buscar_mesas_livres", '{"data": "2026-01'), ("text", "ok")]
        )
        assert ia.gerar_resposta(HISTORICO) == "ok"
        assert "JSON inválido" in _resultados_de_tools(fake.chamadas[1])[0]["erro"]

    def test_funcao_inexistente(self, montar):
        ia, fake = montar([("tool", "inventada", "{}"), ("text", "ok")])
        assert ia.gerar_resposta(HISTORICO) == "ok"
        assert "não encontrada" in _resultados_de_tools(fake.chamadas[1])[0]["erro"]

    def test_erro_inesperado_na_tool_nao_vaza_detalhes(self, montar, monkeypatch):
        def quebra(**kw):
            raise RuntimeError("segredo interno do banco")

        monkeypatch.setattr(openai_service.queries, "listar_mesas", quebra)
        ia, fake = montar([("tool", "listar_mesas", "{}"), ("text", "ok")])
        assert ia.gerar_resposta(HISTORICO) == "ok"
        erro = _resultados_de_tools(fake.chamadas[1])[0]["erro"]
        assert "Falha interna" in erro
        assert "segredo" not in erro

    def test_erro_de_validacao_da_query_chega_ao_modelo(self, montar):
        """Ex.: o modelo manda um horário inválido; a query responde com erro e o modelo corrige."""
        args = json.dumps({"data": "2026-01-15", "horario": "19h", "numero_pessoas": 2})
        ia, fake = montar([("tool", "buscar_mesas_livres", args), ("text", "ok")])
        ia.gerar_resposta(HISTORICO)
        resultado = _resultados_de_tools(fake.chamadas[1])[0]
        assert resultado["status"] == "erro"


class TestSystemPrompt:
    def test_data_atual_usa_fuso_de_sao_paulo(self, montar, monkeypatch):
        """2026-11-14 00:30 UTC é 2026-11-13 21:30 em São Paulo (sexta-feira)."""

        class RelogioFalso(datetime):
            @classmethod
            def now(cls, tz=None):
                base = datetime(2026, 11, 14, 0, 30, tzinfo=timezone.utc)
                return base.astimezone(tz) if tz else base.replace(tzinfo=None)

        monkeypatch.setattr(openai_service, "datetime", RelogioFalso)
        ia, _ = montar([("text", "x")])
        assert "2026-11-13 (sexta-feira)" in ia._montar_system_prompt()

    def test_prompt_tem_regras_de_funcionamento_e_dia_da_reserva(self, montar):
        ia, _ = montar([("text", "x")])
        prompt = ia._montar_system_prompt()
        assert "terça a domingo" in prompt
        assert "dia anterior" in prompt
