import sqlite3
import threading
from contextlib import contextmanager
import os
from dotenv import load_dotenv


load_dotenv()


class SqlitePool:
    def __init__(self, db_path: str):
        self.db_path = db_path
        # armazena uma com isolada por cada thread
        self._local = threading.local()

    def _get_connection(self) -> sqlite3.Connection:
        # se a thread atual nao tem conexao, crie e configure uma
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, timeout=10)
            conn.row_factory = sqlite3.Row

            # otimizacao essenciais para concorrencia
            conn.execute("Pragma journal_mode=WAL;")
            conn.execute("Pragma synchronous=Normal;")

            # Prevenção de travamentos e otimização de memória
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute("PRAGMA cache_size=-20000;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA foreign_keys = ON;")

            self._local.conn = conn

        return self._local.conn

    @contextmanager
    def cursor(self):
        conn = self._get_connection()
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()


db_name = os.getenv("DATABASE_NAME")
db = SqlitePool(db_name)
