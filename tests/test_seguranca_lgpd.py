import sqlite3
from contextlib import contextmanager
from datetime import timedelta

import pytest

from app import mascarar_nome, montar_tabela_reservas
from repository import queries


@pytest.fixture
def banco_em_memoria(monkeypatch):
    """Cria um banco SQLite em memória isolado para testar as queries sem tocar no arquivo real."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    class FakeDB:
        @contextmanager
        def cursor(self):
            cur = conn.cursor()
            try:
                yield cur
                conn.commit()
            finally:
                cur.close()

    monkeypatch.setattr(queries, "db", FakeDB())

    with queries.db.cursor() as cur:
        cur.execute(
            "CREATE TABLE mesas (id INTEGER PRIMARY KEY AUTOINCREMENT, numero INTEGER)"
        )
        cur.execute("INSERT INTO mesas (numero) VALUES (1)")
    queries.criar_tabela_reservas()

    yield conn
    conn.close()


# ---------- Testes de Minimização na UI (app.py) ----------


@pytest.mark.parametrize(
    "entrada, esperado",
    [
        ("João Silva Sauro", "João S."),
        ("maria oliveira", "Maria O."),
        ("Carlos", "Carlos"),
        ("  ana   paula  ", "Ana P."),
        ("", "Visitante"),
        (None, "Visitante"),
    ],
)
def test_mascarar_nome(entrada, esperado):
    assert mascarar_nome(entrada) == esperado


def test_montar_tabela_reservas_oculta_pii():
    reservas_brutas = [
        {
            "id": 1,
            "mesa_id": 2,
            "nome_cliente": "Fernanda Montenegro",
            "telefone": "11987654321",
            "data": "2026-11-20",
            "horario": "20:00",
            "numero_pessoas": 4,
        }
    ]
    df = montar_tabela_reservas(reservas_brutas)

    assert df.loc[0, "Cliente"] == "Fernanda M."
    assert df.loc[0, "Telefone"] == "***4321"
    assert df.loc[0, "Data"] == "20/11"
    assert "Montenegro" not in df.to_string()
    assert "1198765" not in df.to_string()


# ---------- Testes de Retenção de 24h no Banco (queries.py) ----------


def test_limpar_reservas_expiradas_remove_apenas_antigas(banco_em_memoria):
    agora = queries._agora()
    antiga_25h = (agora - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    recente_2h = (agora - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")

    with queries.db.cursor() as cur:
        cur.execute(
            """INSERT INTO reservas (mesa_id, nome_cliente, telefone, data, horario, numero_pessoas, criado_em)
               VALUES (1, 'Cliente Antigo', '11911111111', '2026-11-20', '19:00', 2, ?)""",
            (antiga_25h,),
        )
        cur.execute(
            """INSERT INTO reservas (mesa_id, nome_cliente, telefone, data, horario, numero_pessoas, criado_em)
               VALUES (1, 'Cliente Recente', '11922222222', '2026-11-20', '21:00', 4, ?)""",
            (recente_2h,),
        )

    removidas = queries.limpar_reservas_expiradas()
    restantes = queries.listar_todas_reservas()

    assert removidas == 1
    assert len(restantes) == 1
    assert restantes[0]["nome_cliente"] == "Cliente Recente"
