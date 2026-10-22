import pytest
from django.test import Client


@pytest.mark.django_db
def test_health_reports_database_ok(client: Client) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_rejects_post(client: Client) -> None:
    response = client.post("/api/health")

    assert response.status_code == 405
