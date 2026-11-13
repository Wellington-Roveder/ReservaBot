import pytest

from services.nlu_config.nlu import SemanticNLU

THRESHOLD = 0.65


# scope="module": o modelo de embeddings é carregado uma vez, não a cada teste.
@pytest.fixture(scope="module")
def nlu():
    return SemanticNLU(threshold=THRESHOLD)


class TestSemanticNLU:
    @pytest.mark.parametrize(
        "texto", ["olá", "oi", "bom dia", "boa tarde", "boa noite"]
    )
    def test_classify_greeting(self, nlu, texto):
        result = nlu.classificar(texto)
        assert result["intent"] == "saudacao"
        assert result["in_scope"] is True
        assert result["confidence"] >= THRESHOLD

    @pytest.mark.parametrize(
        "texto",
        [
            "quero reservar uma mesa",
            "gostaria de fazer uma reserva para hoje à noite",
            "tem mesa pra 4 pessoas amanhã?",
            "preciso de uma mesa para sábado",
        ],
    )
    def test_classify_booking_request(self, nlu, texto):
        result = nlu.classificar(texto)
        assert result["intent"] == "fazer_reserva"
        assert result["in_scope"] is True

    @pytest.mark.parametrize(
        "texto",
        [
            "quero cancelar minha reserva",
            "preciso cancelar a reserva de amanhã",
            "quero alterar o horário da minha reserva",
        ],
    )
    def test_classify_cancel_request(self, nlu, texto):
        result = nlu.classificar(texto)
        assert result["intent"] == "alterar_cancelar_reserva"
        assert result["in_scope"] is True

    @pytest.mark.parametrize(
        "texto",
        [
            "quero ver minha reserva",
            "qual o horário da minha reserva?",
            "consultar minhas reservas",
        ],
    )
    def test_classify_query_reservation(self, nlu, texto):
        result = nlu.classificar(texto)
        assert result["intent"] == "consultar_reserva"
        assert result["in_scope"] is True

    @pytest.mark.parametrize(
        "texto",
        [
            "qual o tempo em são paulo?",
            "quem ganhou o jogo ontem?",
            "me conta uma piada",
            "qual a capital da França?",
        ],
    )
    def test_classify_out_of_scope(self, nlu, texto):
        result = nlu.classificar(texto)
        assert result["intent"] == "fora_de_escopo"
        assert result["in_scope"] is False
        # Sem assert de confiança: com a rota fora_de_escopo, a confiança alta
        # significa "tenho certeza que é fora do assunto".

    @pytest.mark.parametrize("texto", ["", "   ", "\n\t"])
    def test_classify_empty_or_blank(self, nlu, texto):
        result = nlu.classificar(texto)
        assert result["intent"] == "fora_de_escopo"
        assert result["in_scope"] is False

    def test_threshold_acima_da_confianca_rejeita_frase_valida(self, nlu):
        """Subir o threshold acima da confiança obtida deve tornar a frase fora de escopo."""
        frase = "quero reservar uma mesa"
        confianca = nlu.classificar(frase)["confidence"]
        rigido = SemanticNLU(threshold=confianca + 0.01)
        result = rigido.classificar(frase)
        assert result["intent"] == "fora_de_escopo"
        assert result["in_scope"] is False
