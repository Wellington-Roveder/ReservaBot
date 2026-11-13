import os
import tempfile

import pytest

# --- Setup temporary database for queries tests ---
tmpdir = tempfile.mkdtemp()
tmpdb = os.path.join(tmpdir, "test_reservas.db")

# Ensure the file exists
import sqlite3

sqlite3.connect(tmpdb).close()

# Patch the module-level db before importing queries
import repository.database

repository.database.db = repository.database.SqlitePool(tmpdb)

# Reload queries so it uses the patched db
import importlib

import repository.queries

importlib.reload(repository.queries)

# Re-export convenience functions after reload
from repository.queries import (
    buscar_mesas_livres,
    buscar_reservas_cliente,
    cancelar_reserva,
    criar_reserva,
    criar_tabela_mesas,
    listar_mesas,
    popular_mesas,
)


@pytest.fixture(autouse=True)
def reset_db():
    """Each test: create tables + seed data, then clean after."""
    criar_tabela_mesas()
    popular_mesas()
    yield
    # Clean: remove all rows to isolate tests
    with repository.database.db.cursor() as cur:
        cur.execute("DELETE FROM reservas")
        cur.execute("DELETE FROM mesas")
        repository.database.db.conn.commit()


class TestQueries:
    def test_listar_mesas_returns_13_seeded(self):
        mesas = listar_mesas()
        assert isinstance(mesas, list)
        assert len(mesas) == 13  # 5+5+3 from seed

    def test_buscar_mesas_livres_returns_list(self):
        resultado = buscar_mesas_livres("2026-01-01", "19:00", 2)
        assert isinstance(resultado, list)

    def test_criar_reserva_valida_retorna_confirmado(self):
        reserva = criar_reserva(
            mesa_id=1,
            nome_cliente="Joao Silva",
            telefone="11988887777",
            data="2026-01-15",
            horario="19:00",
            numero_pessoas=2,
        )
        assert reserva["status"] == "confirmado"
        assert "reserva_id" in reserva

    def test_criar_reserva_excede_capacidade_retorna_erro(self):
        reserva = criar_reserva(
            mesa_id=5,
            nome_cliente="Joao",
            telefone="11999998888",
            data="2026-01-15",
            horario="19:00",
            numero_pessoas=6,
        )
        assert reserva["status"] == "erro"
        assert "comporta no máximo" in reserva["mensagem"]

    def test_buscar_reservas_cliente_encontra_reserva(self):
        # First create a reservation
        criar_reserva(
            mesa_id=1,
            nome_cliente="Maria",
            telefone="1188887777",
            data="2026-01-15",
            horario="20:00",
            numero_pessoas=4,
        )
        reservas = buscar_reservas_cliente("1188887777")
        assert isinstance(reservas, list)
        assert len(reservas) >= 1
        assert reservas[0]["nome_cliente"] == "Maria"

    def test_cancelar_reserva_com_sucesso(self):
        create_res = criar_reserva(
            mesa_id=1,
            nome_cliente="Pedro",
            telefone="11777777777",
            data="2026-01-16",
            horario="19:30",
            numero_pessoas=2,
        )
        assert create_res["status"] == "confirmado"
        reserva_id = create_res["reserva_id"]
        result = cancelar_reserva(reserva_id=reserva_id, telefone="11777777777")
        assert result["status"] == "cancelado"

    def test_cancelar_reserva_com_telefone_errado_retorna_erro(self):
        criar_reserva(
            mesa_id=1,
            nome_cliente="Joao",
            telefone="11988887777",
            data="2026-01-15",
            horario="19:00",
            numero_pessoas=2,
        )
        result = cancelar_reserva(reserva_id=1, telefone="11999998888")
        assert result["status"] == "erro"
        assert "não confere" in result["mensagem"]
