from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker
from trading_system.store.models import Base


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def make_engine(database_url: str) -> Engine:
    return create_engine(database_url, echo=False)


def make_session_factory(engine: Engine):
    return sessionmaker(engine, expire_on_commit=False)
