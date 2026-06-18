from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker
from trading_system.store.models import Base


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def make_engine(database_url: str) -> Engine:
    # Execution and sizing now run via asyncio.to_thread, so DB sessions may be
    # created on worker threads. SQLite's default thread-affinity check rejects
    # that; disable it (each session still uses its own connection per call).
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, echo=False, connect_args=connect_args)


def make_session_factory(engine: Engine):
    return sessionmaker(engine, expire_on_commit=False)
