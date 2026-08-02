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
    derive_events,
    derive_swaps,
    group_transaction_legs,
    is_cash_conversion_event,
    is_token_trade_event,
)
from app.models import (
    Chain,
    InternalTransaction,
    NormalTransaction,
    Token,
    TokenPrice,
    TokenTransfer,
    Wallet,
)
from app.repositories import TokenRepository
from app.token_rules import STABLECOINS, asset_kind, is_suspicious_token_symbol


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

    def token_balance(
        self,
        token_address: str,
        wallet_address: str,
        block: int | str = "latest",
    ) -> int:
        return 10**18


def test_transaction_hash_grouping_detects_multi_leg_swap() -> None:
    grouped = {
        "0xhash": [
            AssetLeg(NATIVE_ADDRESS, "ETH", 0.05, "out", 100, 10, "0xhash"),
            AssetLeg(TOKEN_A, "AAA", 5.0, "in", 100, 10, "0xhash"),
            AssetLeg(TOKEN_B, "BBB", 10.0, "in", 100, 10, "0xhash"),
        ]
    }
    events = derive_events(grouped, FixedResolver())
    assert len(events) == 1
    assert events[0]["event_type"] == "maybe_liquidity_remove"
    assert events[0]["is_multi_leg"] is True
    assert events[0]["usd_out"] == pytest.approx(100.0)
    assert events[0]["usd_in"] == pytest.approx(100.0)


def test_fifo_pnl_consumes_oldest_lot_proportionally() -> None:
    swaps = [
        {
            "tx_hash": "buy",
            "timestamp": 1,
            "event_type": "buy_like",
            "usd_value": 100.0,
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
            "event_type": "sell_like",
            "usd_value": 60.0,
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
    result = compute_fifo_pnl(
        swaps, {TOKEN_A: 10.0}, {TOKEN_A: 6.0}
    )
    assert result["realized_pnl_usd"] == pytest.approx(20.0)
    assert result["gross_buy_spend_usd"] == pytest.approx(100.0)
    assert result["open_positions"][TOKEN_A]["quantity"] == pytest.approx(6.0)
    assert result["open_positions"][TOKEN_A]["known_cost_usd"] == pytest.approx(60.0)
    marked = compute_fifo_pnl(
        swaps, {TOKEN_A: 20.0}, {TOKEN_A: 6.0}
    )
    assert marked["unrealized_pnl_usd"] == pytest.approx(60.0)


def test_fifo_gas_is_counted_once_in_cost_and_realized_pnl() -> None:
    swaps = [
        {
            "tx_hash": "buy", "timestamp": 1, "usd_out": 100.0, "usd_in": 100.0,
            "event_type": "buy_like", "usd_value": 100.0,
            "gas_usd": 2.0,
            "outgoing": [{"asset": NATIVE_ADDRESS, "symbol": "ETH", "amount": 1, "usd_value": 100.0}],
            "incoming": [{"asset": TOKEN_A, "symbol": "AAA", "amount": 10, "usd_value": 100.0}],
        },
        {
            "tx_hash": "sell", "timestamp": 2, "usd_out": 120.0, "usd_in": 120.0,
            "event_type": "sell_like", "usd_value": 120.0,
            "gas_usd": 3.0,
            "outgoing": [{"asset": TOKEN_A, "symbol": "AAA", "amount": 10, "usd_value": 120.0}],
            "incoming": [{"asset": NATIVE_ADDRESS, "symbol": "ETH", "amount": 1, "usd_value": 120.0}],
        },
    ]
    result = compute_fifo_pnl(swaps)
    assert result["gross_buy_spend_usd"] == pytest.approx(102.0)
    assert result["realized_pnl_usd"] == pytest.approx(15.0)
    assert result["total_trade_gas_usd"] == pytest.approx(5.0)


def test_price_resolver_skips_onchain_fallback_by_default(app_client, monkeypatch) -> None:
    _, database = app_client
    monkeypatch.setattr("app.analytics._USE_UNISWAP_FALLBACK", False)
    monkeypatch.setattr("app.analytics._PRICE_CACHE", {})
    with database.session() as session:
        resolver = PriceResolver(session, EmptyPrices(), OnchainPrices(), 1)
        price = resolver.resolve(TOKEN_A, "AAA", 18, 1_700_000_000, 18_000_000)
        stored = session.scalar(
            select(TokenPrice).where(TokenPrice.source == "uniswap_v2")
        )
        assert price is None
        assert stored is None


def test_price_resolver_can_use_onchain_fallback(app_client, monkeypatch) -> None:
    _, database = app_client
    monkeypatch.setattr("app.analytics._USE_UNISWAP_FALLBACK", True)
    monkeypatch.setattr("app.analytics._PRICE_CACHE", {})
    with database.session() as session:
        resolver = PriceResolver(session, EmptyPrices(), OnchainPrices(), 1)
        price = resolver.resolve(TOKEN_A, "AAA", 18, 1_700_000_000, 18_000_000)
        stored = session.scalar(
            select(TokenPrice).where(TokenPrice.source == "uniswap_v2")
        )
        assert price == pytest.approx(12.5)
        assert stored is not None
        assert stored.price_usd == pytest.approx(12.5)


def test_price_resolver_does_not_trust_stablecoin_symbol(app_client, monkeypatch) -> None:
    _, database = app_client
    monkeypatch.setattr("app.analytics._USE_UNISWAP_FALLBACK", True)
    monkeypatch.setattr("app.analytics._PRICE_CACHE", {})
    official_usdc = next(iter(STABLECOINS))
    with database.session() as session:
        resolver = PriceResolver(session, EmptyPrices(), OnchainPrices(), 1)
        fake_usdc = resolver.resolve(
            TOKEN_A, "USDC", 18, 1_700_000_000, 18_000_000
        )
        canonical_usdc = resolver.resolve(
            official_usdc, "NOT_USDC", 6, 1_700_000_000, 18_000_000
        )

        assert fake_usdc == pytest.approx(12.5)
        assert canonical_usdc == pytest.approx(1.0)


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
        assert snapshot.features["token_trade_count"] == 0
        assert snapshot.features["gross_buy_spend_usd"] == 0
        assert snapshot.features["realized_pnl_usd"] == pytest.approx(0.0)
        assert snapshot.features["winrate"] == pytest.approx(0.0)
        assert snapshot.features["covered_sell_count"] == 0
        # transfer_in is not trading inventory, so completeness stays true
        # when there are no unmatched sell_like events.
        assert snapshot.quality["pnl_valid"] is True
        assert snapshot.quality["unknown_open_position_count"] == 0
        assert snapshot.quality["transaction_hash_groups"] == 1


def test_suspicious_symbol_rule_matches_notify() -> None:
    assert is_suspicious_token_symbol("USDT") is False
    assert is_suspicious_token_symbol("UЅDT") is True
    assert is_suspicious_token_symbol(None) is False


def test_self_call_and_internal_value_are_netted_per_transaction() -> None:
    wallet = Wallet(
        id=1,
        chain_id=1,
        address="0x0000000000000000000000000000000000000011",
    )
    normal = NormalTransaction(
        chain_id=1,
        wallet_id=1,
        tx_hash="0xself",
        block_number=10,
        timestamp=100,
        from_address=wallet.address,
        to_address=wallet.address,
        value_wei=str(10**18),
        gas_used="21000",
        gas_price="1",
        success=True,
        raw={},
    )
    internal = InternalTransaction(
        chain_id=1,
        wallet_id=1,
        tx_hash="0xself",
        trace_id="0_1",
        block_number=10,
        timestamp=100,
        from_address=wallet.address,
        to_address=TOKEN_A,
        value_wei=str(10**18),
        success=True,
        raw={},
    )
    token = Token(
        id=1,
        chain_id=1,
        address=TOKEN_B,
        symbol="BBB",
        decimals=6,
        suspicious=False,
    )
    transfer = TokenTransfer(
        chain_id=1,
        wallet_id=1,
        token_id=1,
        tx_hash="0xself",
        log_index=0,
        block_number=10,
        timestamp=100,
        from_address=TOKEN_A,
        to_address=wallet.address,
        raw_value=str(500 * 10**6),
        raw={},
    )

    grouped = group_transaction_legs(
        wallet, [normal], [internal], [(transfer, token)]
    )

    assert len(grouped["0xself"]) == 2
    native = next(
        leg for leg in grouped["0xself"] if leg.asset == NATIVE_ADDRESS
    )
    received = next(
        leg for leg in grouped["0xself"] if leg.asset == TOKEN_B
    )
    assert native.direction == "out"
    assert native.amount == pytest.approx(1.0)
    assert received.amount == pytest.approx(500.0)
    assert received.decimals == 6
    event = derive_events(grouped, FixedResolver())[0]
    assert event["event_type"] == "buy_like"
    assert event["usd_out"] == pytest.approx(2000.0)
    assert event["usd_in"] == pytest.approx(2500.0)
    assert event["usd_value"] == pytest.approx(2000.0)


def test_stablecoin_identity_uses_contract_address_not_symbol() -> None:
    official_usdc = next(iter(STABLECOINS))
    assert asset_kind(official_usdc) == "stable"
    assert asset_kind(TOKEN_A) == "token"


def test_transfer_out_removes_fifo_inventory_without_realizing_pnl() -> None:
    events = [
        {
            "tx_hash": "buy",
            "timestamp": 1,
            "event_type": "buy_like",
            "usd_value": 100.0,
            "gas_usd": 1.0,
            "outgoing": [
                {"asset": NATIVE_ADDRESS, "symbol": "ETH", "amount": 1}
            ],
            "incoming": [
                {
                    "asset": TOKEN_A,
                    "symbol": "AAA",
                    "decimals": 18,
                    "amount": 10.0,
                    "usd_value": 100.0,
                }
            ],
        },
        {
            "tx_hash": "transfer",
            "timestamp": 2,
            "event_type": "transfer_out",
            "usd_value": 0.0,
            "gas_usd": 0.5,
            "outgoing": [
                {
                    "asset": TOKEN_A,
                    "symbol": "AAA",
                    "decimals": 18,
                    "amount": 10.0,
                }
            ],
            "incoming": [],
        },
    ]

    result = compute_fifo_pnl(events)

    assert result["open_positions"] == {}
    assert result["pnl_valid"] is True
    assert result["realized_pnl_usd"] == pytest.approx(0.0)
    assert result["gross_buy_spend_usd"] == pytest.approx(101.0)


def test_unknown_transfer_in_does_not_create_trading_basis() -> None:
    events = [
        {
            "tx_hash": "deposit",
            "timestamp": 1,
            "event_type": "transfer_in",
            "usd_value": 0.0,
            "gas_usd": 0.0,
            "outgoing": [],
            "incoming": [
                {
                    "asset": TOKEN_A,
                    "symbol": "AAA",
                    "decimals": 18,
                    "amount": 10.0,
                }
            ],
        },
        {
            "tx_hash": "sell",
            "timestamp": 2,
            "event_type": "sell_like",
            "usd_value": 120.0,
            "gas_usd": 2.0,
            "outgoing": [
                {
                    "asset": TOKEN_A,
                    "symbol": "AAA",
                    "decimals": 18,
                    "amount": 10.0,
                }
            ],
            "incoming": [
                {"asset": NATIVE_ADDRESS, "symbol": "ETH", "amount": 1}
            ],
        },
    ]

    result = compute_fifo_pnl(events)

    assert result["pnl_basis_coverage_ratio"] == pytest.approx(0.0)
    assert result["unknown_basis_sale_count"] == 1
    assert result["gross_sell_proceeds_usd"] == pytest.approx(120.0)
    assert result["realized_pnl_usd"] == pytest.approx(0.0)
    assert result["winrate"] == pytest.approx(0.0)
    assert result["covered_sell_count"] == 0
    assert result["pnl_valid"] is False
    assert result["open_positions"] == {}


def test_inventory_mismatch_is_diagnostic_only_for_trading_pnl() -> None:
    event = {
        "tx_hash": "buy",
        "timestamp": 1,
        "event_type": "buy_like",
        "usd_value": 100.0,
        "gas_usd": 1.0,
        "outgoing": [
            {"asset": NATIVE_ADDRESS, "symbol": "ETH", "amount": 1}
        ],
        "incoming": [
            {
                "asset": TOKEN_A,
                "symbol": "AAA",
                "decimals": 18,
                "amount": 10.0,
                "usd_value": 100.0,
            }
        ],
    }

    result = compute_fifo_pnl(
        [event], {TOKEN_A: 12.0}, {TOKEN_A: 0.0}
    )

    assert result["inventory_mismatch_count"] == 1
    assert result["inventory_reconciliation_ratio"] == pytest.approx(0.0)
    # Buy/sell sample is complete even if residual trading lot != chain balance.
    assert result["pnl_valid"] is True
    assert result["unrealized_pnl_usd"] == pytest.approx(19.0)
    assert result["realized_pnl_usd"] == pytest.approx(0.0)
    assert result["total_pnl_usd"] == pytest.approx(19.0)


def test_cash_conversion_is_not_counted_as_token_investment() -> None:
    usdc = next(iter(STABLECOINS))
    event = {
        "tx_hash": "cash",
        "timestamp": 1,
        "event_type": "swap_like",
        "usd_value": 1000.0,
        "gas_usd": 2.0,
        "outgoing": [
            {"asset": NATIVE_ADDRESS, "symbol": "ETH", "amount": 0.4}
        ],
        "incoming": [
            {"asset": usdc, "symbol": "USDC", "amount": 995.0}
        ],
    }

    result = compute_fifo_pnl([event])

    assert result["cash_conversion_count"] == 1
    assert result["cash_conversion_volume_usd"] == pytest.approx(1000.0)
    assert result["token_trade_count"] == 0
    assert result["gross_buy_spend_usd"] == pytest.approx(0.0)
    assert result["gross_sell_proceeds_usd"] == pytest.approx(0.0)


def test_stable_token_trades_and_token_swaps_count_as_trading() -> None:
    usdc = next(iter(STABLECOINS))
    buy = {
        "tx_hash": "buy-stable",
        "timestamp": 1,
        "event_type": "buy_like",
        "usd_value": 100.0,
        "gas_usd": 1.0,
        "outgoing": [{"asset": usdc, "symbol": "USDC", "amount": 100.0}],
        "incoming": [
            {
                "asset": TOKEN_A,
                "symbol": "AAA",
                "decimals": 18,
                "amount": 10.0,
                "usd_value": 100.0,
            }
        ],
    }
    sell = {
        "tx_hash": "sell-stable",
        "timestamp": 2,
        "event_type": "sell_like",
        "usd_value": 130.0,
        "gas_usd": 1.0,
        "outgoing": [
            {
                "asset": TOKEN_A,
                "symbol": "AAA",
                "decimals": 18,
                "amount": 10.0,
            }
        ],
        "incoming": [{"asset": usdc, "symbol": "USDC", "amount": 130.0}],
    }
    token_swap = {
        "tx_hash": "token-swap",
        "timestamp": 3,
        "event_type": "swap_like",
        "usd_value": 50.0,
        "gas_usd": 1.0,
        "outgoing": [
            {
                "asset": TOKEN_A,
                "symbol": "AAA",
                "decimals": 18,
                "amount": 1.0,
                "usd_value": 50.0,
            }
        ],
        "incoming": [
            {
                "asset": TOKEN_B,
                "symbol": "BBB",
                "decimals": 18,
                "amount": 2.0,
                "usd_value": 50.0,
            }
        ],
    }

    assert is_token_trade_event(buy) is True
    assert is_token_trade_event(sell) is True
    assert is_token_trade_event(token_swap) is True
    assert is_cash_conversion_event(token_swap) is False

    paired = compute_fifo_pnl([buy, sell])
    assert paired["token_trade_count"] == 2
    assert paired["gross_buy_spend_usd"] == pytest.approx(101.0)
    assert paired["gross_sell_proceeds_usd"] == pytest.approx(130.0)
    assert paired["realized_pnl_usd"] == pytest.approx(28.0)
    assert paired["pnl_valid"] is True

    swap_only = compute_fifo_pnl([token_swap])
    assert swap_only["token_trade_count"] == 1
    assert swap_only["cash_conversion_count"] == 0
    assert swap_only["unknown_basis_sale_count"] == 1
    assert swap_only["open_positions"][TOKEN_B]["quantity"] == pytest.approx(2.0)
