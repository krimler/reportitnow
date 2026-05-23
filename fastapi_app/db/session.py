"""SQLAlchemy engine + session factory + connect-time pragmas."""
from __future__ import annotations

from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, Session

from fastapi_app.config import get_settings


def _make_engine() -> Engine:
    settings = get_settings()
    url = settings.database_url
    if url.startswith("sqlite:///"):
        path = url.replace("sqlite:///", "", 1)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        url,
        future=True,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        if not url.startswith("sqlite"):
            return
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON")
        cur.execute("PRAGMA journal_mode = WAL")
        cur.close()

    return engine


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = _make_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, future=True)
    return _SessionLocal


def init_db() -> None:
    """Create tables from schema.sql if absent. Idempotent."""
    engine = get_engine()
    schema_path = Path(__file__).parent / "schema.sql"
    sql = schema_path.read_text()
    with engine.begin() as conn:
        for stmt in sql.split(";\n"):
            stmt = stmt.strip()
            if stmt:
                conn.exec_driver_sql(stmt)


def get_db():
    """FastAPI dependency: yields a session and ensures close."""
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()


def reset_engine_for_tests() -> None:
    """Tests can use this to swap DATABASE_URL and start clean."""
    global _engine, _SessionLocal
    _engine = None
    _SessionLocal = None
