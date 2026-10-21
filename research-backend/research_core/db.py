from functools import lru_cache
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import settings


class Base(DeclarativeBase):
    pass


@lru_cache
def engine(role="backend"):
    url = settings().database_url_for(role)
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs.update(connect_args={"check_same_thread": False},
                      execution_options={"schema_translate_map": {"app": None, "knowledge": None}})
    return create_engine(url, **kwargs)


def session_factory(role="backend"):
    return sessionmaker(engine(role), expire_on_commit=False)


def get_db():
    with session_factory()() as db:
        yield db
