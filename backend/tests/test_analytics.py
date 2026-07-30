from __future__ import annotations

import pytest
from sqlalchemy import select

from app.analytics import (
    FEATURE_VERSION,
    NATIVE_ADDRESS,
    AssetLeg,
    FeatureCalculator,
    PriceResolver,
    compute_fifo_pnl,
    derive_swaps,
)
from app.models import Chain, NormalTransaction, TokenPrice, TokenTransfer, Wallet
from app.repositories import TokenRepository
from app.token_rules import is_suspicious_token_symbol


TOKEN_A = "0x00000000000000000000000000000000000000aa"
TOKEN_B = "0x00000000000000000000000000000000000000bb"


class FixedResolver:
    def resolve(
        self,
        address: str,
        symbol: str,
        decimals: int,
        timestamp: int,
        block_number: int | None = None,
    ) -> float:
        return {"ETH": 2000.0, "AAA": 10.0, "BBB": 5.0}[symbol]


class EmptyPrices:
    def prices(
        self,
        platform: str,
        token_address: str | None,
        coin_id: str | None,
        start_timestamp: int,
        end_timestamp: int,
    ) -> list[tuple[int, float]]:
        return []


class OnchainPrices:
    def token_price_usd_at_block(
        self, token_address: str, block_number: int
    ) -> float:
        return 12.5


def test_transaction_hash_grouping_detects_multi_leg_swap() -> None:
    grouped = {
        "0xhash": [
            AssetLeg(NATIVE_ADDRESS, "ETH", 0.05, "out", 100, 10, "0xhash"),
            AssetLeg(TOKEN_A, "AAA", 5.0, "in", 100, 10, "0xhash"),
            AssetLeg(TOKEN_B, "BBB", 10.0, "in", 100, 10, "0xhash"),
        ]
    }
    swaps = derive_swaps(grouped, FixedResolver())
    assert len(swaps) == 1
    assert swaps[0]["is_multi_leg"] is True
    assert swaps[0]["usd_out"] == pytest.approx(100.0)
    assert swaps[0]["usd_in"] == pytest.approx(100.0)


def test_fifo_pnl_consumes_oldest_lot_proportionally() -> None:
    swaps = [
        {
            "tx_hash": "buy",
            "timestamp": 1,
            "usd_out": 100.0,
            "usd_in": 100.0,
            "outgoing": [
                {
                    "asset": NATIVE_ADDRESS,
                    "symbol": "ETH",
                    "amount": 0.05,
                    "usd_value": 100.0,
                }
            ],
            "incoming": [
                {
                    "asset": TOKEN_A,
                    "symbol": "AAA",
                    "amount": 10.0,
                    "usd_value": 100.0,
                }
            ],
        },
        {
            "tx_hash": "sell",
            "timestamp": 2,
            "usd_out": 60.0,
            "usd_in": 60.0,
            "outgoing": [
                {
                    "asset": TOKEN_A,
                    "symbol": "AAA",
                    "amount": 4.0,
                    "usd_value": 60.0,
                }
            ],
            "incoming": [
                {
                    "asset": NATIVE_ADDRESS,
                    "symbol": "ETH",
                    "amount": 0.03,
                    "usd_value": 60.0,
                }
            ],
        },
    ]
    result = compute_fifo_pnl(swaps)
    assert result["realized_pnl_usd"] == pytest.approx(20.0)
    assert result["invested_usd"] == pytest.approx(100.0)
    assert result["open_positions"][TOKEN_A]["quantity"] == pytest.approx(6.0)
    assert result["open_positions"][TOKEN_A]["cost_usd"] == pytest.approx(60.0)
    marked = compute_fifo_pnl(swaps, {TOKEN_A: 20.0})
    assert marked["unrealized_pnl_usd"] == pytest.approx(60.0)


def test_fifo_gas_is_counted_once_in_cost_and_realized_pnl() -> None:
    swaps = [
        {
            "tx_hash": "buy", "timestamp": 1, "usd_out": 100.0, "usd_in": 100.0,
            "gas_usd": 2.0,
            "outgoing": [{"asset": NATIVE_ADDRESS, "symbol": "ETH", "amount": 1, "usd_value": 100.0}],
            "incoming": [{"asset": TOKEN_A, "symbol": "AAA", "amount": 10, "usd_value": 100.0}],
        },
        {
            "tx_hash": "sell", "timestamp": 2, "usd_out": 120.0, "usd_in": 120.0,
            "gas_usd": 3.0,
            "outgoing": [{"asset": TOKEN_A, "symbol": "AAA", "amount": 10, "usd_value": 120.0}],
            "incoming": [{"asset": NATIVE_ADDRESS, "symbol": "ETH", "amount": 1, "usd_value": 120.0}],
        },
    ]
    result = compute_fifo_pnl(swaps)
    assert result["invested_usd"] == pytest.approx(102.0)
    assert result["realized_pnl_usd"] == pytest.approx(15.0)
    assert result["total_gas_usd"] == pytest.approx(5.0)


def test_price_resolver_uses_and_caches_onchain_fallback(app_client) -> None:
    _, database = app_client
    with database.session() as session:
        resolver = PriceResolver(session, EmptyPrices(), OnchainPrices(), 1)
        price = resolver.resolve(TOKEN_A, "AAA", 18, 1_700_000_000, 18_000_000)
        stored = session.scalar(
            select(TokenPrice).where(TokenPrice.source == "uniswap_v2")
        )
        assert price == pytest.approx(12.5)
        assert stored is not None
        assert stored.price_usd == pytest.approx(12.5)


def test_suspicious_symbols_and_failed_transactions_are_excluded(app_client) -> None:
    _, database = app_client
    with database.session() as session:
        chain = session.scalar(select(Chain).where(Chain.slug == "ethereum"))
        wallet = Wallet(
            chain_id=chain.id,
            address="0x0000000000000000000000000000000000000011",
            checksum_address="0x0000000000000000000000000000000000000011",
        )
        session.add(wallet)
        session.flush()
        tokens = TokenRepository(session)
        regular = tokens.get_or_create(1, TOKEN_A, "GOOD", "Regular", 18)
        suspicious = tokens.get_or_create(1, TOKEN_B, "UЅDT", "Lookalike", 18)
        session.flush()
        assert regular.suspicious is False
        assert suspicious.suspicious is True
        session.add_all([
            TokenTransfer(
                chain_id=1,
                wallet_id=wallet.id,
                token_id=regular.id,
                tx_hash="0xgood",
                log_index=0,
                block_number=10,
                timestamp=100,
                from_address=TOKEN_A,
                to_address=wallet.address,
                raw_value=str(10**18),
                raw={},
            ),
            TokenTransfer(
                chain_id=1,
                wallet_id=wallet.id,
                token_id=suspicious.id,
                tx_hash="0xspam",
                log_index=0,
                block_number=11,
                timestamp=200,
                from_address=TOKEN_B,
                to_address=wallet.address,
                raw_value=str(10**18),
                raw={},
            ),
            NormalTransaction(
                chain_id=1,
                wallet_id=wallet.id,
                tx_hash="0xfailed",
                block_number=12,
                timestamp=300,
                from_address=wallet.address,
                to_address=TOKEN_A,
                value_wei=str(10**18),
                gas_used="21000",
                gas_price="1",
                success=False,
                raw={},
            ),
        ])
        session.flush()

        snapshot = FeatureCalculator(
            session, EmptyPrices(), OnchainPrices()
        ).calculate(wallet, 12, 10**18)

        assert snapshot.version == FEATURE_VERSION
        assert snapshot.features["token_transfer_count"] == 1
        assert snapshot.features["normal_transaction_count"] == 0
        assert snapshot.quality["transaction_hash_groups"] == 1


def test_suspicious_symbol_rule_matches_notify() -> None:
    assert is_suspicious_token_symbol("USDT") is False
    assert is_suspicious_token_symbol("UЅDT") is True
    assert is_suspicious_token_symbol(None) is False
