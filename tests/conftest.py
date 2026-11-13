import importlib
import os
import sqlite3
import sys
from datetime import datetime

import pytest

# Raiz do projeto = pasta acima de tests/. Garante imports de `services`, `repository`, etc.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# "Agora" fixo usado nos testes de queries (quinta-feira, 12:00, horário do restaurante).
# Os testes mudam o relógio com `queries._agora = lambda: datetime(...)`.
AGORA_PADRAO = datetime(2026, 1, 1, 12, 0)


@pytest.fixture
def queries(tmp_path):
    """Módulo `repository.queries` apontando para um banco SQLite temporário e novo.

    Cada teste recebe um banco limpo (ids reiniciam em 1, sem estado vazando
    entre testes) e um relógio fixo (`_agora`). Ao final, restaura o `db` original
    e recarrega `queries` (o que também restaura o relógio real).
    """
    import repository.database as database
    import repository.queries as queries_mod

    db_path = str(tmp_path / "test_reservas.db")
    sqlite3.connect(db_path).close()  # garante que o arquivo existe

    original_db = database.db
    pool = database.SqlitePool(db_path)
    database.db = pool
    importlib.reload(queries_mod)  # `queries` passa a usar o pool temporário
    queries_mod._agora = lambda: AGORA_PADRAO

    # Ordem importa: reservas tem FK para mesas.
    queries_mod.criar_tabela_mesas()
    queries_mod.criar_tabela_reservas()
    queries_mod.popular_mesas()

    try:
        yield queries_mod
    finally:
        # SqlitePool guarda a conexão em thread-local (pool._local.conn)
        conn = getattr(pool._local, "conn", None)
        if conn is not None:
            conn.close()
        database.db = original_db
        importlib.reload(queries_mod)
