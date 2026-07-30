from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import select

from app.config import SecretBox, Settings
from app.database import Database
from app.models import ApiKey, Wallet


def test_database_creates_six_domain_files(tmp_path: Path) -> None:
    database = Database(Settings(data_dir=tmp_path))
    database.initialize()

    assert {path.name for path in tmp_path.glob("*.sqlite3")} == {
        "keys.sqlite3",
        "wallets.sqlite3",
        "events.sqlite3",
        "tokens.sqlite3",
        "analytics.sqlite3",
        "logs.sqlite3",
    }
    for name, path in database.paths.items():
        with sqlite3.connect(path) as connection:
            assert connection.execute(
                "SELECT version FROM schema_version"
            ).fetchone() == (2 if name == "tokens" else 1,)
    with sqlite3.connect(database.paths["wallets"]) as connection:
        referenced_tables = {
            row[2]
            for row in connection.execute(
                "PRAGMA foreign_key_list(wallet_import_members)"
            )
        }
        assert referenced_tables == {"wallets", "wallet_imports"}
    with sqlite3.connect(database.paths["events"]) as connection:
        assert connection.execute(
            "PRAGMA foreign_key_list(normal_transactions)"
        ).fetchall() == []
        assert connection.execute(
            "PRAGMA foreign_key_list(token_transfers)"
        ).fetchall() == []
    with sqlite3.connect(database.paths["tokens"]) as connection:
        assert "suspicious" in {
            row[1] for row in connection.execute("PRAGMA table_info(tokens)")
        }
        assert connection.execute(
            "PRAGMA foreign_key_list(tokens)"
        ).fetchall() == []
        assert {
            row[2]
            for row in connection.execute(
                "PRAGMA foreign_key_list(token_prices)"
            )
        } == {"tokens"}
    with sqlite3.connect(database.paths["analytics"]) as connection:
        assert connection.execute(
            "PRAGMA foreign_key_list(wallet_feature_snapshots)"
        ).fetchall() == []
        assert {
            row[2]
            for row in connection.execute(
                "PRAGMA foreign_key_list(cluster_assignments)"
            )
        } == {"cluster_runs"}
    with sqlite3.connect(database.paths["logs"]) as connection:
        assert connection.execute(
            "PRAGMA foreign_key_list(app_logs)"
        ).fetchall() == []


def test_key_crud_is_masked_upserted_and_live(app_client) -> None:
    client, _ = app_client
    secret = "key-value-that-must-stay-masked"
    created = client.post(
        "/api/api-keys",
        json={"service": "infura", "label": "primary", "value": secret},
    )
    assert created.status_code == 201
    key_id = created.json()["id"]
    assert secret not in created.text

    upserted = client.post(
        "/api/api-keys",
        json={"service": "infura", "label": "renamed", "value": secret},
    )
    assert upserted.status_code == 201
    assert upserted.json()["id"] == key_id
    assert len(client.get("/api/api-keys").json()) == 1
    assert len(client.app.state.providers.key_pool.health()["infura"]) == 1

    updated = client.patch(
        f"/api/api-keys/{key_id}",
        json={"label": "secondary", "enabled": False, "value": "replacement-value"},
    )
    assert updated.status_code == 200
    assert updated.json()["label"] == "secondary"
    assert updated.json()["enabled"] is False
    assert not any(
        key["enabled"]
        for key in client.app.state.providers.key_pool.health()["infura"]
    )

    assert client.delete(f"/api/api-keys/{key_id}").status_code == 204
    assert client.get("/api/api-keys").json() == []


def test_runtime_settings_persist_and_reconfigure(app_client) -> None:
    client, database = app_client
    response = client.patch(
        "/api/settings",
        json={
            "provider_timeout_seconds": 12.5,
            "provider_max_retries": 7,
            "provider_cooldown_seconds": 9,
            "infura_rps": 17,
            "key_concurrency": 3,
        },
    )
    assert response.status_code == 200
    assert response.json()["provider_timeout_seconds"] == 12.5
    assert response.json()["message"] == "Настройки применены."
    assert {
        service: health["status"]
        for service, health in response.json()["provider_health"].items()
    } == {
        "etherscan": "unavailable",
        "infura": "unavailable",
        "coingecko": "unavailable",
    }
    assert client.app.state.providers.key_pool.max_retries == 7
    assert client.app.state.providers.key_pool.cooldown_seconds == 9
    assert database.runtime_settings().key_concurrency == 3


def test_legacy_import_preserves_ids_and_decrypts_keys(tmp_path: Path) -> None:
    root = tmp_path / "project"
    data_dir = root / "data"
    data_dir.mkdir(parents=True)
    legacy_path = data_dir / "wallet_analyzer.sqlite3"
    secret = "legacy-secret"
    encrypted = SecretBox(secret).encrypt("legacy-provider-key")
    timestamp = "2026-01-01 00:00:00"
    with sqlite3.connect(legacy_path) as connection:
        connection.executescript(
            """
            CREATE TABLE chains (
                id INTEGER PRIMARY KEY, slug TEXT NOT NULL, name TEXT NOT NULL,
                chain_id INTEGER NOT NULL, native_symbol TEXT NOT NULL,
                enabled INTEGER NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE wallets (
                id INTEGER PRIMARY KEY, chain_id INTEGER NOT NULL,
                address TEXT NOT NULL, checksum_address TEXT, is_eoa INTEGER,
                last_collected_at TEXT, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE api_keys (
                id INTEGER PRIMARY KEY, service TEXT NOT NULL, label TEXT NOT NULL,
                encrypted_value TEXT NOT NULL, enabled INTEGER NOT NULL,
                last_used_at TEXT, error_count INTEGER NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO chains VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (9, "ethereum", "Ethereum", 1, "ETH", 1, timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO wallets VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                42,
                9,
                "0x0000000000000000000000000000000000000042",
                None,
                1,
                None,
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            "INSERT INTO api_keys VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (7, "infura", "legacy", encrypted, 1, None, 0, timestamp, timestamp),
        )
    (root / ".env").write_text(f"APP_SECRET_KEY={secret}\n", encoding="utf-8")

    database = Database(
        Settings(data_dir=data_dir, database_url=f"sqlite:///{legacy_path.as_posix()}")
    )
    database.initialize()
    database.initialize()

    with database.session() as session:
        assert session.get(Wallet, 42).chain_id == 9
        key = session.scalar(select(ApiKey))
        assert key.id == 7
        assert key.value == "legacy-provider-key"
    with sqlite3.connect(legacy_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM wallets").fetchone() == (1,)
