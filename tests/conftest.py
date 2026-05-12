import pytest
from sqlalchemy import create_engine
from trading_system.store.db import init_db


@pytest.fixture(scope="session")
def in_memory_engine():
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    return engine
