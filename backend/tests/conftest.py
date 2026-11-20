"""
Pytest fixtures and test database setup.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.db.database import Base, get_db
import app.models.models  # Ensure all model tables are registered in Base.metadata
from app.main import app
from app.core.config import settings

# In-memory SQLite for testing
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def configure_test_settings(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "youtube_provider", "demo")
    monkeypatch.setattr(settings, "cloudinary_cloud_name", "")
    monkeypatch.setattr(settings, "cloudinary_api_key", "")
    monkeypatch.setattr(settings, "cloudinary_api_secret", "")
    monkeypatch.setattr("app.services.video.processor.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.db.database.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.services.instagram.publisher.SessionLocal", TestingSessionLocal)


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db_session():
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
