from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class Settings:
    """Bootstrap defaults only. Runtime values are loaded from keys.sqlite3."""

    def __init__(self, **values: object) -> None:
        database_url = str(values.pop("database_url", ""))
        data_dir = values.pop("data_dir", None)
        if data_dir is None and database_url.startswith("sqlite:///"):
            data_dir = Path(database_url.removeprefix("sqlite:///")).resolve().parent
        self.data_dir = Path(data_dir or Path.cwd() / "data").resolve()
        self.legacy_database_path = (
            Path(database_url.removeprefix("sqlite:///")).resolve()
            if database_url.startswith("sqlite:///")
            else self.data_dir / "wallet_analyzer.sqlite3"
        )
        self.app_name = str(values.pop("app_name", "Wallet Analyzer API"))
        self.app_secret_key = str(
            values.pop("app_secret_key", "local-preview-token-secret")
        )
        self.cors_origins = list(
            values.pop(
                "cors_origins",
                ["http://localhost:5173", "http://127.0.0.1:5173"],
            )
        )
        self.job_workers = max(1, int(values.pop("job_workers", 4)))
        self.provider_timeout_seconds = float(
            values.pop("provider_timeout_seconds", 30.0)
        )
        self.provider_max_retries = max(
            1, int(values.pop("provider_max_retries", 4))
        )
        self.provider_cooldown_seconds = float(
            values.pop("provider_cooldown_seconds", 30.0)
        )
        self.etherscan_rps = float(values.pop("etherscan_rps", 4.0))
        self.infura_rps = float(values.pop("infura_rps", 8.0))
        self.coingecko_rps = float(values.pop("coingecko_rps", 1.0))
        self.key_concurrency = max(1, int(values.pop("key_concurrency", 2)))
        self.infura_api_keys = list(values.pop("infura_api_keys", []))
        self.etherscan_api_keys = list(values.pop("etherscan_api_keys", []))
        self.coingecko_api_keys = list(values.pop("coingecko_api_keys", []))
        # Ignore old BaseSettings-only arguments to preserve test compatibility.
        values.pop("_env_file", None)
        if values:
            names = ", ".join(sorted(values))
            raise TypeError(f"Unknown settings: {names}")


class SecretBox:
    def __init__(self, secret: str) -> None:
        try:
            raw = secret.encode("ascii")
            Fernet(raw)
            key = raw
        except (ValueError, TypeError):
            key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        self._fernet = Fernet(key)

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str, ttl: int | None = None) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii"), ttl=ttl).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Stored secret cannot be decrypted with APP_SECRET_KEY") from exc

    def seal_json(self, value: str) -> str:
        return self.encrypt(value)

    def open_json(self, value: str) -> str:
        return self.decrypt(value, ttl=3600)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
