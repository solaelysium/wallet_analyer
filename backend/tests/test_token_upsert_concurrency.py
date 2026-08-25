from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from sqlalchemy import func, select

from app.config import Settings
from app.database import Database
from app.models import Chain, Token
from app.repositories import TokenRepository, _DB_WRITE_LOCK
from app.token_rules import NATIVE_ADDRESS, WETH_ADDRESS


def test_concurrent_token_upsert_is_race_safe(tmp_path: Path) -> None:
    database = Database(Settings(data_dir=tmp_path))
    database.initialize()
    with database.session() as session:
        chain_id = session.scalar(select(Chain.id).where(Chain.slug == "ethereum"))

    def worker(index: int) -> int:
        with _DB_WRITE_LOCK:
            with database.session() as session:
                tokens = TokenRepository(session)
                eth = tokens.get_or_create(chain_id, NATIVE_ADDRESS, "ETH", "Ether", 18)
                weth = tokens.get_or_create(
                    chain_id, WETH_ADDRESS, "WETH", "Wrapped Ether", 18
                )
                other = tokens.get_or_create(
                    chain_id,
                    f"0x{index + 1:040x}",
                    f"T{index}",
                    f"Token {index}",
                    18,
                )
                return eth.id + weth.id + other.id

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(worker, index) for index in range(36)]
        assert all(future.result() > 0 for future in as_completed(futures))

    with database.session() as session:
        eth_rows = session.scalar(
            select(func.count()).select_from(Token).where(
                Token.chain_id == chain_id,
                Token.address == NATIVE_ADDRESS,
            )
        )
        weth_rows = session.scalar(
            select(func.count()).select_from(Token).where(
                Token.chain_id == chain_id,
                Token.address == WETH_ADDRESS,
            )
        )
        total = session.scalar(
            select(func.count()).select_from(Token).where(Token.chain_id == chain_id)
        )
        assert eth_rows == 1
        assert weth_rows == 1
        assert total >= 38
