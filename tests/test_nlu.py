import importlib.util
import os

import pytest

# Dynamically load SemanticNLU from the file (avoids package import issues with double .py extension)
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_nlu_path = os.path.join(_project_root, "services", "nlu_config", "nlu.py.py")
_spec = importlib.util.spec_from_file_location("nlu_mod", _nlu_path)
_nlu_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_nlu_mod)
SemanticNLU = _nlu_mod.SemanticNLU


# Fixture to provide a SemanticNLU instance (model loaded)
@pytest.fixture
def nlu():
    # threshold padrão usado na classe
    return SemanticNLU(threshold=0.65)


class TestSemanticNLU:
    def test_classify_greeting(self, nlu):
        """Saudações devem ser classificadas como 'saudacao'."""
        result = nlu.classificar("olá")
        assert result["intent"] == "saudacao"
        assert result["in_scope"] is True

    def test_classify_booking_request(self, nlu):
        """Pedidos de reserva devem ser classificados como 'fazer_reserva'."""
        result = nlu.classificar("quero reservar uma mesa")
        assert result["intent"] == "fazer_reserva"
        assert result["in_scope"] is True

    def test_classify_cancel_request(self, nlu):
        """Pedidos de cancelamento devem ser 'alterar_cancelar_reserva'."""
        result = nlu.classificar("quer cancelar minha reserva")
        assert result["intent"] == "alterar_cancelar_reserva"
        assert result["in_scope"] is True

    def test_classify_query_reservation(self, nlu):
        """Consultas de reserva devem ser 'consultar_reserva'."""
        result = nlu.classificar("quero ver minha reserva")
        assert result["intent"] == "consultar_reserva"
        assert result["in_scope"] is True

    def test_classify_out_of_scope(self, nlu):
        """Frases fora do domínio devem retornar 'fora_de_escopo'."""
        result = nlu.classificar("qual o tempo em são paulo?")
        assert result["intent"] == "fora_de_escopo"
        assert result["in_scope"] is False
        assert result["confidence"] < 0.65

    def test_classify_with_threshold(self, nlu):
        """Classificação acima do threshold deve ser aceita."""
        result = nlu.classificar("bom dia")
        # 'saudacao' should be above threshold
        assert result["confidence"] >= 0.65
        assert result["intent"] == "saudacao"

    def test_classify_empty_string(self, nlu):
        """Texto vazio deve retornar fora de escopo."""
        result = nlu.classificar("")
        assert result["intent"] == "fora_de_escopo"
        assert result["in_scope"] is False
