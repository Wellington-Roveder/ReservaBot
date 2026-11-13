from datetime import datetime

import pytest

# Seed: ids 1-5 capacidade 2 | ids 6-10 capacidade 4 | ids 11-13 capacidade 6
# Calendário: 2026-01-12 segunda (fechado) | 13 terça | 15 quinta | 16 sexta | 18 domingo
# Relógio da fixture `queries`: 2026-01-01 12:00 (ver conftest.AGORA_PADRAO).
DATA = "2026-01-15"
SEGUNDA = "2026-01-12"
TERCA = "2026-01-13"
TEL = "11988887777"


def _reservar(
    queries, mesa_id=1, horario="19:00", data=DATA, pessoas=2, nome="Joao", telefone=TEL
):
    return queries.criar_reserva(
        mesa_id=mesa_id,
        nome_cliente=nome,
        telefone=telefone,
        data=data,
        horario=horario,
        numero_pessoas=pessoas,
    )


def _ids(mesas):
    return [m["id"] for m in mesas]


def _relogio(queries, *args):
    queries._agora = lambda: datetime(*args)


class TestListarMesas:
    def test_listar_mesas_returns_13_seeded(self, queries):
        mesas = queries.listar_mesas()
        assert isinstance(mesas, list)
        assert len(mesas) == 13  # seed: 5 + 5 + 3

    def test_seed_capacidades(self, queries):
        capacidades = [m["capacidade"] for m in queries.listar_mesas()]
        assert capacidades == [2] * 5 + [4] * 5 + [6] * 3

    def test_popular_mesas_e_idempotente(self, queries):
        queries.popular_mesas()
        assert len(queries.listar_mesas()) == 13


class TestBuscarMesasLivres:
    def test_sem_reservas_retorna_todas_que_comportam(self, queries):
        assert len(queries.buscar_mesas_livres(DATA, "19:00", 2)) == 13

    def test_filtra_por_capacidade(self, queries):
        assert len(queries.buscar_mesas_livres(DATA, "19:00", 3)) == 8  # 5x4 + 3x6
        assert _ids(queries.buscar_mesas_livres(DATA, "19:00", 6)) == [11, 12, 13]

    def test_ordena_pela_menor_capacidade(self, queries):
        capacidades = [
            m["capacidade"] for m in queries.buscar_mesas_livres(DATA, "19:00", 2)
        ]
        assert capacidades == sorted(capacidades)

    def test_exclui_mesa_reservada_em_horario_proximo(self, queries):
        _reservar(queries, mesa_id=1, horario="19:00")
        livres = _ids(queries.buscar_mesas_livres(DATA, "19:30", 2))
        assert 1 not in livres
        assert len(livres) == 12

    def test_inclui_mesa_em_horario_distante(self, queries):
        _reservar(queries, mesa_id=1, horario="19:00")
        assert 1 in _ids(queries.buscar_mesas_livres(DATA, "22:00", 2))

    def test_inclui_mesa_em_outra_data(self, queries):
        _reservar(queries, mesa_id=1, horario="19:00")
        assert 1 in _ids(queries.buscar_mesas_livres("2026-01-16", "19:00", 2))

    def test_mesa_volta_a_ficar_livre_apos_cancelar(self, queries):
        res = _reservar(queries, mesa_id=1, horario="19:00")
        assert 1 not in _ids(queries.buscar_mesas_livres(DATA, "19:00", 2))
        queries.cancelar_reserva(res["reserva_id"], TEL)
        assert 1 in _ids(queries.buscar_mesas_livres(DATA, "19:00", 2))

    def test_mesa_indisponivel_nao_aparece(self, queries):
        with queries.db.cursor() as cur:
            cur.execute("UPDATE mesas SET status = 'ocupada' WHERE id = 1")
        assert 1 not in _ids(queries.buscar_mesas_livres(DATA, "19:00", 2))

    def test_madrugada_e_consistente_com_criar_reserva(self, queries):
        """23:00 bloqueia 00:30 do mesmo dia de expediente; 01:30 já está livre."""
        _reservar(queries, mesa_id=1, horario="23:00")
        assert 1 not in _ids(queries.buscar_mesas_livres(DATA, "00:30", 2))
        assert 1 in _ids(queries.buscar_mesas_livres(DATA, "01:30", 2))

    @pytest.mark.parametrize("pessoas", [0, -1])
    def test_numero_pessoas_invalido_retorna_erro(self, queries, pessoas):
        res = queries.buscar_mesas_livres(DATA, "19:00", pessoas)
        assert res["status"] == "erro"

    def test_slot_invalido_retorna_erro(self, queries):
        assert queries.buscar_mesas_livres(SEGUNDA, "19:00", 2)["status"] == "erro"
        assert queries.buscar_mesas_livres(DATA, "19h", 2)["status"] == "erro"
        assert queries.buscar_mesas_livres("2025-12-31", "19:00", 2)["status"] == "erro"


class TestCriarReserva:
    def test_criar_reserva_valida_retorna_confirmado(self, queries):
        reserva = _reservar(queries, nome="Joao Silva")
        assert reserva["status"] == "confirmado"
        assert "reserva_id" in reserva
        assert reserva["mesa_id"] == 1
        assert reserva["numero_pessoas"] == 2

    def test_criar_reserva_excede_capacidade_retorna_erro(self, queries):
        reserva = _reservar(queries, mesa_id=5, pessoas=6)
        assert reserva["status"] == "erro"
        assert "comporta no máximo" in reserva["mensagem"]

    def test_capacidade_exata_e_aceita_e_uma_a_mais_e_recusada(self, queries):
        assert _reservar(queries, mesa_id=6, pessoas=4)["status"] == "confirmado"
        recusada = _reservar(queries, mesa_id=7, pessoas=5)
        assert recusada["status"] == "erro"
        assert "comporta no máximo" in recusada["mensagem"]

    def test_mesa_inexistente_retorna_erro(self, queries):
        reserva = _reservar(queries, mesa_id=999)
        assert reserva["status"] == "erro"
        assert "não foi encontrada" in reserva["mensagem"]

    def test_mesa_indisponivel_retorna_erro(self, queries):
        with queries.db.cursor() as cur:
            cur.execute("UPDATE mesas SET status = 'ocupada' WHERE id = 1")
        reserva = _reservar(queries, mesa_id=1)
        assert reserva["status"] == "erro"
        assert "não está disponível" in reserva["mensagem"]

    def test_conflito_mesmo_horario_retorna_erro(self, queries):
        assert _reservar(queries, horario="19:00")["status"] == "confirmado"
        segunda = _reservar(
            queries, horario="19:00", nome="Outro", telefone="11911112222"
        )
        assert segunda["status"] == "erro"
        assert "já possui uma reserva" in segunda["mensagem"]

    def test_conflito_dentro_da_janela_de_2h_retorna_erro(self, queries):
        _reservar(queries, horario="19:00")
        assert _reservar(queries, horario="20:30")["status"] == "erro"

    def test_fora_da_janela_de_2h_e_aceito(self, queries):
        _reservar(queries, horario="21:00")
        assert _reservar(queries, horario="20:00")["status"] == "erro"  # 1h antes
        assert (
            _reservar(queries, horario="18:59")["status"] == "confirmado"
        )  # 2h01 antes
        assert (
            _reservar(queries, horario="23:01")["status"] == "confirmado"
        )  # 2h01 depois

    def test_mesma_mesa_em_outra_data_e_aceita(self, queries):
        _reservar(queries, horario="19:00", data="2026-01-15")
        assert (
            _reservar(queries, horario="19:00", data="2026-01-16")["status"]
            == "confirmado"
        )

    def test_mesmo_horario_em_outra_mesa_e_aceito(self, queries):
        _reservar(queries, mesa_id=1, horario="19:00")

        resultado = _reservar(
            queries,
            mesa_id=2,
            horario="19:00",
            telefone="11911112222",
        )

        assert resultado["status"] == "confirmado"

    def test_conflito_perto_da_meia_noite_retorna_erro(self, queries):
        _reservar(queries, horario="23:00")
        assert _reservar(queries, horario="23:30")["status"] == "erro"

    def test_madrugada_conta_no_mesmo_dia_de_expediente(self, queries):
        """00:30 do dia 15 é depois do 23:00 do dia 15 (1h30), não 22h30 antes."""
        _reservar(queries, horario="23:00")
        assert _reservar(queries, horario="00:30")["status"] == "erro"
        assert (
            _reservar(queries, horario="01:30")["status"] == "confirmado"
        )  # 2h30 depois


class TestValidacoes:
    @pytest.mark.parametrize("pessoas", [0, -1, "4", True, None])
    def test_numero_pessoas_invalido_retorna_erro(self, queries, pessoas):
        assert _reservar(queries, pessoas=pessoas)["status"] == "erro"
        assert queries.buscar_reservas_cliente(TEL) == []

    @pytest.mark.parametrize(
        "data, horario",
        [
            ("2026-1-15", "19:00"),
            ("15/01/2026", "19:00"),
            ("2026-01-15", "19h"),
            ("2026-01-15", "7:00"),
            ("2026-01-15", "25:99"),
            ("2026-01-15", "24:00"),
            ("2026-02-30", "19:00"),
            ("", ""),
            (None, "19:00"),
        ],
    )
    def test_data_ou_horario_invalidos_retornam_erro(self, queries, data, horario):
        res = _reservar(queries, data=data, horario=horario)
        assert res["status"] == "erro"
        assert "inválido" in res["mensagem"]
        assert queries.buscar_reservas_cliente(TEL) == []

    def test_horario_invalido_nao_burla_conflito(self, queries):
        assert _reservar(queries, horario="19h")["status"] == "erro"
        assert _reservar(queries, horario="19h")["status"] == "erro"

    def test_data_no_passado_retorna_erro(self, queries):
        res = _reservar(queries, data="2025-12-31")
        assert res["status"] == "erro"
        assert "já passaram" in res["mensagem"]

    def test_horario_ja_passado_no_mesmo_dia_retorna_erro(self, queries):
        _relogio(queries, 2026, 1, 15, 20, 0)
        assert _reservar(queries, horario="19:00")["status"] == "erro"
        assert _reservar(queries, horario="21:00")["status"] == "confirmado"

    def test_segunda_feira_fechada(self, queries):
        res = _reservar(queries, data=SEGUNDA, horario="19:00")
        assert res["status"] == "erro"
        assert "Funcionamos" in res["mensagem"]

    @pytest.mark.parametrize("horario", ["17:59", "12:00", "10:00", "02:00", "03:30"])
    def test_fora_do_expediente_retorna_erro(self, queries, horario):
        res = _reservar(queries, horario=horario)
        assert res["status"] == "erro"
        assert "Funcionamos" in res["mensagem"]

    @pytest.mark.parametrize("horario", ["18:00", "23:59", "00:00", "01:59"])
    def test_dentro_do_expediente_e_aceito(self, queries, horario):
        assert _reservar(queries, horario=horario)["status"] == "confirmado"

    def test_madrugada_de_segunda_pertence_ao_expediente_de_segunda(self, queries):
        assert _reservar(queries, data=SEGUNDA, horario="00:30")["status"] == "erro"

    def test_madrugada_de_terca_pertence_ao_expediente_de_terca(self, queries):
        assert _reservar(queries, data=TERCA, horario="00:30")["status"] == "confirmado"

    @pytest.mark.parametrize("telefone", ["123", "abc", "", None, "119999"])
    def test_telefone_invalido_retorna_erro(self, queries, telefone):
        res = _reservar(queries, telefone=telefone)
        assert res["status"] == "erro"
        assert "Telefone inválido" in res["mensagem"]


class TestTelefone:
    def test_reserva_salva_telefone_normalizado(self, queries):
        res = _reservar(queries, telefone="(11) 98888-7777")
        assert res["status"] == "confirmado"
        assert res["telefone"] == "11988887777"
        assert len(queries.buscar_reservas_cliente("11988887777")) == 1

    @pytest.mark.parametrize(
        "busca",
        ["(11) 98888-7777", "+55 11 98888-7777", "11 98888 7777", "11988887777"],
    )
    def test_busca_aceita_qualquer_formato(self, queries, busca):
        _reservar(queries, telefone="11988887777")
        assert len(queries.buscar_reservas_cliente(busca)) == 1

    def test_cancelar_aceita_telefone_formatado(self, queries):
        res = _reservar(queries, telefone="11988887777")
        out = queries.cancelar_reserva(res["reserva_id"], "(11) 98888-7777")
        assert out["status"] == "cancelado"


class TestBuscarReservasCliente:
    def test_buscar_reservas_cliente_encontra_reserva(self, queries):
        _reservar(
            queries,
            mesa_id=6,
            nome="Maria",
            telefone="1188887777",
            horario="20:00",
            pessoas=4,
        )
        reservas = queries.buscar_reservas_cliente("1188887777")
        assert isinstance(reservas, list)
        assert len(reservas) == 1
        assert reservas[0]["nome_cliente"] == "Maria"

    def test_telefone_sem_reservas_retorna_lista_vazia(self, queries):
        assert queries.buscar_reservas_cliente("00000000000") == []

    def test_nao_retorna_reservas_de_outro_cliente(self, queries):
        _reservar(queries, mesa_id=1, telefone="11111111111", nome="A")
        _reservar(queries, mesa_id=2, telefone="22222222222", nome="B")
        reservas = queries.buscar_reservas_cliente("11111111111")
        assert [r["nome_cliente"] for r in reservas] == ["A"]

    def test_ordena_pelo_momento_real(self, queries):

        tel = "11999990000"

        _reservar(queries, mesa_id=1, data="2026-01-16", horario="19:00", telefone=tel)

        _reservar(queries, mesa_id=2, data="2026-01-15", horario="01:30", telefone=tel)

        _reservar(queries, mesa_id=3, data="2026-01-15", horario="23:00", telefone=tel)

        _reservar(queries, mesa_id=4, data="2026-01-15", horario="19:00", telefone=tel)

        reservas = queries.buscar_reservas_cliente(tel)

        assert [(r["data"], r["horario"]) for r in reservas] == [
            ("2026-01-15", "19:00"),
            ("2026-01-15", "23:00"),
            ("2026-01-15", "01:30"),
            ("2026-01-16", "19:00"),
        ]

    def test_reserva_cancelada_nao_aparece(self, queries):
        res = _reservar(queries, telefone="11777777777")
        queries.cancelar_reserva(res["reserva_id"], "11777777777")
        assert queries.buscar_reservas_cliente("11777777777") == []

    def test_reserva_em_andamento_aparece(self, queries):
        _reservar(queries, horario="19:00")
        _relogio(queries, 2026, 1, 15, 20, 0)  # 1h depois do início
        assert len(queries.buscar_reservas_cliente(TEL)) == 1

    def test_reserva_ja_terminada_nao_aparece(self, queries):
        _reservar(queries, horario="19:00")
        _relogio(queries, 2026, 1, 15, 22, 0)  # 3h depois do início
        assert queries.buscar_reservas_cliente(TEL) == []

    def test_reserva_de_dias_atras_nao_aparece(self, queries):
        _reservar(queries, horario="19:00")
        _relogio(queries, 2026, 1, 20, 12, 0)
        assert queries.buscar_reservas_cliente(TEL) == []


class TestCancelarReserva:
    def test_cancelar_reserva_com_sucesso(self, queries):
        res = _reservar(queries, nome="Pedro", telefone="11777777777", horario="19:30")
        assert res["status"] == "confirmado"
        result = queries.cancelar_reserva(res["reserva_id"], "11777777777")
        assert result["status"] == "cancelado"

    def test_cancelar_reserva_com_telefone_errado_retorna_erro(self, queries):
        res = _reservar(queries, telefone="11988887777")
        assert res["status"] == "confirmado"
        result = queries.cancelar_reserva(res["reserva_id"], "11999998888")
        assert result["status"] == "erro"
        assert "não confere" in result["mensagem"]

    def test_telefone_errado_nao_apaga_a_reserva(self, queries):
        res = _reservar(queries, telefone="11988887777")
        queries.cancelar_reserva(res["reserva_id"], "11999998888")
        assert len(queries.buscar_reservas_cliente("11988887777")) == 1

    def test_cancelar_duas_vezes_retorna_erro_na_segunda(self, queries):
        res = _reservar(queries, telefone="11988887777")
        assert (
            queries.cancelar_reserva(res["reserva_id"], "11988887777")["status"]
            == "cancelado"
        )
        segunda = queries.cancelar_reserva(res["reserva_id"], "11988887777")
        assert segunda["status"] == "erro"

    def test_cancelar_reserva_inexistente_retorna_erro(self, queries):
        assert queries.cancelar_reserva(999, "11988887777")["status"] == "erro"

    def test_cancelar_libera_o_horario_para_nova_reserva(self, queries):
        res = _reservar(queries, horario="19:00", telefone="11988887777")
        assert (
            _reservar(queries, horario="19:00", telefone="11911112222")["status"]
            == "erro"
        )
        queries.cancelar_reserva(res["reserva_id"], "11988887777")
        assert (
            _reservar(queries, horario="19:00", telefone="11911112222")["status"]
            == "confirmado"
        )
