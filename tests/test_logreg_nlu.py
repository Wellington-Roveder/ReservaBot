from unittest.mock import Mock

import numpy as np
import pytest

from benchmarks.nlu_experiment.treinar_comparar import prever_com_threshold


@pytest.fixture
def classificador_mock():
    """Classificador isolado com classes em ordem alfabética (padrão scikit-learn)."""
    clf = Mock()

    clf.classes_ = np.array(
        [
            "cancelar_reserva",  # índice 0
            "consultar_reserva",  # índice 1
            "fazer_reserva",  # índice 2
            "fora_de_escopo",  # índice 3
            "horario_funcionamento",  # índice 4
        ]
    )

    return clf


@pytest.fixture
def vetor_teste():
    """Embedding fictício para testar a lógica de decisão."""
    return np.array([[0.1, 0.2]])


class TestPreverComThreshold:
    def test_aceita_intencao_acima_do_threshold(self, classificador_mock, vetor_teste):
        # índice 2 ('fazer_reserva') = 0.70
        classificador_mock.predict_proba.return_value = np.array(
            [[0.05, 0.10, 0.70, 0.10, 0.05]]
        )

        resultado = prever_com_threshold(classificador_mock, vetor_teste, 0.50)

        assert resultado == ["fazer_reserva"]

    def test_rejeita_intencao_abaixo_do_threshold(
        self, classificador_mock, vetor_teste
    ):
        # Maior válida: índice 2 ('fazer_reserva') = 0.30 (< threshold 0.50)
        classificador_mock.predict_proba.return_value = np.array(
            [[0.15, 0.20, 0.30, 0.10, 0.25]]
        )

        resultado = prever_com_threshold(classificador_mock, vetor_teste, 0.50)

        assert resultado == ["fora_de_escopo"]

    def test_rejeita_quando_fora_de_escopo_tem_maior_probabilidade(
        self, classificador_mock, vetor_teste
    ):
        # índice 3 ('fora_de_escopo') = 0.45 vence índice 1 ('consultar_reserva') = 0.20
        classificador_mock.predict_proba.return_value = np.array(
            [[0.10, 0.20, 0.15, 0.45, 0.10]]
        )

        resultado = prever_com_threshold(classificador_mock, vetor_teste, 0.40)

        assert resultado == ["fora_de_escopo"]

    def test_aceita_intencao_em_empate_com_fora_de_escopo(
        self, classificador_mock, vetor_teste
    ):
        # Empate de 0.40 entre índice 0 ('cancelar_reserva') e índice 3 ('fora_de_escopo')
        classificador_mock.predict_proba.return_value = np.array(
            [[0.40, 0.10, 0.05, 0.40, 0.05]]
        )

        resultado = prever_com_threshold(classificador_mock, vetor_teste, 0.40)

        assert resultado == ["cancelar_reserva"]

    def test_classifica_multiplas_mensagens(self, classificador_mock):
        vetores = np.array(
            [
                [0.1, 0.2],
                [0.2, 0.3],
                [0.3, 0.4],
            ]
        )

        classificador_mock.predict_proba.return_value = np.array(
            [
                [0.05, 0.10, 0.70, 0.10, 0.05],  # índice 2: fazer_reserva (0.70)
                [0.10, 0.15, 0.10, 0.60, 0.05],  # índice 3: fora_de_escopo (0.60)
                [0.05, 0.70, 0.10, 0.10, 0.05],  # índice 1: consultar_reserva (0.70)
            ]
        )

        resultado = prever_com_threshold(classificador_mock, vetores, 0.50)

        assert resultado == [
            "fazer_reserva",
            "fora_de_escopo",
            "consultar_reserva",
        ]
