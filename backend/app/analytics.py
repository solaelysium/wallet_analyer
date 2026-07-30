from __future__ import annotations

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


FEATURE_VERSION = "wallet_features.v3"
TRADE_EVENT_TYPES = {"buy_like", "sell_like", "swap_like"}


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
        token = self._token(address.lower(), symbol, decimals)
        bucket = timestamp // 1800
        key = (token.id, bucket)
        if key in self.memory:
            return self.memory[key]
        window = 6 * 3600
        cached = self.session.scalar(
            select(TokenPrice)
            .where(
                TokenPrice.token_id == token.id,
                TokenPrice.timestamp.between(timestamp - window, timestamp + window),
            )
            .order_by(func.abs(TokenPrice.timestamp - timestamp))
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
            return quote
        rows = []
        try:
            rows = self.provider.prices(
                "ethereum",
                None if token.coingecko_id else token.address,
                token.coingecko_id,
                timestamp - 24 * 3600,
                timestamp + 24 * 3600,
            )
        except (NoProviderKeyError, RuntimeError, ValueError):
            pass
        for quote_timestamp, price in rows:
            exists = self.session.scalar(
                select(TokenPrice.id).where(
                    TokenPrice.token_id == token.id,
                    TokenPrice.timestamp == quote_timestamp,
                    TokenPrice.source == "coingecko",
                )
            )
            if exists is None:
                self.session.add(
                    TokenPrice(
                        token_id=token.id,
                        timestamp=quote_timestamp,
                        price_usd=price,
                        source="coingecko",
                        block_number=block_number,
                    )
                )
        self.session.flush()
        if rows:
            price = min(rows, key=lambda item: abs(item[0] - timestamp))[1]
            quote = PriceQuote(
                price,
                "coingecko",
                "high" if address in ETH_LIKE_ADDRESSES else "medium",
            )
            self.memory[key] = quote
            return quote
        if block_number is not None:
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
                return quote
        self.memory[key] = None
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


def compute_fifo_pnl(
    swaps: list[dict],
    latest_prices: dict[str, float] | None = None,
) -> dict:
    latest_prices = latest_prices or {}
    lots: dict[str, deque] = defaultdict(deque)
    realized = 0.0
    invested = 0.0
    cash_in = 0.0
    cash_out = 0.0
    total_gas = 0.0
    wins = 0
    losses = 0
    symbols: dict[str, str] = {}
    decimals_by_asset: dict[str, int] = {}

    def add_lot(asset: str, quantity: float, cost: float) -> None:
        if quantity > 0 and cost >= 0:
            lots[asset].append([quantity, cost])

    def consume(asset: str, quantity: float) -> tuple[float, float]:
        remaining = max(quantity, 0.0)
        cost = 0.0
        consumed = 0.0
        queue = lots[asset]
        while remaining > 1e-15 and queue:
            lot_quantity, lot_cost = queue[0]
            take = min(remaining, lot_quantity)
            fraction = take / lot_quantity
            allocated = lot_cost * fraction
            cost += allocated
            consumed += take
            remaining -= take
            if take >= lot_quantity - 1e-15:
                queue.popleft()
            else:
                queue[0] = [lot_quantity - take, lot_cost - allocated]
        return cost, consumed

    for swap in sorted(swaps, key=lambda item: (item.get("timestamp", 0), item.get("tx_hash", ""))):
        outgoing = swap.get("outgoing", [])
        incoming = swap.get("incoming", [])
        notional = float(
            swap.get(
                "usd_value",
                max(
                    float(swap.get("usd_out", 0.0) or 0.0),
                    float(swap.get("usd_in", 0.0) or 0.0),
                ),
            )
            or 0.0
        )
        gas_usd = float(swap.get("gas_usd", 0.0) or 0.0)
        total_gas += gas_usd
        outgoing_cash = any(
            is_trusted_quote_asset(str(leg.get("asset", "")))
            for leg in outgoing
        )
        incoming_cash = any(
            is_trusted_quote_asset(str(leg.get("asset", "")))
            for leg in incoming
        )
        if outgoing_cash and notional > 0:
            invested += notional
            cash_out += notional
        sold_cost = 0.0
        sold_any = False
        for leg in outgoing:
            asset = str(leg.get("asset", "")).lower()
            quantity = float(leg.get("amount", 0.0) or 0.0)
            if is_trusted_quote_asset(asset):
                continue
            symbols[asset] = str(leg.get("symbol", ""))
            decimals_by_asset[asset] = int(leg.get("decimals", 18) or 18)
            cost, consumed = consume(asset, quantity)
            sold_cost += cost
            sold_any = sold_any or consumed > 0
        if sold_any and notional > 0:
            proceeds = notional
            trade_pnl = proceeds - sold_cost - gas_usd
            realized += trade_pnl
            if incoming_cash:
                cash_in += proceeds
            if trade_pnl > 0:
                wins += 1
            elif trade_pnl < 0:
                losses += 1
        acquired = [
            leg
            for leg in incoming
            if not is_trusted_quote_asset(str(leg.get("asset", "")))
        ]
        priced_total = sum(
            float(leg.get("usd_value", 0.0) or 0.0) for leg in acquired
        )
        for leg in acquired:
            asset = str(leg.get("asset", "")).lower()
            quantity = float(leg.get("amount", 0.0) or 0.0)
            market_value = float(leg.get("usd_value", 0.0) or 0.0)
            weight = (
                market_value / priced_total
                if priced_total > 0
                else 1 / max(len(acquired), 1)
            )
            leg_value = (notional + gas_usd) * weight
            symbols[asset] = str(leg.get("symbol", ""))
            decimals_by_asset[asset] = int(leg.get("decimals", 18) or 18)
            if notional > 0:
                add_lot(asset, quantity, leg_value)
        if outgoing_cash and gas_usd:
            invested += gas_usd
            cash_out += gas_usd

    unrealized = 0.0
    open_positions = {}
    for asset, queue in lots.items():
        quantity = sum(lot[0] for lot in queue)
        cost = sum(lot[1] for lot in queue)
        market_value = quantity * latest_prices.get(asset, 0.0)
        unrealized += market_value - cost if asset in latest_prices else 0.0
        open_positions[asset] = {
            "quantity": quantity,
            "cost_usd": cost,
            "symbol": symbols.get(asset, ""),
            "decimals": decimals_by_asset.get(asset, 18),
        }
    total = realized + unrealized
    trades = wins + losses
    return {
        "realized_pnl_usd": realized,
        "unrealized_pnl_usd": unrealized,
        "total_pnl_usd": total,
        "roi": total / invested if invested else 0.0,
        "invested_usd": invested,
        "cash_out_usd": cash_out,
        "cash_in_usd": cash_in,
        "total_gas_usd": total_gas,
        "wins": wins,
        "losses": losses,
        "winrate": wins / trades if trades else 0.0,
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
        for row in normals:
            if row.from_address.lower() != wallet.address.lower():
                continue
            gas_eth = safe_int(row.gas_used) * safe_int(row.gas_price) / 10**18
            eth_price = resolver.resolve(
                NATIVE_ADDRESS, "ETH", 18, row.timestamp, row.block_number
            )
            gas_by_hash[row.tx_hash] = gas_eth * (eth_price or 0.0)
        events = derive_events(grouped, resolver, gas_by_hash)
        swaps = [
            event
            for event in events
            if event["event_type"] in TRADE_EVENT_TYPES
        ]
        pnl = compute_fifo_pnl(swaps)
        latest_prices = {}
        now = int(datetime.now(timezone.utc).timestamp())
        for asset, position in pnl["open_positions"].items():
            price = resolver.resolve(
                asset,
                position.get("symbol", ""),
                int(position.get("decimals", 18)),
                now,
                as_of_block,
            )
            if price is not None:
                latest_prices[asset] = price
        pnl = compute_fifo_pnl(swaps, latest_prices)
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
        trade_values = [
            float(item.get("usd_value", 0.0))
            for item in swaps
            if float(item.get("usd_value", 0.0)) > 0
        ]
        swap_days = {
            datetime.fromtimestamp(item["timestamp"], timezone.utc).date()
            for item in swaps
            if item["timestamp"]
        }
        token_assets = {
            leg["asset"]
            for swap in swaps
            for leg in [*swap["outgoing"], *swap["incoming"]]
            if asset_kind(str(leg["asset"])) == "token"
        }
        known_prices = sum(
            leg.get("price_usd") is not None
            for swap in swaps
            for leg in [*swap["outgoing"], *swap["incoming"]]
        )
        total_legs = sum(
            len(swap["outgoing"]) + len(swap["incoming"]) for swap in swaps
        )
        total_out = float(pnl["cash_out_usd"])
        total_in = float(pnl["cash_in_usd"])
        total_gas = sum(gas_by_hash.values())
        token_volume: dict[str, float] = defaultdict(float)
        stable_volume = 0.0
        for swap in swaps:
            notional = float(swap.get("usd_value", 0.0) or 0.0)
            token_legs = [
                leg
                for leg in [*swap["outgoing"], *swap["incoming"]]
                if asset_kind(str(leg.get("asset", ""))) == "token"
            ]
            symbols = {str(leg.get("symbol", "")) for leg in token_legs}
            for symbol in symbols:
                token_volume[symbol] += notional / max(len(symbols), 1)
            if any(
                asset_kind(str(leg.get("asset", ""))) == "stable"
                for leg in [*swap["outgoing"], *swap["incoming"]]
            ):
                stable_volume += notional
        token_volume_total = sum(token_volume.values())
        if token_volume:
            top_token_symbol, top_token_volume = max(
                token_volume.items(), key=lambda item: item[1]
            )
        else:
            top_token_symbol, top_token_volume = "", 0.0
        features = {
            "wallet_age_days": (now - first) / 86400 if timestamps else 0.0,
            "days_since_last_activity": (now - last) / 86400 if timestamps else 0.0,
            "transactions_per_day": len(blocks) / active_days,
            "normal_transaction_count": len(normals),
            "internal_transaction_count": len(internals),
            "token_transfer_count": len(transfers),
            "native_balance": balance_wei / 10**18,
            "classified_event_count": len(events),
            "swap_count": len(swaps),
            "valued_swap_count": len(trade_values),
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
            "multi_leg_swap_count": sum(item["is_multi_leg"] for item in swaps),
            "dex_active_days": len(swap_days),
            "swaps_per_active_day": len(swaps) / len(swap_days) if swap_days else 0.0,
            "average_trade_usd": mean(trade_values) if trade_values else 0.0,
            "median_trade_usd": median(trade_values) if trade_values else 0.0,
            "max_trade_usd": max(trade_values) if trade_values else 0.0,
            "total_volume_usd": sum(trade_values),
            "total_out_usd": total_out,
            "total_in_usd": total_in,
            "total_gas_usd": total_gas,
            "average_gas_per_swap_usd": total_gas / len(swaps) if swaps else 0.0,
            "gas_share_percent": (
                total_gas * 100 / sum(trade_values)
                if trade_values else 0.0
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
            **{key: value for key, value in pnl.items() if key != "open_positions"},
        }
        quality = {
            "priced_leg_ratio": known_prices / total_legs if total_legs else 1.0,
            "transaction_hash_groups": len(grouped),
            "reliably_valued_trade_ratio": (
                len(trade_values) / len(swaps) if swaps else 1.0
            ),
            "low_confidence_trade_count": sum(
                item["valuation_confidence"] in {"low", "none"}
                for item in swaps
            ),
            "max_valuation_divergence": max(
                (
                    float(item["valuation_divergence"])
                    for item in swaps
                    if item["valuation_divergence"] is not None
                ),
                default=0.0,
            ),
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
