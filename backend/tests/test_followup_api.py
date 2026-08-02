from __future__ import annotations

import time

from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy import select

from app.config import Settings
from app.database import Database
from app.key_pool import KeyPool
from app.main import create_app
from app.models import ApiKey, Job, JobItem
from app.providers import ProviderBundle
from conftest import FakeExplorer, FakePrices, FakeRpc


ADDRESS = "0x0000000000000000000000000000000000000011"


def create_import(client) -> int:
    preview = client.post(
        "/api/imports/preview", data={"manual_text": ADDRESS}
    ).json()
    response = client.post(
        "/api/imports/confirm",
        json={"token": preview["token"], "name": "Follow-up import"},
    )
    assert response.status_code == 201
    return response.json()["job"]["id"]


def wait_for_job(client, job_id: int) -> str:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        state = client.get(f"/api/jobs/{job_id}").json()["state"]
        if state not in {"queued", "running", "cancelling"}:
            return state
        time.sleep(0.02)
    raise AssertionError("Job did not finish")


def test_import_history_wallet_filter_and_delete_cascade(app_client) -> None:
    client, _ = app_client
    job_id = create_import(client)
    assert wait_for_job(client, job_id) == "completed"
    history = client.get("/api/imports").json()
    assert history["total"] == 1
    assert history["items"][0]["source_summary"]["sources"] == ["manual"]
    import_id = history["items"][0]["id"]
    wallets = client.get(f"/api/wallets?import_id={import_id}&search=0011").json()
    assert wallets["total"] == 1
    deleted = client.delete(f"/api/wallets/{wallets['items'][0]['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["cascade"]["wallet_import_members"] == 1
    assert client.get("/api/wallets").json()["total"] == 0


def test_delete_import_history_retains_collected_wallet_data(app_client) -> None:
    client, _ = app_client
    job_id = create_import(client)
    assert wait_for_job(client, job_id) == "completed"
    import_id = client.get("/api/imports").json()["items"][0]["id"]
    assert client.get("/api/wallets").json()["total"] == 1
    assert client.get(f"/api/logs?job_id={job_id}").json()["total"] > 0

    response = client.delete(f"/api/imports/{import_id}")

    assert response.status_code == 200
    assert response.json()["cascade"]["jobs"] == 1
    assert client.get("/api/imports").json()["total"] == 0
    assert client.get("/api/jobs").json()["total"] == 0
    assert client.get(f"/api/logs?job_id={job_id}").json()["total"] == 0
    assert client.get("/api/wallets").json()["total"] == 1
    assert client.get("/api/features").json()["total"] == 1


def test_retry_requeues_failed_item_and_rejects_duplicate_start(app_client) -> None:
    client, database = app_client
    job_id = create_import(client)
    assert wait_for_job(client, job_id) == "completed"
    with database.session() as session:
        job = session.get(Job, job_id)
        item = session.scalar(select(JobItem).where(JobItem.job_id == job_id))
        job.state = "completed_with_errors"
        job.progress_done = 1
        item.state = "failed"
        item.error = "synthetic failure"
    response = client.post(f"/api/jobs/{job_id}/retry")
    assert response.status_code == 200
    duplicate = client.post(f"/api/jobs/{job_id}/resume")
    assert duplicate.status_code in {409, 422}
    assert wait_for_job(client, job_id) == "completed"
    detail = client.get(f"/api/jobs/{job_id}").json()
    # Initial collect+features, then retry runs features again.
    assert detail["items"][0]["attempts"] == 3
    assert detail["items"][0]["error"] is None


def test_recalculate_reprocesses_completed_wallets(app_client) -> None:
    client, _ = app_client
    job_id = create_import(client)
    assert wait_for_job(client, job_id) == "completed"

    response = client.post(f"/api/jobs/{job_id}/recalculate")

    assert response.status_code == 200
    assert wait_for_job(client, job_id) == "completed"
    detail = client.get(f"/api/jobs/{job_id}").json()
    # Two full waves: collect+features, then collect+features again.
    assert detail["items"][0]["attempts"] == 4
    assert client.get("/api/features").json()["items"][0]["version"] == (
            "wallet_features.v4"
    )


def test_bootstrap_keys_are_not_used_as_runtime_configuration(tmp_path) -> None:
    secret = "environment-provider-secret"
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'keys.db').as_posix()}",
        app_secret_key="encryption-secret",
        infura_api_keys=[secret],
        etherscan_api_keys=[],
        coingecko_api_keys=[],
    )
    database = Database(settings)
    for _ in range(2):
        providers = ProviderBundle(
            FakeExplorer(), FakeRpc(), FakePrices(), KeyPool({})
        )
        with TestClient(create_app(settings, database, providers)):
            assert providers.key_pool.health().get("infura", []) == []
    with database.session() as session:
        assert session.scalar(select(func.count(ApiKey.id))) == 0
