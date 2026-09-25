"""Bounded Postgres pools and shared transactions for state and vectors."""
import atexit
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pools = {}
_guard = threading.Lock()
_current = ContextVar('desk_postgres_transaction', default=None)

def pool(url):
    with _guard:
        if url not in _pools:
            value = ConnectionPool(url, min_size=0, max_size=4, max_waiting=32,
                                   timeout=15, max_idle=60, open=True,
                                   kwargs={'row_factory':dict_row, 'connect_timeout':10,
                                           'prepare_threshold':None},
                                   check=ConnectionPool.check_connection)
            _pools[url] = value
            atexit.register(value.close)
        return _pools[url]

class Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key,int) else super().__getitem__(key)

class Cursor:
    def __init__(self, cursor): self.cursor = cursor
    def fetchone(self):
        row = self.cursor.fetchone()
        return Row(row) if row is not None else None
    def fetchall(self): return [Row(r) for r in self.cursor.fetchall()]
    def __iter__(self): return iter(self.fetchall())

class Connection:
    def __init__(self, raw): self.raw = raw
    def execute(self, sql, parameters=()):
        # Application queries use qmark bindings; values never enter SQL text.
        return Cursor(self.raw.execute(sql.replace('?', '%s'), parameters))
    def executescript(self, sql):
        for statement in sql.split(';'):
            if statement.strip(): self.raw.execute(statement)

@contextmanager
def transaction(url, write=False):
    active = _current.get()
    if active is not None and active[0] == url:
        if write and not active[2]:
            raise RuntimeError('Cannot promote a read transaction')
        yield active[1]
        return
    with pool(url).connection() as raw:
        with raw.transaction():
            raw.execute("SET LOCAL statement_timeout = '15s'")
            raw.execute("SET LOCAL lock_timeout = '10s'")
            if write:
                # Preserve existing short write-transaction invariants across
                # instances (approval, rate limits, idempotency, audit chain).
                # No model/embedding network calls may occur inside this lock.
                raw.execute('SELECT pg_advisory_xact_lock(72419031)')
            connection = Connection(raw)
            token = _current.set((url,connection,write))
            try: yield connection
            finally: _current.reset(token)
