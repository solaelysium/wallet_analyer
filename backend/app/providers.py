from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

import requests

from .config import Settings
from .key_pool import KeyPool, RateLimitedError


class TooManyTransactionsError(Exception):
    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__(f"Wallet has more than allowed transactions ({count})")


class ResultWindowTooLargeError(RuntimeError):
    """Etherscan page*offset window exceeded; caller should slide startblock."""


# Etherscan account API: page * offset must be <= 10000 per request window.
ETHERSCAN_MAX_RESULT_WINDOW = 10_000


class ExplorerProvider(ABC):
    @abstractmethod
    def normal_transactions(
        self,
        address: str,
        cancel_check=None,
        start_block: int = 0,
        max_rows: int | None = None,
    ) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def internal_transactions(
        self,
        address: str,
        cancel_check=None,
        start_block: int = 0,
        max_rows: int | None = None,
    ) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def token_transfers(
        self,
        address: str,
        cancel_check=None,
        start_block: int = 0,
        max_rows: int | None = None,
    ) -> list[dict]:
        raise NotImplementedError


class RpcProvider(ABC):
    @abstractmethod
    def latest_block(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def is_wallet(self, address: str) -> bool | None:
        raise NotImplementedError

    @abstractmethod
    def balance_wei(self, address: str, block: str = "latest") -> int:
        raise NotImplementedError

    def token_price_usd_at_block(
        self, token_address: str, block_number: int
    ) -> float | None:
        return None

    def token_decimals(
        self, token_address: str, block: int | str = "latest"
    ) -> int | None:
        return None

    def token_balance(
        self,
        token_address: str,
        wallet_address: str,
        block: int | str = "latest",
    ) -> int | None:
        return None


class HistoricalPriceProvider(ABC):
    @abstractmethod
    def prices(
        self,
        platform: str,
        token_address: str | None,
        coin_id: str | None,
        start_timestamp: int,
        end_timestamp: int,
    ) -> list[tuple[int, float]]:
        raise NotImplementedError


class EtherscanV2Provider(ExplorerProvider):
    base_url = "https://api.etherscan.io/v2/api"

    def __init__(
        self,
        key_pool: KeyPool,
        *,
        chain_id: int = 1,
        timeout: float = 30.0,
        page_size: int = 1000,
    ) -> None:
        self.key_pool = key_pool
        self.chain_id = chain_id
        self.timeout = timeout
        self.page_size = min(max(page_size, 1), 10_000)

    def _request(self, key: str, params: dict) -> list[dict]:
        try:
            response = requests.get(
                self.base_url,
                params={"chainid": self.chain_id, "apikey": key, **params},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Etherscan request failed: {type(exc).__name__}"
            ) from exc
        if response.status_code == 429:
            raise RateLimitedError("Etherscan returned HTTP 429")
        if not response.ok:
            raise RuntimeError(f"Etherscan returned HTTP {response.status_code}")
        payload = response.json()
        result = payload.get("result")
        status = str(payload.get("status", ""))
        message = f"{payload.get('message', '')} {result if isinstance(result, str) else ''}"
        if status == "0":
            lowered = message.lower()
            if "no transactions" in lowered or result == []:
                return []
            if "rate limit" in lowered or "max rate" in lowered:
                raise RateLimitedError(message.strip())
            if "result window is too large" in lowered:
                raise ResultWindowTooLargeError(message.strip())
            raise RuntimeError(f"Etherscan error: {message.strip()}")
        if not isinstance(result, list):
            raise RuntimeError("Etherscan returned an unexpected response")
        return result

    def _paged(
        self,
        action: str,
        address: str,
        cancel_check=None,
        start_block: int = 0,
        max_rows: int | None = None,
    ) -> list[dict]:
        if max_rows is not None and max_rows < 0:
            raise TooManyTransactionsError(0)
        page = 1
        cursor_block = max(0, int(start_block))
        rows: list[dict] = []
        seen: set[tuple] = set()
        while True:
            if cancel_check:
                cancel_check()
            if max_rows is not None and len(rows) > max_rows:
                raise TooManyTransactionsError(len(rows))
            if page * self.page_size > ETHERSCAN_MAX_RESULT_WINDOW:
                if not rows:
                    break
                next_block = int(rows[-1].get("blockNumber") or cursor_block)
                if next_block <= cursor_block:
                    next_block = cursor_block + 1
                cursor_block = next_block
                page = 1
                continue
            try:
                chunk = self.key_pool.call(
                    "etherscan",
                    self._request,
                    {
                        "module": "account",
                        "action": action,
                        "address": address,
                        "startblock": cursor_block,
                        "endblock": 99_999_999,
                        "page": page,
                        "offset": self.page_size,
                        "sort": "asc",
                    },
                )
            except ResultWindowTooLargeError:
                if not rows:
                    raise TooManyTransactionsError(
                        ETHERSCAN_MAX_RESULT_WINDOW + 1
                    )
                next_block = int(rows[-1].get("blockNumber") or cursor_block)
                if next_block <= cursor_block:
                    next_block = cursor_block + 1
                cursor_block = next_block
                page = 1
                continue
            added = 0
            for row in chunk:
                identity = (
                    row.get("hash"),
                    row.get("traceId", ""),
                    row.get("logIndex", row.get("transactionIndex", "")),
                )
                if identity not in seen:
                    seen.add(identity)
                    rows.append(row)
                    added += 1
                    if max_rows is not None and len(rows) > max_rows:
                        raise TooManyTransactionsError(len(rows))
            if len(chunk) < self.page_size:
                break
            if added == 0:
                # Full page of duplicates from an overlapping block window.
                last_block = int(
                    chunk[-1].get("blockNumber") or cursor_block
                )
                cursor_block = max(cursor_block + 1, last_block + 1)
                page = 1
                continue
            if max_rows is not None and len(rows) >= max_rows:
                raise TooManyTransactionsError(len(rows) + 1)
            page += 1
        return rows

    def normal_transactions(
        self,
        address: str,
        cancel_check=None,
        start_block: int = 0,
        max_rows: int | None = None,
    ) -> list[dict]:
        return self._paged(
            "txlist", address, cancel_check, start_block, max_rows
        )

    def internal_transactions(
        self,
        address: str,
        cancel_check=None,
        start_block: int = 0,
        max_rows: int | None = None,
    ) -> list[dict]:
        return self._paged(
            "txlistinternal", address, cancel_check, start_block, max_rows
        )

    def token_transfers(
        self,
        address: str,
        cancel_check=None,
        start_block: int = 0,
        max_rows: int | None = None,
    ) -> list[dict]:
        return self._paged(
            "tokentx", address, cancel_check, start_block, max_rows
        )


class InfuraProvider(RpcProvider):
    uniswap_v2_factory = "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f"
    weth = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
    usdc = "0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    native_address = "0x0000000000000000000000000000000000000000"

    def __init__(self, key_pool: KeyPool, *, timeout: float = 30.0) -> None:
        self.key_pool = key_pool
        self.timeout = timeout

    def _rpc(self, key: str, method: str, params: list) -> object:
        try:
            response = requests.post(
                f"https://mainnet.infura.io/v3/{key}",
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Infura request failed: {type(exc).__name__}") from exc
        if response.status_code == 429:
            raise RateLimitedError("Infura returned HTTP 429")
        if not response.ok:
            raise RuntimeError(f"Infura returned HTTP {response.status_code}")
        payload = response.json()
        if payload.get("error"):
            message = str(payload["error"])
            if "rate" in message.lower() or "limit" in message.lower():
                raise RateLimitedError(message)
            raise RuntimeError(f"Infura RPC error: {message}")
        return payload.get("result")

    def rpc(self, method: str, params: list) -> object:
        return self.key_pool.call("infura", self._rpc, method, params)

    def latest_block(self) -> int:
        return int(str(self.rpc("eth_blockNumber", [])), 16)

    def is_wallet(self, address: str) -> bool | None:
        if not address:
            raise ValueError("Адрес не указан")
        try:
            code = str(self.rpc("eth_getCode", [address, "latest"]))
            return len(code.removeprefix("0x")) <= 46
        except Exception:
            return None

    def balance_wei(self, address: str, block: str = "latest") -> int:
        return int(str(self.rpc("eth_getBalance", [address, block])), 16)

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str:
        block_tag = hex(block) if isinstance(block, int) else block
        return str(self.rpc("eth_call", [{"to": to, "data": data}, block_tag]))

    def token_decimals(
        self, address: str, block: int | str = "latest"
    ) -> int | None:
        try:
            value = int(self.eth_call(address, "0x313ce567", block), 16)
            return value if 0 <= value <= 255 else None
        except Exception:
            return None

    def token_balance(
        self,
        token_address: str,
        wallet_address: str,
        block: int | str = "latest",
    ) -> int | None:
        try:
            data = "0x70a08231" + wallet_address[2:].lower().zfill(64)
            return int(self.eth_call(token_address, data, block), 16)
        except Exception:
            return None

    @staticmethod
    def _encode_address(address: str) -> str:
        return address.lower().removeprefix("0x").rjust(64, "0")

    @staticmethod
    def _decode_address(value: str) -> str | None:
        raw = value.removeprefix("0x")
        if len(raw) < 40:
            return None
        address = "0x" + raw[-40:]
        return None if int(address, 16) == 0 else address.lower()

    def _v2_pair(self, token_a: str, token_b: str, block_number: int) -> str | None:
        data = (
            "0xe6a43905"
            + self._encode_address(token_a)
            + self._encode_address(token_b)
        )
        result = self.eth_call(self.uniswap_v2_factory, data, block_number)
        return self._decode_address(result)

    def _v2_quote(
        self,
        base: str,
        quote: str,
        base_decimals: int,
        quote_decimals: int,
        block_number: int,
    ) -> float | None:
        pair = self._v2_pair(base, quote, block_number)
        if pair is None:
            return None
        token0 = self._decode_address(
            self.eth_call(pair, "0x0dfe1681", block_number)
        )
        reserves_hex = self.eth_call(pair, "0x0902f1ac", block_number).removeprefix(
            "0x"
        )
        if token0 is None or len(reserves_hex) < 128:
            return None
        reserve0 = int(reserves_hex[:64], 16)
        reserve1 = int(reserves_hex[64:128], 16)
        if reserve0 <= 0 or reserve1 <= 0:
            return None
        if token0 == base.lower():
            base_reserve, quote_reserve = reserve0, reserve1
        else:
            base_reserve, quote_reserve = reserve1, reserve0
        normalized_base = base_reserve / 10**base_decimals
        normalized_quote = quote_reserve / 10**quote_decimals
        return normalized_quote / normalized_base if normalized_base else None

    def token_price_usd_at_block(
        self, token_address: str, block_number: int
    ) -> float | None:
        token = (
            self.weth
            if token_address.lower() == self.native_address
            else token_address
        )
        if token.lower() == self.usdc.lower():
            return 1.0
        try:
            weth_usd = self._v2_quote(
                self.weth, self.usdc, 18, 6, block_number
            )
            if token.lower() == self.weth.lower():
                return weth_usd
            decimals = self.token_decimals(token, block_number)
            if decimals is None:
                return None
            direct = self._v2_quote(
                token, self.usdc, decimals, 6, block_number
            )
            if direct is not None:
                return direct
            token_weth = self._v2_quote(
                token, self.weth, decimals, 18, block_number
            )
            if token_weth is not None and weth_usd is not None:
                return token_weth * weth_usd
        except (RuntimeError, ValueError, TypeError):
            return None
        return None


class CoinGeckoProvider(HistoricalPriceProvider):
    base_url = "https://api.coingecko.com/api/v3"

    def __init__(self, key_pool: KeyPool, *, timeout: float = 30.0) -> None:
        self.key_pool = key_pool
        self.timeout = timeout

    def _request(self, key: str, path: str, params: dict) -> dict:
        headers = {"accept": "application/json"}
        if key:
            headers["x-cg-demo-api-key"] = key
        try:
            response = requests.get(
                f"{self.base_url}/{path}",
                headers=headers,
                params=params,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise RuntimeError(
                f"CoinGecko request failed: {type(exc).__name__}"
            ) from exc
        if response.status_code == 429:
            raise RateLimitedError("CoinGecko returned HTTP 429")
        if not response.ok:
            raise RuntimeError(f"CoinGecko returned HTTP {response.status_code}")
        return response.json()

    def prices(
        self,
        platform: str,
        token_address: str | None,
        coin_id: str | None,
        start_timestamp: int,
        end_timestamp: int,
    ) -> list[tuple[int, float]]:
        if coin_id:
            path = f"coins/{coin_id}/market_chart/range"
        elif token_address:
            path = f"coins/{platform}/contract/{token_address}/market_chart/range"
        else:
            raise ValueError("token_address or coin_id is required")
        payload = self.key_pool.call(
            "coingecko",
            self._request,
            path,
            {
                "vs_currency": "usd",
                "from": start_timestamp,
                "to": end_timestamp,
            },
        )
        return [
            (int(item[0]) // 1000, float(item[1]))
            for item in payload.get("prices", [])
            if isinstance(item, list) and len(item) >= 2
        ]


def build_key_pool(
    settings: object, keys: dict[str, list[str]] | None = None
) -> KeyPool:
    return KeyPool(
        keys
        or {
            "etherscan": getattr(settings, "etherscan_api_keys", []),
            "infura": getattr(settings, "infura_api_keys", []),
            "coingecko": getattr(settings, "coingecko_api_keys", []),
        },
        concurrency=settings.key_concurrency,
        rps={
            "etherscan": settings.etherscan_rps,
            "infura": settings.infura_rps,
            "coingecko": settings.coingecko_rps,
        },
        cooldown_seconds=settings.provider_cooldown_seconds,
        max_retries=settings.provider_max_retries,
    )


class ProviderBundle:
    def __init__(
        self,
        explorer: ExplorerProvider,
        rpc: RpcProvider,
        prices: HistoricalPriceProvider,
        key_pool: KeyPool,
    ) -> None:
        self.explorer = explorer
        self.rpc = rpc
        self.prices = prices
        self.key_pool = key_pool

    @classmethod
    def from_settings(
        cls, settings: object, keys: dict[str, list[str]] | None = None
    ) -> "ProviderBundle":
        pool = build_key_pool(settings, keys)
        return cls(
            explorer=EtherscanV2Provider(pool, timeout=settings.provider_timeout_seconds),
            rpc=InfuraProvider(pool, timeout=settings.provider_timeout_seconds),
            prices=CoinGeckoProvider(pool, timeout=settings.provider_timeout_seconds),
            key_pool=pool,
        )

    def reconfigure(self, settings: object, rows: list[object]) -> None:
        keys: dict[str, list[dict]] = {
            "etherscan": [],
            "infura": [],
            "coingecko": [],
        }
        for row in rows:
            keys.setdefault(row.service, []).append(
                {
                    "value": row.value,
                    "label": row.label,
                    "enabled": row.enabled,
                }
            )
        self.key_pool.reconfigure(
            keys,
            concurrency=settings.key_concurrency,
            rps={
                "etherscan": settings.etherscan_rps,
                "infura": settings.infura_rps,
                "coingecko": settings.coingecko_rps,
            },
            cooldown_seconds=settings.provider_cooldown_seconds,
            max_retries=settings.provider_max_retries,
        )
        self.explorer.timeout = settings.provider_timeout_seconds
        self.rpc.timeout = settings.provider_timeout_seconds
        self.prices.timeout = settings.provider_timeout_seconds


def timestamp_to_utc(timestamp: int) -> datetime:
    return datetime.fromtimestamp(timestamp, timezone.utc)
