import asyncio
import os
from datetime import datetime

import requests

from .settings import CONFIGS, Logger

logger = Logger()


class CoingeckoClient:
    """
    Coingecko API client for cryptocurrency data

    Args:
    - `API_KEY (str)`: Coingecko API key for authentication
    """

    def __init__(self, API_KEY: str | None = None) -> None:
        """
        Initialize CoingeckoClient with API key

        Documentation: https://docs.coingecko.com/v3.0.1/reference/introduction
        """
        self.API_KEY: str = API_KEY or CONFIGS.COINGECKO.API_KEY
        self.base_url: str = "https://api.coingecko.com/api/v3"

        self.headers: dict = {
            "x-cg-demo-api-key": self.API_KEY,
            "accept": "application/json",
        }
        # default per-request delay (seconds) to avoid rate-limits; can override per-call
        try:
            self.rate_delay: float = float(os.getenv("COINGECKO_DELAY", "0.6"))
        except Exception:
            self.rate_delay = 0.6
        # simple global throttle per-process
        try:
            self._min_interval: float = float(os.getenv("COINGECKO_MIN_INTERVAL", "1"))
        except Exception:
            self._min_interval = 1
        self._last_request_ts: float | None = None

    def get_token_data(self, network: str = "ethereum", token_address: str | None = None) -> dict:
        """
        Get token data from Coingecko

        About endpoint: https://docs.coingecko.com/v3.0.1/reference/coins-contract-address

        Args:
        - `network (str)`: Network to get token data for
        - `token_address (str)`: Token address to get data for

        Returns:
        - `dict`: Token data
        """
        if not token_address:
            raise ValueError("Token address is required")
        try:
            response = requests.get(
                f"{self.base_url}/coins/{network}/contract/{token_address}",
                headers=self.headers,
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting token data: {e}")
            raise ValueError(f"Error getting token data: {e}")

    def get_networks_map(self) -> dict:
        try:
            response = requests.get(f"{self.base_url}/asset_platforms", headers=self.headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting networks map: {e}")
            raise ValueError(f"Error getting networks map: {e}")

    def get_coins_id(self) -> dict:
        """
        Get coins id from Coingecko
        """
        try:
            response = requests.get(f"{self.base_url}/coins/list", headers=self.headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting coins id: {e}")
            raise ValueError(f"Error getting coins id: {e}")

    def get_historical_price(
        self, network: str = "ethereum", token_address: str | None = None, start_date: str | None = None, end_date: str | None = None
    ) -> dict:
        """
        Get historical price from Coingecko

        About endpoint: https://docs.coingecko.com/v3.0.1/reference/contract-address-market-chart

        Args:
        - `network (str)`: Network to get historical price for
        - `token_address (str)`: Token address to get historical price for
        - `start_date (str)`: Start date to get historical price for
        - `end_date (str)`: End date to get historical price for

        Returns:
        - `dict`: Historical price data
        """

        if not token_address:
            raise ValueError("Token address is required")
        if not start_date:
            raise ValueError("Start date is required")
        if not end_date:
            raise ValueError("End date is required")

        start_dt = datetime.strptime(start_date, "%d-%m-%Y")
        end_dt = datetime.strptime(end_date, "%d-%m-%Y")
        start_ts = int(start_dt.timestamp())
        end_ts = int(end_dt.timestamp())

        if end_ts <= start_ts:
            logger.warning(f"End date is before start date for {token_address}")
            raise ValueError(f"End date is before start date for {token_address}")

        start_timestamp = str(start_ts)
        end_timestamp = str(end_ts)

        try:
            # throttle
            import time

            now = time.time()
            if self._last_request_ts is not None:
                wait = self._min_interval - (now - self._last_request_ts)
                if wait > 0:
                    time.sleep(wait)
            response = requests.get(
                f"{self.base_url}/coins/{network}/contract/{token_address}/market_chart/range",
                headers=self.headers,
                params={"vs_currency": "usd", "from": start_timestamp, "to": end_timestamp},
                timeout=30,
            )
            response.raise_for_status()
            self._last_request_ts = time.time()
            return response.json()
        except requests.exceptions.RequestException as e:
            err_payload = {"error": None}
            try:
                if hasattr(e, "response") and e.response is not None:
                    err_payload = e.response.json()
                else:
                    err_payload = {"error": str(e)}
            except Exception:
                err_payload = {"error": str(e)}
            # backoff on 429
            if err_payload.get("error") and ("429" in str(err_payload["error"]) or "Too Many Requests" in str(err_payload["error"])):
                import time as _t

                _t.sleep(2.0)
            logger.error(f"Error getting historical price for {token_address}: {err_payload.get('error')}")
            return err_payload

    def get_historical_price_by_id(self, token_id: str, start_date: str | None = None, end_date: str | None = None) -> dict:
        """
        Get historical price from Coingecko by id

        About endpoint: https://docs.coingecko.com/v3.0.1/reference/coins-id-market-chart-range

        Args:
        - `token_id (str)`: Token id to get historical price for
        - `start_date (str)`: Start date to get historical price for
        - `end_date (str)`: End date to get historical price for

        Returns:
        - `dict`: Historical price data
        """
        if not token_id:
            raise ValueError("Token id is required")
        if not start_date:
            raise ValueError("Start date is required")
        if not end_date:
            raise ValueError("End date is required")

        start_dt = datetime.strptime(start_date, "%d-%m-%Y")
        end_dt = datetime.strptime(end_date, "%d-%m-%Y")

        start_ts = int(start_dt.timestamp())
        end_ts = int(end_dt.timestamp())

        start_timestamp = str(start_ts)
        end_timestamp = str(end_ts)
        try:
            # throttle
            import time

            now = time.time()
            if self._last_request_ts is not None:
                wait = self._min_interval - (now - self._last_request_ts)
                if wait > 0:
                    time.sleep(wait)
            response = requests.get(
                f"{self.base_url}/coins/{token_id}/market_chart/range",
                headers=self.headers,
                params={"vs_currency": "usd", "from": start_timestamp, "to": end_timestamp},
                timeout=30,
            )
            response.raise_for_status()
            self._last_request_ts = time.time()
            return response.json()
        except requests.exceptions.RequestException as e:
            err_payload = {"error": None}
            try:
                if hasattr(e, "response") and e.response is not None:
                    err_payload = e.response.json()
                else:
                    err_payload = {"error": str(e)}
            except Exception:
                err_payload = {"error": str(e)}
            if err_payload.get("error") and ("429" in str(err_payload["error"]) or "Too Many Requests" in str(err_payload["error"])):
                import time as _t

                _t.sleep(2.0)
            logger.error(f"Error getting historical price for {token_id}: {err_payload.get('error')}")
            return err_payload

    async def get_historical_price_async(
        self,
        network: str = "ethereum",
        token_address: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        delay_sec: float | None = None,
        retries: int = 3,
        backoff: float = 2.0,
    ) -> dict:
        """
        Async variant with pre-request delay and simple retry/backoff for 429/5xx.
        """
        current_delay = self.rate_delay if delay_sec is None else delay_sec
        attempt = 0
        while True:
            attempt += 1
            await asyncio.sleep(current_delay)
            try:
                return await asyncio.to_thread(self.get_historical_price, network, token_address, start_date, end_date)
            except Exception as e:
                msg = str(e)
                transient = ("429" in msg) or ("502" in msg) or ("503" in msg) or ("504" in msg) or ("Too Many Requests" in msg)
                if attempt <= retries and transient:
                    current_delay = max(current_delay * backoff, self.rate_delay)
                    continue
                raise
