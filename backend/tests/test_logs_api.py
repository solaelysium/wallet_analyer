from datetime import timedelta

from app.models import AppLog, utcnow


def test_logs_are_paginated_newest_first_and_redacted(app_client):
    client, database = app_client
    now = utcnow()
    with database.session() as session:
        session.add_all(
            [
                AppLog(
                    level="INFO",
                    event="collection.started",
                    message="Collection started",
                    job_id=7,
                    context={"provider": "etherscan"},
                    created_at=now - timedelta(minutes=1),
                ),
                AppLog(
                    level="ERROR",
                    event="provider.request_failed",
                    message="Provider request failed",
                    job_id=7,
                    job_item_id=11,
                    cluster_run_id=3,
                    context={
                        "api_key": "must-not-leak",
                        "request": {"authorization": "Bearer must-not-leak"},
                        "attempt": 2,
                    },
                    created_at=now,
                ),
            ]
        )

    response = client.get("/api/logs", params={"page": 1, "size": 1})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["page"] == 1
    assert payload["size"] == 1
    assert payload["items"][0]["event"] == "provider.request_failed"
    assert payload["items"][0]["context"] == {
        "api_key": "[REDACTED]",
        "request": {"authorization": "[REDACTED]"},
        "attempt": 2,
    }
    assert "must-not-leak" not in response.text


def test_logs_support_level_event_search_and_relation_filters(app_client):
    client, database = app_client
    with database.session() as session:
        session.add_all(
            [
                AppLog(
                    level="ERROR",
                    event="provider.rate_limit",
                    message="Quota exceeded",
                    job_id=9,
                    cluster_run_id=4,
                    context={"provider": "etherscan"},
                ),
                AppLog(
                    level="INFO",
                    event="collection.completed",
                    message="Collection finished",
                    job_id=10,
                    cluster_run_id=5,
                    context={"provider": "infura"},
                ),
            ]
        )

    response = client.get(
        "/api/logs",
        params={
            "level": "error",
            "event": "rate",
            "search": "etherscan",
            "job_id": 9,
            "cluster_run_id": 4,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["event"] == "provider.rate_limit"


def test_logs_validate_pagination_and_relation_filters(app_client):
    client, _ = app_client

    assert client.get("/api/logs", params={"page": 0}).status_code == 422
    assert client.get("/api/logs", params={"size": 201}).status_code == 422
    assert client.get("/api/logs", params={"job_id": 0}).status_code == 422
