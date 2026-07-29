"""Smoke tests for application wiring."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _client() -> TestClient:
    settings = Settings(
        environment="development",
        edgar_contact_email="tests@example.com",
        cors_allowed_origins="http://localhost:3000",
    )
    return TestClient(create_app(settings))


def test_health_reports_ok() -> None:
    with _client() as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["edgar_configured"] is True


def test_health_reports_missing_edgar_contact() -> None:
    settings = Settings(environment="development", edgar_contact_email="")
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["edgar_configured"] is False
