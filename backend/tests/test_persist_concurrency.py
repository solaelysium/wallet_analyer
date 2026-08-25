from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import time

from sqlalchemy import func, select

from app.config import Settings
from app.database import Database
from app.jobs import CollectionJobManager
from app.key_pool import KeyPool
from app.models import Chain, Token, TokenTransfer, Wallet
from app.providers import ProviderBundle
from app.token_rules import NATIVE_ADDRESS, WETH_ADDRESS
from conftest import FakeExplorer, FakePrices, FakeRpc


HOT_TOKENS = [
    (NATIVE_ADDRESS, "ETH", "Ether", 18),
    (WETH_ADDRESS, "WETH", "Wrapped Ether", 18),
    ("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USDC", "USD Coin", 6),
    ("0xdac17f958d2ee523a2206206994597c13d831ec7", "USDT", "Tether USD", 6),
]


def _transfer_rows(wallet_index: int, count: int) -> list[dict]:
    rows = []
    for index in range(count):
        token_address, symbol, name, decimals = HOT_TOKENS[index % len(HOT_TOKENS)]
        rows.append(
            {
                "contractAddress": token_address,
                "hash": f"0x{wallet_index:04x}{index:060x}",
                "blockNumber": str(20_000_000 + index),
                "timeStamp": str(1_700_000_000 + index),
                "from": f"0x{wallet_index + 1:040x}",
                "to": f"0x{(wallet_index + 2):040x}",
                "value": str(10**decimals),
                "tokenDecimal": str(decimals),
                "tokenSymbol": symbol,
                "tokenName": name,
                "logIndex": str(index),
            }
        )
    return rows


def test_persist_transfers_survives_concurrent_hot_token_writes(tmp_path: Path) -> None:
    database = Database(Settings(data_dir=tmp_path))
    database.initialize()
    providers = ProviderBundle(FakeExplorer(), FakeRpc(), FakePrices(), KeyPool({}))
    manager = CollectionJobManager(database, providers, max_workers=8)

    with database.session() as session:
        chain = session.scalar(select(Chain).where(Chain.slug == "ethereum"))
        wallets = []
        for index in range(8):
            wallet = Wallet(
                chain_id=chain.id,
                address=f"0x{index + 10:040x}",
            )
            session.add(wallet)
            session.flush()
            wallets.append(wallet.id)

    rows_per_wallet = 800

    def worker(wallet_id: int, wallet_index: int) -> int:
        manager._persist_transfers(wallet_id, _transfer_rows(wallet_index, rows_per_wallet))
        return wallet_id

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(worker, wallet_id, index)
            for index, wallet_id in enumerate(wallets)
        ]
        done = [future.result(timeout=120) for future in as_completed(futures, timeout=120)]
    elapsed = time.perf_counter() - started
    assert elapsed < 60

    assert sorted(done) == sorted(wallets)
    with database.session() as session:
        transfers = session.scalar(select(func.count()).select_from(TokenTransfer))
        eth = session.scalar(
            select(func.count())
            .select_from(Token)
            .where(Token.address == NATIVE_ADDRESS)
        )
        assert transfers == 8 * rows_per_wallet
        assert eth == 1

    manager.shutdown()
