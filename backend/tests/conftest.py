from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.config import Settings
from app.database import Database
from app.key_pool import KeyPool
from app.main import create_app
from app.providers import (
    ExplorerProvider,
    HistoricalPriceProvider,
    ProviderBundle,
    RpcProvider,
)


class FakeExplorer(ExplorerProvider):
    def normal_transactions(
        self,
        address: str,
        cancel_check=None,
        start_block: int = 0,
        max_rows: int | None = None,
    ) -> list[dict]:
        if cancel_check:
            cancel_check()
        return []

    def internal_transactions(
        self,
        address: str,
        cancel_check=None,
        start_block: int = 0,
        max_rows: int | None = None,
    ) -> list[dict]:
        if cancel_check:
            cancel_check()
        return []

    def token_transfers(
        self,
        address: str,
        cancel_check=None,
        start_block: int = 0,
        max_rows: int | None = None,
    ) -> list[dict]:
        if cancel_check:
            cancel_check()
        return []


class FakeRpc(RpcProvider):
    def latest_block(self) -> int:
        return 22_000_000

    def is_wallet(self, address: str) -> bool | None:
        return True

    def balance_wei(self, address: str, block: str = "latest") -> int:
        return 10**18


class FakePrices(HistoricalPriceProvider):
    def prices(
        self,
        platform: str,
        token_address: str | None,
        coin_id: str | None,
        start_timestamp: int,
        end_timestamp: int,
    ) -> list[tuple[int, float]]:
        return [(start_timestamp, 2500.0), (end_timestamp, 2500.0)]


@pytest.fixture
def app_client(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        app_secret_key="test-secret",
        infura_api_keys=[],
        etherscan_api_keys=[],
        coingecko_api_keys=[],
        job_workers=2,
    )
    database = Database(settings)
    key_pool = KeyPool({})
    providers = ProviderBundle(
        explorer=FakeExplorer(),
        rpc=FakeRpc(),
        prices=FakePrices(),
        key_pool=key_pool,
    )
    app = create_app(settings, database, providers)
    with TestClient(app) as client:
        yield client, database
