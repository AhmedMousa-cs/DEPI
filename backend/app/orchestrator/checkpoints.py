"""Checkpointers. The PostgreSQL one uses its own connection, never the SQLAlchemy session."""
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.engine import make_url

from app.config import settings


def to_conninfo(sqlalchemy_url: str) -> str:
    """postgresql+psycopg://... -> postgresql://... (what psycopg / PostgresSaver expect)."""
    return make_url(sqlalchemy_url).set(drivername="postgresql").render_as_string(hide_password=False)


def memory_checkpointer():
    try:
        from langgraph.checkpoint.memory import InMemorySaver as Saver
    except ImportError:  # older langgraph
        from langgraph.checkpoint.memory import MemorySaver as Saver
    return Saver()


@contextmanager
def postgres_checkpointer(database_url: str | None = None, *, setup: bool = True) -> Iterator:
    from langgraph.checkpoint.postgres import PostgresSaver

    conninfo = to_conninfo(database_url or settings.database_url)
    with PostgresSaver.from_conn_string(conninfo) as saver:
        if setup:
            saver.setup()  # idempotent: creates the checkpoint tables if missing
        yield saver
