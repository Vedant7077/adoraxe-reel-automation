"""
Database configuration – SQLAlchemy 2 async-compatible session factory.
Connects to the EXISTING PostgreSQL Docker service (shortly-postgres).
"""
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.core.config import settings


raw_url = settings.database_url
if raw_url.startswith("postgres://"):
    db_url = raw_url.replace("postgres://", "postgresql+psycopg2://", 1)
elif raw_url.startswith("postgresql://") and not raw_url.startswith("postgresql+"):
    db_url = raw_url.replace("postgresql://", "postgresql+psycopg2://", 1)
else:
    db_url = raw_url

engine = create_engine(
    db_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=settings.debug,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency – yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_db_connection() -> bool:
    """Health check: returns True if database is reachable."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
