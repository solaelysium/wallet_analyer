from __future__ import annotations

import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from statistics import mean, median

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .key_pool import NoProviderKeyError
from .models import (
    InternalTransaction,
    NormalTransaction,
    Token,
    TokenPrice,
    TokenTransfer,
    Wallet,
    WalletFeatureSnapshot,
)
from .providers import HistoricalPriceProvider, RpcProvider
from .repositories import TokenRepository
from .token_rules import (
    ETH_LIKE_ADDRESSES,
    NATIVE_ADDRESS,
    STABLECOINS,
    WETH_ADDRESS,
    asset_kind,
    is_trusted_quote_asset,
)


FEATURE_VERSION = "wallet_features.v4"
TRADE_EVENT_TYPES = {"buy_like", "sell_like", "swap_like"}
# Uniswap V2 fallback does multiple eth_calls per miss and stalls features.
_USE_UNISWAP_FALLBACK = False
# Daily buckets are enough for wallet analytics and match CoinGecko granularity.
_PRICE_BUCKET_SECONDS = 86400
_PRICE_CACHE: dict[tuple[str, int], object] = {}
_PRICE_CACHE_LOCK = threading.Lock()
_ETH_HISTORY_WARMED = False


def safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


@dataclass
class AssetLeg:
    asset: str
    symbol: str
    amount: float
    direction: str
    timestamp: int
    block_number: int
    tx_hash: str
    decimals: int = 18
    price_usd: float | None = None
    price_source: str | None = None
    price_confidence: str = "none"

    @property
    def usd_value(self) -> float | None:
        if self.price_usd is None:
            return None
        return self.amount * self.price_usd


@dataclass
class PriceQuote:
    price_usd: float
    source: str
    confidence: str


class PriceResolver:
    def __init__(
        self,
        session: Session,
        provider: HistoricalPriceProvider,
        rpc_provider: RpcProvider,
        chain_id: int,
    ) -> None:
        self.session = session
        self.provider = provider
        self.rpc_provider = rpc_provider
        self.chain_id = chain_id
        self.memory: dict[tuple[int, int], PriceQuote | None] = {}

    def _token(
        self, address: str, symbol: str | None = None, decimals: int = 18
    ) -> Token:
        coin_id = "ethereum" if address in ETH_LIKE_ADDRESSES else None
        token = TokenRepository(self.session).get_or_create(
            self.chain_id,
            address,
            symbol or ("ETH" if address in ETH_LIKE_ADDRESSES else None),
            "Ether" if address in ETH_LIKE_ADDRESSES else None,
            decimals,
        )
        if coin_id and not token.coingecko_id:
            token.coingecko_id = coin_id
        return token

    def resolve(
        self,
        address: str,
        symbol: str,
        decimals: int,
        timestamp: int,
        block_number: int | None = None,
    ) -> float | None:
        quote = self.resolve_quote(
            address, symbol, decimals, timestamp, block_number
        )
        return quote.price_usd if quote else None

    def _bucket(self, timestamp: int) -> int:
        return int(timestamp) // _PRICE_BUCKET_SECONDS

    def _fill_process_cache(
        self,
        address: str,
        rows: list[tuple[int, float]],
        confidence: str,
        source: str = "coingecko",
    ) -> None:
        if not rows:
            return
        ordered = sorted(rows, key=lambda item: item[0])
        with _PRICE_CACHE_LOCK:
            for index, (quote_timestamp, price) in enumerate(ordered):
                quote = PriceQuote(price, source, confidence)
                day = self._bucket(quote_timestamp)
                _PRICE_CACHE[(address, day)] = quote
                if index + 1 >= len(ordered):
                    continue
                next_day = self._bucket(ordered[index + 1][0])
                fill_day = day + 1
                while fill_day < next_day:
                    _PRICE_CACHE[(address, fill_day)] = quote
                    fill_day += 1

    def _store_process_cache(self, address: str, bucket: int, quote: PriceQuote | None) -> None:
        with _PRICE_CACHE_LOCK:
            _PRICE_CACHE[(address, bucket)] = quote

    def _load_process_cache(self, address: str, bucket: int) -> tuple[bool, PriceQuote | None]:
        with _PRICE_CACHE_LOCK:
            if (address, bucket) in _PRICE_CACHE:
                return True, _PRICE_CACHE[(address, bucket)]
            for delta in (1, -1, 2, -2, 3, -3):
                nearby = (address, bucket + delta)
                if nearby in _PRICE_CACHE and _PRICE_CACHE[nearby] is not None:
                    return True, _PRICE_CACHE[nearby]
        return False, None

    def _warm_eth_history(self) -> None:
        global _ETH_HISTORY_WARMED
        with _PRICE_CACHE_LOCK:
            if _ETH_HISTORY_WARMED:
                return
            _ETH_HISTORY_WARMED = True
        now = int(datetime.now(timezone.utc).timestamp())
        start = 1_514_764_800  # 2018-01-01
        try:
            rows = self.provider.prices(
                "ethereum", None, "ethereum", start, now + 86400
            )
        except (NoProviderKeyError, RuntimeError, ValueError):
            rows = []
        if not rows:
            return
        token = self._token(NATIVE_ADDRESS, "ETH", 18)
        self._fill_process_cache(NATIVE_ADDRESS, rows, "high")
        self._fill_process_cache(WETH_ADDRESS, rows, "high")
        from .repositories import sqlite_upsert

        for quote_timestamp, price in rows:
            sqlite_upsert(
                self.session,
                TokenPrice,
                {
                    "token_id": token.id,
                    "timestamp": int(quote_timestamp),
                    "price_usd": price,
                    "source": "coingecko",
                    "block_number": None,
                },
                ["token_id", "timestamp", "source"],
            )

    def resolve_quote(
        self,
        address: str,
        symbol: str,
        decimals: int,
        timestamp: int,
        block_number: int | None = None,
    ) -> PriceQuote | None:
        address = address.lower()
        if address in STABLECOINS:
            return PriceQuote(1.0, "canonical_stablecoin", "high")
        bucket = self._bucket(timestamp)
        hit, cached_quote = self._load_process_cache(address, bucket)
        if hit:
            return cached_quote
        # Junk ERC-20s: skip CoinGecko entirely. PnL uses trusted quote legs.
        if asset_kind(address) == "token" and not _USE_UNISWAP_FALLBACK:
            self._store_process_cache(address, bucket, None)
            return None
        if address in ETH_LIKE_ADDRESSES:
            self._warm_eth_history()
            hit, cached_quote = self._load_process_cache(NATIVE_ADDRESS, bucket)
            if hit:
                self._store_process_cache(address, bucket, cached_quote)
                return cached_quote
            hit, cached_quote = self._load_process_cache(address, bucket)
            if hit:
                return cached_quote
        token = self._token(address, symbol, decimals)
        key = (token.id, bucket)
        if key in self.memory:
            self._store_process_cache(address, bucket, self.memory[key])
            return self.memory[key]
        window = 2 * 86400
        cached = self.session.scalar(
            select(TokenPrice)
            .where(
                TokenPrice.token_id == token.id,
                TokenPrice.timestamp.between(timestamp - window, timestamp + window),
            )
            .limit(1)
        )
        if cached is not None:
            quote = PriceQuote(
                cached.price_usd,
                cached.source,
                (
                    "high"
                    if address in ETH_LIKE_ADDRESSES
                    else "medium" if cached.source == "coingecko" else "low"
                ),
            )
            self.memory[key] = quote
            self._store_process_cache(address, bucket, quote)
            return quote
        # Optional Uniswap fallback for non-trusted assets.
        if asset_kind(address) == "token":
            if _USE_UNISWAP_FALLBACK and block_number is not None:
                price = self.rpc_provider.token_price_usd_at_block(
                    token.address, block_number
                )
                if price is not None and price > 0:
                    self.session.add(
                        TokenPrice(
                            token_id=token.id,
                            timestamp=timestamp,
                            price_usd=price,
                            source="uniswap_v2",
                            block_number=block_number,
                        )
                    )
                    self.session.flush()
                    quote = PriceQuote(price, "uniswap_v2", "low")
                    self.memory[key] = quote
                    self._store_process_cache(address, bucket, quote)
                    return quote
            self.memory[key] = None
            self._store_process_cache(address, bucket, None)
            return None
        rows = []
        try:
            span = 30 * 86400 if address in ETH_LIKE_ADDRESSES else 24 * 3600
            rows = self.provider.prices(
                "ethereum",
                None if token.coingecko_id else token.address,
                token.coingecko_id,
                timestamp - span,
                timestamp + span,
            )
        except (NoProviderKeyError, RuntimeError, ValueError):
            pass
        conf = "high" if address in ETH_LIKE_ADDRESSES else "medium"
        self._fill_process_cache(address, rows, conf)
        for quote_timestamp, price in rows:
            self.session.add(
                TokenPrice(
                    token_id=token.id,
                    timestamp=int(quote_timestamp),
                    price_usd=price,
                    source="coingecko",
                    block_number=block_number,
                )
            )
        if rows:
            try:
                self.session.flush()
            except Exception:
                self.session.rollback()
            price = min(rows, key=lambda item: abs(item[0] - timestamp))[1]
            quote = PriceQuote(price, "coingecko", conf)
            self.memory[key] = quote
            self._store_process_cache(address, bucket, quote)
            return quote
        if _USE_UNISWAP_FALLBACK and block_number is not None:
            price = self.rpc_provider.token_price_usd_at_block(
                token.address, block_number
            )
            if price is not None and price > 0:
                self.session.add(
                    TokenPrice(
                        token_id=token.id,
                        timestamp=timestamp,
                        price_usd=price,
                        source="uniswap_v2",
                        block_number=block_number,
                    )
                )
                self.session.flush()
                quote = PriceQuote(price, "uniswap_v2", "low")
                self.memory[key] = quote
                self._store_process_cache(address, bucket, quote)
                return quote
        self.memory[key] = None
        self._store_process_cache(address, bucket, None)
        return None


def group_transaction_legs(
    wallet: Wallet,
    normals: list[NormalTransaction],
    internals: list[InternalTransaction],
    transfers: list[tuple[TokenTransfer, Token]],
) -> dict[str, list[AssetLeg]]:
    address = wallet.address.lower()
    net_moves: dict[tuple[str, str], dict] = {}

    def add_move(
        tx_hash: str,
        asset: str,
        symbol: str,
        decimals: int,
        raw_delta: int,
        timestamp: int,
        block_number: int,
    ) -> None:
        if raw_delta == 0:
            return
        key = (tx_hash, asset)
        current = net_moves.setdefault(
            key,
            {
                "tx_hash": tx_hash,
                "asset": asset,
                "symbol": symbol,
                "decimals": decimals,
                "raw_delta": 0,
                "timestamp": timestamp,
                "block_number": block_number,
            },
        )
        current["raw_delta"] += raw_delta
        if timestamp and not current["timestamp"]:
            current["timestamp"] = timestamp
        if block_number:
            current["block_number"] = min(
                current["block_number"] or block_number, block_number
            )

    def native_move(row: NormalTransaction | InternalTransaction) -> None:
        raw_value = safe_int(row.value_wei)
        if raw_value <= 0:
            return
        delta = 0
        if (row.from_address or "").lower() == address:
            delta -= raw_value
        if (row.to_address or "").lower() == address:
            delta += raw_value
        add_move(
            row.tx_hash,
            NATIVE_ADDRESS,
            "ETH",
            18,
            delta,
            row.timestamp,
            row.block_number,
        )

    for row in normals:
        native_move(row)
    for row in internals:
        native_move(row)
    for transfer, token in transfers:
        raw_value = safe_int(transfer.raw_value)
        if raw_value <= 0:
            continue
        delta = 0
        if transfer.from_address.lower() == address:
            delta -= raw_value
        if transfer.to_address.lower() == address:
            delta += raw_value
        add_move(
            transfer.tx_hash,
            token.address,
            token.symbol or token.address[:10],
            token.decimals,
            delta,
            transfer.timestamp,
            transfer.block_number,
        )

    grouped: dict[str, list[AssetLeg]] = defaultdict(list)
    for move in net_moves.values():
        raw_delta = int(move["raw_delta"])
        if raw_delta == 0:
            continue
        decimals = int(move["decimals"])
        amount = float(
            abs(Decimal(raw_delta)) / (Decimal(10) ** max(decimals, 0))
        )
        grouped[move["tx_hash"]].append(
            AssetLeg(
                asset=move["asset"],
                symbol=move["symbol"],
                amount=amount,
                direction="out" if raw_delta < 0 else "in",
                timestamp=move["timestamp"],
                block_number=move["block_number"],
                tx_hash=move["tx_hash"],
                decimals=decimals,
            )
        )
    return grouped


def aggregate_legs(legs: list[AssetLeg]) -> list[AssetLeg]:
    aggregated: dict[tuple[str, str], AssetLeg] = {}
    for leg in legs:
        key = (leg.asset, leg.direction)
        if key not in aggregated:
            aggregated[key] = AssetLeg(**leg.__dict__)
        else:
            aggregated[key].amount += leg.amount
    return list(aggregated.values())


def classify_event(legs: list[AssetLeg]) -> str:
    outgoing = [leg for leg in legs if leg.direction == "out"]
    incoming = [leg for leg in legs if leg.direction == "in"]
    out_kinds = {asset_kind(leg.asset) for leg in outgoing}
    in_kinds = {asset_kind(leg.asset) for leg in incoming}
    out_assets = {leg.asset for leg in outgoing}
    in_assets = {leg.asset for leg in incoming}
    if not outgoing and not incoming:
        return "empty"
    if outgoing and not incoming:
        return "transfer_out"
    if incoming and not outgoing:
        return "transfer_in"
    if (out_assets | in_assets) <= ETH_LIKE_ADDRESSES:
        return "wrap_or_unwrap"
    if len(outgoing) >= 2 and len(incoming) == 1:
        return "maybe_liquidity_add"
    if len(outgoing) == 1 and len(incoming) >= 2:
        return "maybe_liquidity_remove"
    if out_assets != in_assets:
        if out_kinds & {"native", "weth", "stable"} and "token" in in_kinds:
            return "buy_like"
        if "token" in out_kinds and in_kinds & {"native", "weth", "stable"}:
            return "sell_like"
        return "swap_like"
    return "self_transfer_or_rebalance"


def _reliable_notional(
    event_type: str,
    outgoing: list[AssetLeg],
    incoming: list[AssetLeg],
) -> tuple[float, str, float | None]:
    trusted_out = sum(
        leg.usd_value or 0.0
        for leg in outgoing
        if is_trusted_quote_asset(leg.asset)
    )
    trusted_in = sum(
        leg.usd_value or 0.0
        for leg in incoming
        if is_trusted_quote_asset(leg.asset)
    )
    if event_type == "buy_like" and trusted_out > 0:
        return trusted_out, "high", None
    if event_type == "sell_like" and trusted_in > 0:
        return trusted_in, "high", None
    if trusted_out > 0 or trusted_in > 0:
        values = [value for value in (trusted_out, trusted_in) if value > 0]
        divergence = (
            abs(trusted_out - trusted_in) / max(trusted_out, trusted_in)
            if trusted_out > 0 and trusted_in > 0
            else None
        )
        return sum(values) / len(values), "high", divergence

    priced_out = sum(leg.usd_value or 0.0 for leg in outgoing)
    priced_in = sum(leg.usd_value or 0.0 for leg in incoming)
    acceptable = all(
        leg.price_usd is not None and leg.price_confidence in {"high", "medium"}
        for leg in [*outgoing, *incoming]
    )
    if not acceptable or priced_out <= 0 or priced_in <= 0:
        return 0.0, "none", None
    divergence = abs(priced_out - priced_in) / max(priced_out, priced_in)
    if divergence > 0.20:
        return 0.0, "low", divergence
    return (priced_out + priced_in) / 2, "medium", divergence


def derive_events(
    grouped: dict[str, list[AssetLeg]],
    resolver: PriceResolver,
    gas_by_hash: dict[str, float] | None = None,
) -> list[dict]:
    gas_by_hash = gas_by_hash or {}
    events = []
    for tx_hash, raw_legs in grouped.items():
        legs = aggregate_legs(raw_legs)
        outgoing = [leg for leg in legs if leg.direction == "out"]
        incoming = [leg for leg in legs if leg.direction == "in"]
        for leg in legs:
            if hasattr(resolver, "resolve_quote"):
                quote = resolver.resolve_quote(
                    leg.asset,
                    leg.symbol,
                    leg.decimals,
                    leg.timestamp,
                    leg.block_number,
                )
                if quote is not None:
                    leg.price_usd = quote.price_usd
                    leg.price_source = quote.source
                    leg.price_confidence = quote.confidence
            else:
                leg.price_usd = resolver.resolve(
                    leg.asset,
                    leg.symbol,
                    leg.decimals,
                    leg.timestamp,
                    leg.block_number,
                )
                if leg.price_usd is not None:
                    leg.price_source = "resolver"
                    leg.price_confidence = (
                        "high" if is_trusted_quote_asset(leg.asset) else "medium"
                    )
        known_out = sum(leg.usd_value or 0.0 for leg in outgoing)
        known_in = sum(leg.usd_value or 0.0 for leg in incoming)
        event_type = classify_event(legs)
        notional, valuation_confidence, divergence = _reliable_notional(
            event_type, outgoing, incoming
        )
        events.append(
            {
                "tx_hash": tx_hash,
                "timestamp": min(leg.timestamp for leg in legs),
                "block_number": min(leg.block_number for leg in legs),
                "event_type": event_type,
                "outgoing": [leg.__dict__ | {"usd_value": leg.usd_value} for leg in outgoing],
                "incoming": [leg.__dict__ | {"usd_value": leg.usd_value} for leg in incoming],
                "usd_out": known_out,
                "usd_in": known_in,
                "usd_value": notional,
                "valuation_confidence": valuation_confidence,
                "valuation_divergence": divergence,
                "gas_usd": gas_by_hash.get(tx_hash, 0.0),
                "is_multi_leg": len(outgoing) > 1 or len(incoming) > 1,
            }
        )
    return sorted(events, key=lambda item: (item["timestamp"], item["tx_hash"]))


def derive_swaps(
    grouped: dict[str, list[AssetLeg]],
    resolver: PriceResolver,
    gas_by_hash: dict[str, float] | None = None,
) -> list[dict]:
    return [
        event
        for event in derive_events(grouped, resolver, gas_by_hash)
        if event["event_type"] in TRADE_EVENT_TYPES
    ]


def is_cash_conversion_event(event: dict) -> bool:
    legs = [*event.get("outgoing", []), *event.get("incoming", [])]
    return (
        event.get("event_type") == "swap_like"
        and bool(legs)
        and all(is_trusted_quote_asset(str(leg.get("asset", ""))) for leg in legs)
    )


def is_token_trade_event(event: dict) -> bool:
    # Trading set aligned with notify intent:
    # - buy_like:  stable/ETH/WETH -> token
    # - sell_like: token -> stable/ETH/WETH
    # - token <-> token swap_like
    # ETH/WETH <-> stable remains cash conversion, not a token trade.
    event_type = event.get("event_type")
    if event_type in {"buy_like", "sell_like"}:
        return True
    if event_type != "swap_like" or is_cash_conversion_event(event):
        return False
    return any(
        asset_kind(str(leg.get("asset", ""))) == "token"
        for leg in [*event.get("outgoing", []), *event.get("incoming", [])]
    )


def compute_fifo_pnl(
    events: list[dict],
    latest_prices: dict[str, float] | None = None,
    current_balances: dict[str, float | None] | None = None,
) -> dict:
    latest_prices = latest_prices or {}
    current_balances = current_balances or {}
    lots: dict[str, deque] = defaultdict(deque)
    known_realized = 0.0
    covered_sale_notional = 0.0
    total_sale_notional = 0.0
    gross_buy_spend = 0.0
    gross_sell_proceeds = 0.0
    token_trade_volume = 0.0
    cash_conversion_volume = 0.0
    token_trade_gas = 0.0
    token_trade_count = 0
    cash_conversion_count = 0
    unknown_basis_sale_count = 0
    unknown_inventory_outflow_count = 0
    wins = 0
    losses = 0
    symbols: dict[str, str] = {}
    decimals_by_asset: dict[str, int] = {}

    def add_lot(asset: str, quantity: float, cost: float | None) -> None:
        if quantity > 1e-15:
            lots[asset].append(
                {"quantity": quantity, "cost_usd": cost}
            )

    def consume(asset: str, quantity: float) -> dict:
        remaining = max(quantity, 0.0)
        known_cost = 0.0
        known_quantity = 0.0
        unknown_quantity = 0.0
        queue = lots[asset]
        while remaining > 1e-15 and queue:
            lot = queue[0]
            lot_quantity = float(lot["quantity"])
            lot_cost = lot["cost_usd"]
            take = min(remaining, lot_quantity)
            fraction = take / lot_quantity
            if lot_cost is None:
                allocated = None
                unknown_quantity += take
            else:
                allocated = float(lot_cost) * fraction
                known_cost += allocated
                known_quantity += take
            remaining -= take
            if take >= lot_quantity - 1e-15:
                queue.popleft()
            else:
                lot["quantity"] = lot_quantity - take
                if allocated is not None:
                    lot["cost_usd"] = float(lot_cost) - allocated
        if remaining > 1e-15:
            unknown_quantity += remaining
        return {
            "known_cost_usd": known_cost,
            "known_quantity": known_quantity,
            "unknown_quantity": unknown_quantity,
            "requested_quantity": max(quantity, 0.0),
        }

    ordered_events = sorted(
        events,
        key=lambda item: (item.get("timestamp", 0), item.get("tx_hash", "")),
    )
    for event in ordered_events:
        event_type = str(event.get("event_type", ""))
        outgoing = event.get("outgoing", [])
        incoming = event.get("incoming", [])
        notional = float(
            event.get(
                "usd_value",
                max(
                    float(event.get("usd_out", 0.0) or 0.0),
                    float(event.get("usd_in", 0.0) or 0.0),
                ),
            )
            or 0.0
        )
        gas_usd = float(event.get("gas_usd", 0.0) or 0.0)
        token_trade = is_token_trade_event(event)
        cash_conversion = is_cash_conversion_event(event)
        if token_trade:
            token_trade_count += 1
            token_trade_volume += notional
            token_trade_gas += gas_usd
        if cash_conversion:
            cash_conversion_count += 1
            cash_conversion_volume += notional
        if event_type == "buy_like":
            gross_buy_spend += notional + gas_usd
        if event_type == "sell_like":
            gross_sell_proceeds += notional

        # Trading ledger:
        # - buy_like / sell_like (including stable <-> token)
        # - token <-> token swap_like
        # - transfer_out only shrinks leftover buy inventory
        # transfer_in never invents cost basis.
        # ETH/WETH <-> stable is cash conversion and is skipped here.
        token_swap = event_type == "swap_like" and token_trade
        if event_type not in {"buy_like", "sell_like", "transfer_out"} and not token_swap:
            continue

        outgoing_tokens = [
            leg
            for leg in outgoing
            if asset_kind(str(leg.get("asset", ""))) == "token"
        ]
        incoming_tokens = [
            leg
            for leg in incoming
            if asset_kind(str(leg.get("asset", ""))) == "token"
        ]

        if event_type in {"sell_like", "transfer_out"} or token_swap:
            consumed_rows = []
            for leg in outgoing_tokens:
                asset = str(leg.get("asset", "")).lower()
                quantity = float(leg.get("amount", 0.0) or 0.0)
                symbols[asset] = str(leg.get("symbol", ""))
                decimals_by_asset[asset] = int(leg.get("decimals", 18) or 18)
                result = consume(asset, quantity)
                consumed_rows.append(result)
                if (
                    event_type == "transfer_out"
                    and result["unknown_quantity"] > 1e-15
                ):
                    unknown_inventory_outflow_count += 1

            if (
                event_type in {"sell_like"} or token_swap
            ) and outgoing_tokens:
                sold_quantity = sum(
                    row["requested_quantity"] for row in consumed_rows
                )
                known_quantity = sum(
                    row["known_quantity"] for row in consumed_rows
                )
                known_cost = sum(
                    row["known_cost_usd"] for row in consumed_rows
                )
                coverage = (
                    min(known_quantity / sold_quantity, 1.0)
                    if sold_quantity > 1e-15
                    else 0.0
                )
                total_sale_notional += notional
                covered_sale_notional += notional * coverage
                known_trade_pnl = (
                    notional * coverage - known_cost - gas_usd * coverage
                )
                known_realized += known_trade_pnl
                if coverage < 1.0 - 1e-12:
                    unknown_basis_sale_count += 1
                else:
                    if known_trade_pnl > 0:
                        wins += 1
                    elif known_trade_pnl < 0:
                        losses += 1

        if event_type == "buy_like" or token_swap:
            priced_total = sum(
                float(leg.get("usd_value", 0.0) or 0.0)
                for leg in incoming_tokens
            )
            for leg in incoming_tokens:
                asset = str(leg.get("asset", "")).lower()
                quantity = float(leg.get("amount", 0.0) or 0.0)
                market_value = float(leg.get("usd_value", 0.0) or 0.0)
                weight = (
                    market_value / priced_total
                    if priced_total > 0
                    else 1 / max(len(incoming_tokens), 1)
                )
                symbols[asset] = str(leg.get("symbol", ""))
                decimals_by_asset[asset] = int(
                    leg.get("decimals", 18) or 18
                )
                cost = (
                    (notional + gas_usd) * weight
                    if notional > 0
                    else None
                )
                add_lot(asset, quantity, cost)

    open_positions = {}
    inventory_matched = 0
    inventory_mismatch_count = 0
    unknown_open_position_count = 0
    unpriced_open_position_count = 0
    unrealized = 0.0
    for asset, queue in lots.items():
        quantity = sum(float(lot["quantity"]) for lot in queue)
        if quantity <= 1e-12:
            continue
        known_cost = sum(
            float(lot["cost_usd"])
            for lot in queue
            if lot["cost_usd"] is not None
        )
        unknown_quantity = sum(
            float(lot["quantity"])
            for lot in queue
            if lot["cost_usd"] is None
        )
        if unknown_quantity > 1e-12:
            unknown_open_position_count += 1
        price = latest_prices.get(asset)
        if price is None:
            unpriced_open_position_count += 1
        elif unknown_quantity <= 1e-12:
            unrealized += quantity * price - known_cost
        actual_balance = current_balances.get(asset)
        # Trading inventory is buy/sell residual, not full wallet holdings.
        if asset in current_balances and actual_balance is not None:
            tolerance = max(
                10 ** (-min(decimals_by_asset.get(asset, 18), 12)),
                abs(actual_balance) * 1e-9,
            )
            if abs(quantity - actual_balance) <= tolerance:
                inventory_matched += 1
            else:
                inventory_mismatch_count += 1
        open_positions[asset] = {
            "quantity": quantity,
            "known_cost_usd": known_cost,
            "unknown_quantity": unknown_quantity,
            "symbol": symbols.get(asset, ""),
            "decimals": decimals_by_asset.get(asset, 18),
            "onchain_quantity": actual_balance,
        }

    basis_coverage = (
        covered_sale_notional / total_sale_notional
        if total_sale_notional > 0
        else 1.0
    )
    reconciliation_ratio = (
        inventory_matched / len(open_positions)
        if open_positions
        else 1.0
    )
    # Completeness of the trading sample: every sell_like had buy_like basis.
    pnl_complete = (
        basis_coverage >= 1.0 - 1e-12
        and unknown_basis_sale_count == 0
    )
    resolved_trades = wins + losses
    total = known_realized + unrealized
    return {
        "realized_pnl_usd": known_realized,
        "unrealized_pnl_usd": unrealized,
        "total_pnl_usd": total,
        "roi": total / gross_buy_spend if gross_buy_spend else 0.0,
        "wins": wins,
        "losses": losses,
        "winrate": wins / resolved_trades if resolved_trades else 0.0,
        "covered_sell_count": resolved_trades,
        "gross_buy_spend_usd": gross_buy_spend,
        "gross_sell_proceeds_usd": gross_sell_proceeds,
        "net_cash_deployed_usd": (
            gross_buy_spend - gross_sell_proceeds
        ),
        "token_trade_count": token_trade_count,
        "total_token_trade_volume_usd": token_trade_volume,
        "cash_conversion_count": cash_conversion_count,
        "cash_conversion_volume_usd": cash_conversion_volume,
        "total_trade_gas_usd": token_trade_gas,
        "known_realized_pnl_usd": known_realized,
        "pnl_basis_coverage_ratio": basis_coverage,
        "inventory_reconciliation_ratio": reconciliation_ratio,
        "unknown_basis_sale_count": unknown_basis_sale_count,
        "unknown_inventory_outflow_count": (
            unknown_inventory_outflow_count
        ),
        "inventory_mismatch_count": inventory_mismatch_count,
        "unknown_open_position_count": unknown_open_position_count,
        "unpriced_open_position_count": unpriced_open_position_count,
        "pnl_valid": pnl_complete,
        "open_positions": open_positions,
    }



class FeatureCalculator:
    def __init__(
        self,
        session: Session,
        price_provider: HistoricalPriceProvider,
        rpc_provider: RpcProvider,
    ) -> None:
        self.session = session
        self.price_provider = price_provider
        self.rpc_provider = rpc_provider

    def calculate(
        self,
        wallet: Wallet,
        as_of_block: int,
        balance_wei: int,
    ) -> WalletFeatureSnapshot:
        normals = list(
            self.session.scalars(
                select(NormalTransaction).where(
                    NormalTransaction.wallet_id == wallet.id,
                    NormalTransaction.success.is_(True),
                )
            ).all()
        )
        internals = list(
            self.session.scalars(
                select(InternalTransaction).where(
                    InternalTransaction.wallet_id == wallet.id,
                    InternalTransaction.success.is_(True),
                )
            ).all()
        )
        transfers = list(
            self.session.execute(
                select(TokenTransfer, Token)
                .join(Token, Token.id == TokenTransfer.token_id)
                .where(
                    TokenTransfer.wallet_id == wallet.id,
                    Token.suspicious.is_(False),
                )
            ).all()
        )
        grouped = group_transaction_legs(wallet, normals, internals, transfers)
        resolver = PriceResolver(
            self.session,
            self.price_provider,
            self.rpc_provider,
            wallet.chain_id,
        )
        gas_by_hash = {}
        eth_price_by_day: dict[int, float] = {}
        for row in normals:
            if row.from_address.lower() != wallet.address.lower():
                continue
            day = row.timestamp // _PRICE_BUCKET_SECONDS
            if day not in eth_price_by_day:
                eth_price_by_day[day] = resolver.resolve(
                    NATIVE_ADDRESS, "ETH", 18, row.timestamp, row.block_number
                ) or 0.0
            gas_eth = safe_int(row.gas_used) * safe_int(row.gas_price) / 10**18
            gas_by_hash[row.tx_hash] = gas_eth * eth_price_by_day[day]
        events = derive_events(grouped, resolver, gas_by_hash)
        token_trades = [
            event
            for event in events
            if is_token_trade_event(event)
        ]
        cash_conversions = [
            event
            for event in events
            if is_cash_conversion_event(event)
        ]
        pnl = compute_fifo_pnl(events)
        latest_prices = {}
        current_balances: dict[str, float | None] = {}
        now = int(datetime.now(timezone.utc).timestamp())
        for asset, position in pnl["open_positions"].items():
            # Skip RPC/price work for junk tokens; trusted notionals drive PnL.
            if asset_kind(str(asset)) == "token":
                current_balances[asset] = None
                continue
            price = resolver.resolve(
                asset,
                position.get("symbol", ""),
                int(position.get("decimals", 18)),
                now,
                as_of_block,
            )
            if price is not None:
                latest_prices[asset] = price
            raw_balance = self.rpc_provider.token_balance(
                asset, wallet.address, as_of_block
            )
            current_balances[asset] = (
                raw_balance / 10 ** int(position.get("decimals", 18))
                if raw_balance is not None
                else None
            )
        pnl = compute_fifo_pnl(
            events, latest_prices, current_balances
        )
        timestamps = [
            row.timestamp for row in [*normals, *internals, *[pair[0] for pair in transfers]]
            if row.timestamp > 0
        ]
        blocks = {
            row.block_number
            for row in [*normals, *internals, *[pair[0] for pair in transfers]]
            if row.block_number > 0
        }
        first = min(timestamps) if timestamps else now
        last = max(timestamps) if timestamps else now
        active_days = max((last - first) / 86400, 1.0)
        token_trade_values = [
            float(item.get("usd_value", 0.0))
            for item in token_trades
            if float(item.get("usd_value", 0.0)) > 0
        ]
        trade_days = {
            datetime.fromtimestamp(item["timestamp"], timezone.utc).date()
            for item in token_trades
            if item["timestamp"]
        }
        token_assets = {
            leg["asset"]
            for trade in token_trades
            for leg in [*trade["outgoing"], *trade["incoming"]]
            if asset_kind(str(leg["asset"])) == "token"
        }
        known_prices = sum(
            leg.get("price_usd") is not None
            for trade in token_trades
            for leg in [*trade["outgoing"], *trade["incoming"]]
        )
        total_legs = sum(
            len(trade["outgoing"]) + len(trade["incoming"])
            for trade in token_trades
        )
        total_transaction_gas = sum(gas_by_hash.values())
        total_trade_gas = sum(
            float(item.get("gas_usd", 0.0) or 0.0)
            for item in token_trades
        )
        token_volume: dict[str, float] = defaultdict(float)
        stable_volume = 0.0
        for trade in token_trades:
            notional = float(trade.get("usd_value", 0.0) or 0.0)
            token_legs = [
                leg
                for leg in [*trade["outgoing"], *trade["incoming"]]
                if asset_kind(str(leg.get("asset", ""))) == "token"
            ]
            symbols = {str(leg.get("symbol", "")) for leg in token_legs}
            for symbol in symbols:
                token_volume[symbol] += notional / max(len(symbols), 1)
            if any(
                asset_kind(str(leg.get("asset", ""))) == "stable"
                for leg in [*trade["outgoing"], *trade["incoming"]]
            ):
                stable_volume += notional
        token_volume_total = sum(token_volume.values())
        if token_volume:
            top_token_symbol, top_token_volume = max(
                token_volume.items(), key=lambda item: item[1]
            )
        else:
            top_token_symbol, top_token_volume = "", 0.0
        financial_features = {
            key: pnl[key]
            for key in (
                "realized_pnl_usd",
                "unrealized_pnl_usd",
                "total_pnl_usd",
                "roi",
                "wins",
                "losses",
                "winrate",
                "covered_sell_count",
                "gross_buy_spend_usd",
                "gross_sell_proceeds_usd",
                "net_cash_deployed_usd",
            )
        }
        features = {
            "wallet_age_days": (now - first) / 86400 if timestamps else 0.0,
            "days_since_last_activity": (now - last) / 86400 if timestamps else 0.0,
            "transactions_per_day": len(blocks) / active_days,
            "normal_transaction_count": len(normals),
            "internal_transaction_count": len(internals),
            "token_transfer_count": len(transfers),
            "native_balance": balance_wei / 10**18,
            "classified_event_count": len(events),
            "token_trade_count": len(token_trades),
            "valued_token_trade_count": len(token_trade_values),
            "cash_conversion_count": len(cash_conversions),
            "buy_like_count": sum(
                item["event_type"] == "buy_like" for item in events
            ),
            "sell_like_count": sum(
                item["event_type"] == "sell_like" for item in events
            ),
            "swap_like_count": sum(
                item["event_type"] == "swap_like" for item in events
            ),
            "transfer_in_count": sum(
                item["event_type"] == "transfer_in" for item in events
            ),
            "transfer_out_count": sum(
                item["event_type"] == "transfer_out" for item in events
            ),
            "wrap_or_unwrap_count": sum(
                item["event_type"] == "wrap_or_unwrap" for item in events
            ),
            "liquidity_event_count": sum(
                item["event_type"] in {
                    "maybe_liquidity_add", "maybe_liquidity_remove"
                }
                for item in events
            ),
            "multi_leg_token_trade_count": sum(
                item["is_multi_leg"] for item in token_trades
            ),
            "dex_active_days": len(trade_days),
            "token_trades_per_active_day": (
                len(token_trades) / len(trade_days)
                if trade_days else 0.0
            ),
            "average_token_trade_usd": (
                mean(token_trade_values) if token_trade_values else 0.0
            ),
            "median_token_trade_usd": (
                median(token_trade_values) if token_trade_values else 0.0
            ),
            "max_token_trade_usd": (
                max(token_trade_values) if token_trade_values else 0.0
            ),
            "total_token_trade_volume_usd": sum(token_trade_values),
            "cash_conversion_volume_usd": sum(
                float(item.get("usd_value", 0.0) or 0.0)
                for item in cash_conversions
            ),
            "max_cash_conversion_usd": max(
                (
                    float(item.get("usd_value", 0.0) or 0.0)
                    for item in cash_conversions
                ),
                default=0.0,
            ),
            "total_transaction_gas_usd": total_transaction_gas,
            "total_trade_gas_usd": total_trade_gas,
            "average_trade_gas_usd": (
                total_trade_gas / len(token_trades)
                if token_trades else 0.0
            ),
            "trade_gas_share_percent": (
                total_trade_gas * 100 / sum(token_trade_values)
                if token_trade_values else 0.0
            ),
            "unique_tokens_traded": len(token_assets),
            "top_token_symbol": top_token_symbol,
            "top_token_share_percent": (
                top_token_volume * 100 / token_volume_total
                if token_volume_total else 0.0
            ),
            "stable_token_volume_share_percent": (
                stable_volume * 100 / token_volume_total
                if token_volume_total else 0.0
            ),
            **financial_features,
        }
        quality = {
            "priced_leg_ratio": known_prices / total_legs if total_legs else 1.0,
            "transaction_hash_groups": len(grouped),
            "reliably_valued_token_trade_ratio": (
                len(token_trade_values) / len(token_trades)
                if token_trades else 1.0
            ),
            "low_confidence_token_trade_count": sum(
                item["valuation_confidence"] in {"low", "none"}
                for item in token_trades
            ),
            "max_valuation_divergence": max(
                (
                    float(item["valuation_divergence"])
                    for item in token_trades
                    if item["valuation_divergence"] is not None
                ),
                default=0.0,
            ),
            "pnl_valid": pnl["pnl_valid"],
            "pnl_basis_coverage_ratio": pnl[
                "pnl_basis_coverage_ratio"
            ],
            "inventory_reconciliation_ratio": pnl[
                "inventory_reconciliation_ratio"
            ],
            "unknown_basis_sale_count": pnl[
                "unknown_basis_sale_count"
            ],
            "unknown_inventory_outflow_count": pnl[
                "unknown_inventory_outflow_count"
            ],
            "inventory_mismatch_count": pnl[
                "inventory_mismatch_count"
            ],
            "unknown_open_position_count": pnl[
                "unknown_open_position_count"
            ],
            "unpriced_open_position_count": pnl[
                "unpriced_open_position_count"
            ],
            "known_realized_pnl_usd": pnl[
                "known_realized_pnl_usd"
            ],
            "open_positions": pnl["open_positions"],
        }
        snapshot = self.session.scalar(
            select(WalletFeatureSnapshot).where(
                WalletFeatureSnapshot.wallet_id == wallet.id,
                WalletFeatureSnapshot.version == FEATURE_VERSION,
                WalletFeatureSnapshot.as_of_block == as_of_block,
            )
        )
        if snapshot is None:
            snapshot = WalletFeatureSnapshot(
                wallet_id=wallet.id,
                version=FEATURE_VERSION,
                as_of_block=as_of_block,
                features=features,
                quality=quality,
            )
            self.session.add(snapshot)
        else:
            snapshot.features = features
            snapshot.quality = quality
        self.session.flush()
        return snapshot
