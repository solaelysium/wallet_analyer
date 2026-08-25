from sqlalchemy import select

from app.jobs import MAX_WALLET_EVENTS, CollectionJobManager
from app.key_pool import KeyPool
from app.models import Chain, Job, JobItem, Wallet
from app.providers import ProviderBundle, TooManyTransactionsError
from conftest import FakeExplorer, FakePrices, FakeRpc


class HeavyExplorer(FakeExplorer):
    def __init__(self, normal_count: int) -> None:
        self.normal_count = normal_count

    def normal_transactions(
        self,
        address: str,
        cancel_check=None,
        start_block: int = 0,
        max_rows: int | None = None,
    ) -> list[dict]:
        if cancel_check:
            cancel_check()
        if max_rows is not None and self.normal_count > max_rows:
            raise TooManyTransactionsError(self.normal_count)
        return [
            {
                "hash": f"0x{index:064x}",
                "blockNumber": str(index),
                "timeStamp": str(1_700_000_000 + index),
                "from": address,
                "to": "0x00000000000000000000000000000000000000bb",
                "value": "0",
                "gasUsed": "21000",
                "gasPrice": "1",
                "isError": "0",
                "txreceipt_status": "1",
                "methodId": "0x",
                "functionName": "",
            }
            for index in range(self.normal_count)
        ]


def test_collection_skips_wallets_with_too_many_transactions(app_client) -> None:
    client, database = app_client
    providers = ProviderBundle(
        HeavyExplorer(MAX_WALLET_EVENTS + 1),
        FakeRpc(),
        FakePrices(),
        KeyPool({}),
    )
    manager = CollectionJobManager(database, providers, max_workers=1)
    with database.session() as session:
        chain = session.scalar(select(Chain).where(Chain.slug == "ethereum"))
        wallet = Wallet(
            chain_id=chain.id,
            address="0x00000000000000000000000000000000000000aa",
        )
        session.add(wallet)
        session.flush()
        job = Job(kind="collection", state="queued", progress_total=1)
        session.add(job)
        session.flush()
        item = JobItem(job_id=job.id, wallet_id=wallet.id, state="queued")
        session.add(item)
        session.flush()
        job_id = job.id
        item_id = item.id

    manager._run_item(job_id, item_id)
    with database.session() as session:
        item = session.get(JobItem, item_id)
        job = session.get(Job, job_id)
        assert item.state == "skipped"
        assert "25 000" in (item.error or "") or "25000" in (item.error or "")
        assert (item.checkpoint or {}).get("event_count") == MAX_WALLET_EVENTS + 1
        assert job.progress_done == 1
        assert job.state == "completed"

    response = client.get(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["skipped"] == 1
    assert payload["summary"]["completed"] == 0
    assert payload["summary"]["total"] == 1
    assert payload["items"][0]["event_count"] == MAX_WALLET_EVENTS + 1
    assert payload["items"][0]["state"] == "skipped"
    manager.shutdown()


class SplitHeavyExplorer(FakeExplorer):
    """Overflow only on token transfers after earlier phases fill most of the budget."""

    def __init__(self, normal_count: int, transfer_count: int) -> None:
        self.normal_count = normal_count
        self.transfer_count = transfer_count

    def normal_transactions(
        self,
        address: str,
        cancel_check=None,
        start_block: int = 0,
        max_rows: int | None = None,
    ) -> list[dict]:
        if cancel_check:
            cancel_check()
        assert max_rows is None or self.normal_count <= max_rows
        return [
            {
                "hash": f"0xn{index:063x}",
                "blockNumber": str(index),
                "timeStamp": str(1_700_000_000 + index),
                "from": address,
                "to": "0x00000000000000000000000000000000000000bb",
                "value": "0",
                "gasUsed": "21000",
                "gasPrice": "1",
                "isError": "0",
                "txreceipt_status": "1",
                "methodId": "0x",
                "functionName": "",
            }
            for index in range(self.normal_count)
        ]

    def token_transfers(
        self,
        address: str,
        cancel_check=None,
        start_block: int = 0,
        max_rows: int | None = None,
    ) -> list[dict]:
        if cancel_check:
            cancel_check()
        if max_rows is not None and self.transfer_count > max_rows:
            raise TooManyTransactionsError(self.transfer_count)
        return []


def test_skip_count_includes_earlier_phases(app_client) -> None:
    _, database = app_client
    normal_count = 18_000
    transfer_reported = 7_144
    providers = ProviderBundle(
        SplitHeavyExplorer(normal_count, transfer_reported),
        FakeRpc(),
        FakePrices(),
        KeyPool({}),
    )
    manager = CollectionJobManager(database, providers, max_workers=1)
    with database.session() as session:
        chain = session.scalar(select(Chain).where(Chain.slug == "ethereum"))
        wallet = Wallet(
            chain_id=chain.id,
            address="0x00000000000000000000000000000000000000cc",
        )
        session.add(wallet)
        session.flush()
        job = Job(kind="collection", state="queued", progress_total=1)
        session.add(job)
        session.flush()
        item = JobItem(job_id=job.id, wallet_id=wallet.id, state="queued")
        session.add(item)
        session.flush()
        job_id = job.id
        item_id = item.id

    manager._run_item(job_id, item_id)
    with database.session() as session:
        item = session.get(JobItem, item_id)
        assert item.state == "skipped"
        event_count = (item.checkpoint or {}).get("event_count")
        assert event_count == normal_count + transfer_reported
        assert event_count > MAX_WALLET_EVENTS
        assert f"{event_count:,}".replace(",", " ") in (item.error or "")
    manager.shutdown()


def test_etherscan_paged_raises_when_over_max_rows(monkeypatch) -> None:
    from app.providers import EtherscanV2Provider

    provider = EtherscanV2Provider(KeyPool({}), page_size=10)
    calls = {"n": 0}

    def fake_call(service, fn, params):
        page = int(params["page"])
        start_block = int(params["startblock"])
        calls["n"] += 1
        start = (page - 1) * 10
        return [
            {
                "hash": f"0x{start_block:08x}{start + i:056x}",
                "blockNumber": str(start_block + start + i),
                "traceId": "",
                "logIndex": str(start + i),
            }
            for i in range(10)
        ]

    monkeypatch.setattr(provider.key_pool, "call", fake_call)
    try:
        provider.normal_transactions("0xabc", max_rows=15)
        raised = False
    except TooManyTransactionsError as exc:
        raised = True
        assert exc.count > 15
    assert raised
    assert calls["n"] >= 2


def test_etherscan_paged_slides_past_result_window(monkeypatch) -> None:
    from app.providers import ETHERSCAN_MAX_RESULT_WINDOW, EtherscanV2Provider

    provider = EtherscanV2Provider(KeyPool({}), page_size=1000)
    calls = {"n": 0, "startblocks": []}

    def fake_call(service, fn, params):
        page = int(params["page"])
        start_block = int(params["startblock"])
        assert page * 1000 <= ETHERSCAN_MAX_RESULT_WINDOW
        calls["n"] += 1
        calls["startblocks"].append(start_block)
        base = start_block + (page - 1) * 1000
        if base >= 25_000:
            return []
        return [
            {
                "hash": f"0x{base + i:064x}",
                "blockNumber": str(base + i),
                "traceId": "",
                "logIndex": str(base + i),
            }
            for i in range(1000)
        ]

    monkeypatch.setattr(provider.key_pool, "call", fake_call)
    rows = provider.normal_transactions("0xabc")
    assert len(rows) >= 12_000
    assert calls["n"] > 10
    assert max(calls["startblocks"]) > 0
    # Overlapping startblock slides must not duplicate identities.
    assert len({row["hash"] for row in rows}) == len(rows)


def test_etherscan_maps_result_window_error() -> None:
    from app.providers import ResultWindowTooLargeError, EtherscanV2Provider

    class FakeResponse:
        status_code = 200
        ok = True

        def json(self):
            return {
                "status": "0",
                "message": "NOTOK",
                "result": (
                    "Result window is too large, PageNo x Offset size "
                    "must be less than or equal to 10000"
                ),
            }

    provider = EtherscanV2Provider(KeyPool({}), page_size=1000)

    def fake_get(*args, **kwargs):
        return FakeResponse()

    import app.providers as providers_mod

    original = providers_mod.requests.get
    providers_mod.requests.get = fake_get
    try:
        try:
            provider._request("key", {"module": "account", "action": "tokentx"})
            raised = False
        except ResultWindowTooLargeError:
            raised = True
        assert raised
    finally:
        providers_mod.requests.get = original
