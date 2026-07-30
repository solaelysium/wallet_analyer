from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
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


FEATURE_VERSION = "wallet_features.v2"
NATIVE_ADDRESS = "0x0000000000000000000000000000000000000000"
STABLE_SYMBOLS = {"USDC", "USDT", "DAI", "TUSD", "USDP", "BUSD", "FRAX"}


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
    price_usd: float | None = None

    @property
    def usd_value(self) -> float | None:
        if self.price_usd is None:
            return None
        return self.amount * self.price_usd


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
        self.memory: dict[tuple[int, int], float | None] = {}

    def _token(
        self, address: str, symbol: str | None = None, decimals: int = 18
    ) -> Token:
        coin_id = "ethereum" if address == NATIVE_ADDRESS else None
        token = TokenRepository(self.session).get_or_create(
            self.chain_id,
            address,
            symbol or ("ETH" if address == NATIVE_ADDRESS else None),
            "Ether" if address == NATIVE_ADDRESS else None,
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
        if symbol.upper() in STABLE_SYMBOLS:
            return 1.0
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
            self.memory[key] = cached.price_usd
            return cached.price_usd
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
            self.memory[key] = price
            return price
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
                self.memory[key] = price
                return price
        self.memory[key] = None
        return None


def group_transaction_legs(
    wallet: Wallet,
    normals: list[NormalTransaction],
    internals: list[InternalTransaction],
    transfers: list[tuple[TokenTransfer, Token]],
) -> dict[str, list[AssetLeg]]:
    address = wallet.address.lower()
    grouped: dict[str, list[AssetLeg]] = defaultdict(list)

    def native_leg(row: NormalTransaction | InternalTransaction) -> None:
        amount = safe_int(row.value_wei) / 10**18
        if amount <= 0:
            return
        from_address = (row.from_address or "").lower()
        to_address = (row.to_address or "").lower()
        if from_address == address:
            direction = "out"
        elif to_address == address:
            direction = "in"
        else:
            return
        grouped[row.tx_hash].append(
            AssetLeg(
                asset=NATIVE_ADDRESS,
                symbol="ETH",
                amount=amount,
                direction=direction,
                timestamp=row.timestamp,
                block_number=row.block_number,
                tx_hash=row.tx_hash,
            )
        )

    for row in normals:
        native_leg(row)
    for row in internals:
        native_leg(row)
    for transfer, token in transfers:
        amount = safe_int(transfer.raw_value) / 10 ** max(token.decimals, 0)
        if amount <= 0:
            continue
        if transfer.from_address.lower() == address:
            direction = "out"
        elif transfer.to_address.lower() == address:
            direction = "in"
        else:
            continue
        grouped[transfer.tx_hash].append(
            AssetLeg(
                asset=token.address,
                symbol=token.symbol or token.address[:10],
                amount=amount,
                direction=direction,
                timestamp=transfer.timestamp,
                block_number=transfer.block_number,
                tx_hash=transfer.tx_hash,
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


def derive_swaps(
    grouped: dict[str, list[AssetLeg]],
    resolver: PriceResolver,
    gas_by_hash: dict[str, float] | None = None,
) -> list[dict]:
    gas_by_hash = gas_by_hash or {}
    swaps = []
    for tx_hash, raw_legs in grouped.items():
        legs = aggregate_legs(raw_legs)
        outgoing = [leg for leg in legs if leg.direction == "out"]
        incoming = [leg for leg in legs if leg.direction == "in"]
        if not outgoing or not incoming:
            continue
        for leg in legs:
            leg.price_usd = resolver.resolve(
                leg.asset,
                leg.symbol,
                18,
                leg.timestamp,
                leg.block_number,
            )
        known_out = sum(leg.usd_value or 0.0 for leg in outgoing)
        known_in = sum(leg.usd_value or 0.0 for leg in incoming)
        swaps.append(
            {
                "tx_hash": tx_hash,
                "timestamp": min(leg.timestamp for leg in legs),
                "block_number": min(leg.block_number for leg in legs),
                "outgoing": [leg.__dict__ | {"usd_value": leg.usd_value} for leg in outgoing],
                "incoming": [leg.__dict__ | {"usd_value": leg.usd_value} for leg in incoming],
                "usd_out": known_out,
                "usd_in": known_in,
                "usd_value": max(known_out, known_in),
                "gas_usd": gas_by_hash.get(tx_hash, 0.0),
                "is_multi_leg": len(outgoing) > 1 or len(incoming) > 1,
            }
        )
    return sorted(swaps, key=lambda item: (item["timestamp"], item["tx_hash"]))


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
        total_out = float(swap.get("usd_out", 0.0) or 0.0)
        total_in = float(swap.get("usd_in", 0.0) or 0.0)
        notional = max(total_out, total_in)
        gas_usd = float(swap.get("gas_usd", 0.0) or 0.0)
        total_gas += gas_usd
        sold_cost = 0.0
        sold_any = False
        for leg in outgoing:
            asset = str(leg.get("asset", "")).lower()
            quantity = float(leg.get("amount", 0.0) or 0.0)
            if asset == NATIVE_ADDRESS or str(leg.get("symbol", "")).upper() in STABLE_SYMBOLS:
                cash_value = float(leg.get("usd_value", 0.0) or 0.0)
                invested += cash_value
                cash_out += cash_value
                continue
            symbols[asset] = str(leg.get("symbol", ""))
            cost, consumed = consume(asset, quantity)
            sold_cost += cost
            sold_any = sold_any or consumed > 0
        if sold_any:
            proceeds = total_in or notional
            trade_pnl = proceeds - sold_cost - gas_usd
            realized += trade_pnl
            cash_in += proceeds
            if trade_pnl > 0:
                wins += 1
            elif trade_pnl < 0:
                losses += 1
        for leg in incoming:
            asset = str(leg.get("asset", "")).lower()
            if asset == NATIVE_ADDRESS or str(leg.get("symbol", "")).upper() in STABLE_SYMBOLS:
                continue
            quantity = float(leg.get("amount", 0.0) or 0.0)
            leg_value = float(leg.get("usd_value", 0.0) or 0.0)
            if leg_value <= 0 and total_in > 0:
                leg_value = notional / max(len(incoming), 1)
            symbols[asset] = str(leg.get("symbol", ""))
            if not sold_any and total_in > 0:
                leg_value += gas_usd * (leg_value / total_in)
            add_lot(asset, quantity, leg_value)
        if not sold_any and gas_usd:
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
            "quantity": quantity, "cost_usd": cost, "symbol": symbols.get(asset, "")
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
        swaps = derive_swaps(grouped, resolver, gas_by_hash)
        pnl = compute_fifo_pnl(swaps)
        latest_prices = {}
        now = int(datetime.now(timezone.utc).timestamp())
        for asset, position in pnl["open_positions"].items():
            price = resolver.resolve(
                asset, position.get("symbol", ""), 18, now, as_of_block
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
        trade_values = [float(item.get("usd_value", 0.0)) for item in swaps]
        swap_days = {
            datetime.fromtimestamp(item["timestamp"], timezone.utc).date()
            for item in swaps
            if item["timestamp"]
        }
        token_assets = {
            leg["asset"]
            for swap in swaps
            for leg in [*swap["outgoing"], *swap["incoming"]]
            if leg["asset"] != NATIVE_ADDRESS
        }
        known_prices = sum(
            leg.get("price_usd") is not None
            for swap in swaps
            for leg in [*swap["outgoing"], *swap["incoming"]]
        )
        total_legs = sum(
            len(swap["outgoing"]) + len(swap["incoming"]) for swap in swaps
        )
        total_out = sum(float(item.get("usd_out", 0.0)) for item in swaps)
        total_in = sum(float(item.get("usd_in", 0.0)) for item in swaps)
        total_gas = sum(float(item.get("gas_usd", 0.0)) for item in swaps)
        token_volume: dict[str, float] = defaultdict(float)
        stable_volume = 0.0
        for swap in swaps:
            for leg in [*swap["outgoing"], *swap["incoming"]]:
                value = float(leg.get("usd_value", 0.0) or 0.0)
                symbol = str(leg.get("symbol", ""))
                token_volume[symbol] += value
                if symbol.upper() in STABLE_SYMBOLS:
                    stable_volume += value
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
            "swap_count": len(swaps),
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
                total_gas * 100 / (total_out + total_in)
                if total_out + total_in else 0.0
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
