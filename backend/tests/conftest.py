"""Shared test fixtures — in-memory-backed SQLite file, no MQTT broker required."""
import os

os.environ["DATABASE_URL"] = "sqlite:///./test_aeris.db"

# Fresh database per pytest session (avoids duplicate-seed failures across runs).
if os.path.exists("test_aeris.db"):
    os.remove("test_aeris.db")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database.session import Base, engine, SessionLocal  # noqa: E402
from app.database.seed import init_db  # noqa: E402
from app.main import app  # lifespan starts MQTT loop; safe without broker  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def database():
    Base.metadata.create_all(bind=engine)
    init_db()
    yield
    engine.dispose()


@pytest.fixture(scope="session")
def client(database):
    # Session-scoped: the app lifespan (MQTT loop + health monitor) starts once.
    # With no broker present, wait_for_connection times out quickly and the
    # subscriber keeps retrying harmlessly in a daemon thread.
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def auth_headers(client):
    res = client.post(
        "/api/auth/login",
        json={"email": "admin@aeris.io", "password": "admin123"},
    )
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session")
def safety_officer_headers(client):
    res = client.post(
        "/api/auth/login",
        json={"email": "safety@aeris.io", "password": "safety123"},
    )
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
