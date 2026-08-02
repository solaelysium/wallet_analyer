from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from sqlalchemy import select

from app.config import Settings
from app.database import Database
from app.models import Chain, Token
from app.repositories import TokenRepository, _DB_WRITE_LOCK


def test_concurrent_token_lookups_do_not_close_database(tmp_path: Path) -> None:
    database = Database(Settings(data_dir=tmp_path))
    database.initialize()
    with database.session() as session:
        chain = session.scalar(select(Chain).where(Chain.slug == "ethereum"))
        chain_id = chain.id

    def worker(index: int) -> str:
        address = f"0x{index + 1000:040x}"
        with _DB_WRITE_LOCK:
            with database.session() as session:
                token = TokenRepository(session).get_or_create(
                    chain_id,
                    address,
                    symbol=f"T{index}",
                    name=f"Token {index}",
                    decimals=18,
                )
                found = session.scalar(
                    select(Token).where(
                        Token.chain_id == chain_id,
                        Token.address == address,
                    )
                )
                assert found is not None
                return token.address

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(worker, index) for index in range(48)]
        results = [future.result() for future in as_completed(futures)]

    assert len(results) == 48
    with database.session() as session:
        created = len(
            session.scalars(
                select(Token).where(
                    Token.chain_id == chain_id,
                    Token.address.in_([f"0x{index + 1000:040x}" for index in range(48)]),
                )
            ).all()
        )
        assert created == 48
