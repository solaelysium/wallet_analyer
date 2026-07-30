from __future__ import annotations

import time

from sqlalchemy import select

from app.models import ApiKey, Wallet, WalletImport


ADDRESS_A = "0x0000000000000000000000000000000000000001"
ADDRESS_B = "0x0000000000000000000000000000000000000002"


def test_mixed_preview_and_atomic_confirm(app_client) -> None:
    client, database = app_client
    csv_data = (
        "index,wallet_address\n"
        f"alpha,{ADDRESS_A}\n"
        f"duplicate,{ADDRESS_A.upper().replace('0X', '0x')}\n"
    )
    response = client.post(
        "/api/imports/preview",
        files={"files": ("wallets.csv", csv_data, "text/csv")},
        data={"manual_text": f"{ADDRESS_B}\nnot-an-address\n"},
    )
    assert response.status_code == 200
    preview = response.json()
    assert preview["valid_count"] == 2
    assert preview["duplicate_count"] == 1
    assert preview["invalid_count"] == 1
    assert {issue["source"] for issue in preview["issues"]} == {
        "wallets.csv",
        "manual",
    }

    confirmed = client.post(
        "/api/imports/confirm",
        json={"token": preview["token"], "name": "Mixed import"},
    )
    assert confirmed.status_code == 201
    job_id = confirmed.json()["job"]["id"]
    deadline = time.monotonic() + 3
    state = "running"
    while time.monotonic() < deadline and state in {"queued", "running"}:
        state = client.get(f"/api/jobs/{job_id}").json()["state"]
        time.sleep(0.02)
    assert state == "completed"
    features = client.get(
        "/api/features",
        params={
            "sort_by": "native_balance",
            "sort_order": "desc",
            "filters": '{"native_balance":{"min":0.5}}',
        },
    )
    assert features.status_code == 200
    assert features.json()["total"] == 2
    exported = client.get("/api/features/export?file_format=csv")
    assert exported.status_code == 200
    assert "native_balance" in exported.text
    with database.session() as session:
        assert len(session.scalars(select(Wallet)).all()) == 2
        assert len(session.scalars(select(WalletImport)).all()) == 1


def test_preview_does_not_persist(app_client) -> None:
    client, database = app_client
    response = client.post(
        "/api/imports/preview",
        data={"manual_text": ADDRESS_A},
    )
    assert response.status_code == 200
    with database.session() as session:
        assert session.scalar(select(Wallet.id)) is None
        assert session.scalar(select(WalletImport.id)) is None


def test_tabular_import_rejects_extra_columns(app_client) -> None:
    client, _ = app_client
    response = client.post(
        "/api/imports/preview",
        files={
            "files": (
                "wallets.csv",
                f"index,wallet_address,label\n1,{ADDRESS_A},extra\n",
                "text/csv",
            )
        },
    )
    assert response.status_code == 422
    assert "нужны только столбцы" in response.json()["detail"]


def test_api_keys_are_plaintext_in_database_and_masked_in_api(app_client) -> None:
    client, database = app_client
    secret = "super-secret-provider-key"
    created = client.post(
        "/api/api-keys",
        json={"service": "etherscan", "label": "primary", "value": secret},
    )
    assert created.status_code == 201
    listed = client.get("/api/api-keys")
    assert listed.status_code == 200
    assert secret not in listed.text
    with database.session() as session:
        row = session.scalar(select(ApiKey))
        assert row is not None
        assert row.value == secret
    payload = listed.json()[0]
    assert payload["value"].startswith("supe")
    assert payload["value"].endswith("-key")
