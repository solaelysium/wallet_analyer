from __future__ import annotations

import time

from sqlalchemy import select

from app.config import Settings
from app.database import Database
from app.jobs import CollectionJobManager
from app.key_pool import KeyPool, RateLimitedError
from app.models import Chain, Job, JobItem, Token, Wallet
from app.providers import ProviderBundle
from conftest import FakeExplorer, FakePrices, FakeRpc


def test_settings_ignore_environment_provider_keys(monkeypatch) -> None:
    monkeypatch.setenv("INFURA_API_KEYS", "first, second")
    settings = Settings(_env_file=None)
    assert settings.infura_api_keys == []


def test_key_pool_rotates_after_rate_limit(monkeypatch) -> None:
    monkeypatch.setattr("app.key_pool.time.sleep", lambda _: None)
    pool = KeyPool(
        {"service": ["first", "second"]},
        concurrency=1,
        rps={"service": 1000},
        cooldown_seconds=10,
        max_retries=1,
    )

    def operation(key: str) -> str:
        if key == "first":
            raise RateLimitedError("429 rate limit")
        return key

    assert pool.call("service", operation) == "second"
    health = pool.health()["service"]
    assert health[0]["rate_limits"] == 1
    assert health[1]["successes"] == 1
    assert all("first" not in item["id"] and "second" not in item["id"] for item in health)


def test_missing_provider_keys_fail_item_not_startup(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}",
        app_secret_key="test",
        infura_api_keys=[],
        etherscan_api_keys=[],
        coingecko_api_keys=[],
        provider_max_retries=1,
    )
    database = Database(settings)
    database.initialize()
    providers = ProviderBundle.from_settings(settings)
    with database.session() as session:
        chain = session.scalar(select(Chain).where(Chain.slug == "ethereum"))
        wallet = Wallet(
            chain_id=chain.id,
            address="0x0000000000000000000000000000000000000001",
        )
        session.add(wallet)
        session.flush()
        job = Job(kind="collection", state="queued", progress_total=1)
        session.add(job)
        session.flush()
        item = JobItem(job_id=job.id, wallet_id=wallet.id)
        session.add(item)
        session.flush()
        job_id = job.id
        item_id = item.id
    manager = CollectionJobManager(database, providers, max_workers=1)
    manager.start(job_id)
    deadline = time.monotonic() + 3
    state = "running"
    while time.monotonic() < deadline and state == "running":
        with database.session() as session:
            state = session.get(Job, job_id).state
        time.sleep(0.02)
    with database.session() as session:
        job = session.get(Job, job_id)
        item = session.get(JobItem, item_id)
        assert job.state == "completed_with_errors"
        assert item.state == "failed"
        assert "No configured infura API keys" in item.error
    manager.shutdown()


def test_collection_prefers_onchain_token_decimals(app_client) -> None:
    _, database = app_client

    class SixDecimalRpc(FakeRpc):
        def token_decimals(
            self, token_address: str, block: int | str = "latest"
        ) -> int | None:
            return 6

    providers = ProviderBundle(
        FakeExplorer(), SixDecimalRpc(), FakePrices(), KeyPool({})
    )
    with database.session() as session:
        chain = session.scalar(select(Chain).where(Chain.slug == "ethereum"))
        wallet = Wallet(
            chain_id=chain.id,
            address="0x0000000000000000000000000000000000000011",
        )
        session.add(wallet)
        session.flush()
        wallet_id = wallet.id

    CollectionJobManager(database, providers, max_workers=1)._persist_transfers(
        wallet_id,
        [
            {
                "contractAddress": "0x00000000000000000000000000000000000000aa",
                "hash": "0xdecimals",
                "blockNumber": "10",
                "timeStamp": "100",
                "from": "0x00000000000000000000000000000000000000bb",
                "to": "0x0000000000000000000000000000000000000011",
                "value": str(500 * 10**6),
                "tokenDecimal": "18",
                "tokenSymbol": "AAA",
                "tokenName": "Token A",
                "logIndex": "0",
            }
        ],
    )

    with database.session() as session:
        token = session.scalar(
            select(Token).where(
                Token.address
                == "0x00000000000000000000000000000000000000aa"
            )
        )
        assert token.decimals == 6
