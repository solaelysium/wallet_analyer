import time
from datetime import datetime, timedelta
from statistics import mean, median

from .database_helper import DB
from .settings import Logger
from .web3_client import WebClient

logger = Logger()


class DataHandler:
    def __init__(self, web_client: WebClient):
        self.web_client = web_client
        # in-memory cache: (blockchain, addr_key, day_start_ms) -> float
        self._price_cache = {}

    def get_wallet_age(self, ERC20_TXS: list[dict]) -> float:
        """
        Get the age of the wallet (from first ERC20 transfer)

        Args:
        - `ERC20_TXS (list[dict])`: List of ERC20 transactions info

        Returns:
        - `float`: Age of the wallet, days
        """
        first = ERC20_TXS[0]
        start_date = datetime.fromtimestamp(int(first["timeStamp"]))
        current_date = datetime.now()
        wallet_age = (current_date - start_date).days
        return wallet_age

    def get_frequency_of_transactions(self, ERC20_TXS: list[dict]) -> float:
        """
        Get the average frequency of transactions

        Args:
        - `ERC20_TXS (list[dict])`: List of ERC20 transactions info

        Returns:
        - `float`: Average frequency of transactions, transactions/day
        """
        first = ERC20_TXS[0]
        last = ERC20_TXS[-1]
        start_date = datetime.fromtimestamp(int(first["timeStamp"]))
        end_date = datetime.fromtimestamp(int(last["timeStamp"]))

        days = (end_date - start_date).days

        if days == 0:
            logger.warning("Count of days is 0")
            return 0

        frequency = len(ERC20_TXS) / days
        return round(frequency, 5)

    def get_days_since_last_transaction(self, ERC20_TXS: list[dict]) -> float:
        """
        Get the days since the last transaction

        Args:
        - `ERC20_TXS (list[dict])`: List of ERC20 transactions info ascending by block number

        Returns:
        - `float`: Days since the last transaction
        """

        last = ERC20_TXS[-1]
        return (datetime.now() - datetime.fromtimestamp(int(last["timeStamp"]))).days

    # TODO: Add suppport for other networks
    def get_price_token(
        self,
        network: str | None = None,
        blockchain: str = "ethereum",
        token_address: str | None = None,
        token_id: str | None = None,
        timestamp: int | None = None,
    ) -> float | None:
        """
        Get the price of the token.

        If the price is not found, it will be fetched from Coingecko.

        If the price is not found in Coingecko, it will be returned None and added to the excluded tokens table

        Args:
        - `network (str)`: Network of the token (optional)
        - `blockchain (str)`: Blockchain key (alias to network); default: "ethereum"
        - `token_address (str)`: Address of the token
        - `timestamp (int)`: Timestamp of the transaction

        Returns:
        - `float`: Price of the token
        - `None`: If the price is not found
        """
        if token_address is None:
            raise ValueError("Token address is required")
        if timestamp is None:
            raise ValueError("Timestamp is required")

        addr_key = token_address.lower()
        target_ms = int(timestamp) * 1000
        dt = datetime.fromtimestamp(int(timestamp))
        day_start_dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        day_start_ms = int(day_start_dt.timestamp() * 1000)
        day_end_ms = day_start_ms + 86_400_000 - 1

        cache_key = (blockchain, addr_key, day_start_ms)
        cached = self._price_cache.get(cache_key)
        if cached is not None:
            return cached

        if token_id is None:
            # 1) Exclusions
            try:
                if DB.is_excluded(addr_key, blockchain=blockchain):
                    return None
            except Exception:
                pass

            # 2) Try existing quotes
            try:
                got = DB.query_nearest_price(addr_key, day_start_ms, day_end_ms, target_ms, blockchain=blockchain)
                if got is not None:
                    price = float(got)
                    self._price_cache[cache_key] = price
                    return price
            except Exception:
                pass

        # 3) Fetch and backfill
        # Strategy:
        # - If token has no quotes at all -> fetch wide (today-360d .. today)
        # - Else -> fetch incremental recent window (e.g., last 7 days) to fill gaps
        end_dt = datetime.now()
        has_any = False
        try:
            has_any = DB.has_any_quotes(addr_key, blockchain=blockchain)
        except Exception:
            pass

        if has_any:
            start_dt = end_dt - timedelta(days=7)
        else:
            start_dt = end_dt - timedelta(days=360)

        start_date = start_dt.strftime("%d-%m-%Y")
        end_date = end_dt.strftime("%d-%m-%Y")

        try:
            cg = self.web_client.coingecko_client

            active_network = network or blockchain or getattr(getattr(self, "web_client", None), "NETWORK", "ethereum")

            if token_id is not None:
                payload = cg.get_historical_price_by_id(token_id=token_id, start_date=start_date, end_date=end_date)
            else:
                payload = cg.get_historical_price(network=active_network, token_address=addr_key, start_date=start_date, end_date=end_date)

            # Error handling
            # TODO: Add support for other reasons (for example: 'Exceed time range')
            if payload.get("error") is not None:
                reason = payload["error"]
                # do not exclude on transient/server-side errors like 429
                if isinstance(reason, str) and ("429" not in reason and "Too Many Requests" not in reason):
                    try:
                        DB.add_excluded(addr_key, reason, blockchain=blockchain)
                    except Exception:
                        pass
                    return None
                # TODO: dict reasons are not handled for now

                return None

            prices = payload.get("prices", [])
            rows = []

            # Record prices
            for p in prices:
                if not p or len(p) < 2:
                    continue
                ts_ms, px = int(p[0]), float(p[1])
                rows.append((ts_ms, px))
            if rows:
                try:
                    DB.insert_quotes_rows(addr_key, rows, blockchain=blockchain)
                except Exception:
                    pass
                try:
                    got = DB.query_nearest_price(addr_key, day_start_ms, day_end_ms, target_ms, blockchain=blockchain)
                    if got is not None:
                        price = float(got)
                        self._price_cache[cache_key] = price
                        return price
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Coingecko fetch failed for {addr_key}: {e}")

        return None

    def get_stats(self, wallet_address: str, wallet_balance: float, ERC20_TXS: list[dict] = None) -> dict:
        """
        Get full stats of the account

        Args:
        - `wallet_address (str)`: Address of the wallet
        - `ERC20_TXS (list[dict])`: List of ERC20 transactions info ascending by block number
        - `balances_info (list[dict])`: List of token balances of the account

        Returns:
        - `dict`: Stats of the account
        """

        # Guards
        if not ERC20_TXS:
            logger.warning("No transactions to analyze")
            return None

        if self.web_client is None:
            logger.error("web_client is required for swap analysis")
            raise ValueError("web_client is required for swap analysis")

        # Get lower case address
        wallet_address = wallet_address.lower()

        # Define counting data
        counting_data = {
            "total_out, $": 0.0,
            "total_in, $": 0.0,
            "total_gas, $": 0.0,
        }

        LENGTH = len(ERC20_TXS) - 1
        i = 0

        # Aggregation helpers
        swap_days = set()
        trade_values = []
        token_volume_by_symbol = {}
        unique_tokens = set()
        gas_values = []
        swaps_count = 0

        while i <= LENGTH - 4:
            tx = ERC20_TXS[i]
            tx_next = ERC20_TXS[i + 1] if i + 1 <= LENGTH else None

            # * Some way for check SWAP:
            # * 1) Check equal blocknumber for several transactions. If 'swap', 'execute' or '' in 'functionName'.lower() ---> Check by BOTH transactions
            # * 2) If this block number does not have an equal pair, then if 'swap' in 'functionName'.lower() ---> Check by THIS transaction
            # ! ALL WAYS MUST BE CHECKED BY THAT RULE: RESULT FIELDS 'FROM ADDRESS' AND 'TO ADDRESS' MUST BE EQUAL TO 'WALLET ADDRESS'

            blocknumber = int(tx["blockNumber"])
            blocknumber_next = int(tx_next["blockNumber"]) if tx_next else None

            if tx_next is not None and blocknumber == blocknumber_next:
                if "swap" in tx["functionName"].lower() or "execute" in tx["functionName"].lower() or "" == tx["functionName"].lower():
                    from_addr = tx["from"].lower()
                    to_addr = tx_next["to"].lower()
                    if from_addr != wallet_address and to_addr != wallet_address:
                        i += 2
                        continue

                    token_contract_out: str = tx["contractAddress"]
                    token_symbol_out: str = tx["tokenSymbol"]
                    token_decimal_out: int = int(tx["tokenDecimal"])
                    amount_out: float = int(tx["value"]) / 10**token_decimal_out

                    token_contract_in: str = tx_next["contractAddress"]
                    token_symbol_in: str = tx_next["tokenSymbol"]
                    token_decimal_in: int = int(tx_next["tokenDecimal"])
                    amount_in: float = int(tx_next["value"]) / 10**token_decimal_in
                    timestamp_in: int = int(tx_next["timeStamp"])

                    gas_out: float = int(tx["gasUsed"]) * int(tx["gasPrice"]) / 10**18
                    gas_in: float = int(tx_next["gasUsed"]) * int(tx_next["gasPrice"]) / 10**18
                    gas_total: float = gas_out + gas_in

                    i += 2
                else:
                    i += 2
                    continue
            else:
                # if "swap" in tx["functionName"].lower():
                #     receipt: dict = self.web_client.get_transcation_receipt(tx_hash=tx["hash"])
                #     from_addr = receipt["from"].lower()
                #     to_addr = "0x" + receipt["logs"][-1]["topics"][2].hex().lower()[-40:]
                #     if from_addr != wallet_address and to_addr != wallet_address:
                #         i += 1
                #         continue

                #     token_contract_out: str = receipt["logs"][0]["address"]
                #     token_symbol_out: str = self.web_client.get_token_meta(token_contract_out)[0]
                #     token_decimal_out: int = self.web_client.get_token_meta(token_contract_out)[1]
                #     amount_out: float = int(receipt["logs"][0]["data"].hex(), 16) / 10**token_decimal_out

                #     token_symbol_in: str = tx["tokenSymbol"]
                #     token_decimal_in: int = int(tx["tokenDecimal"])
                #     token_contract_in: str = tx["contractAddress"]
                #     amount_in: float = int(tx["value"]) / 10**token_decimal_in
                #     timestamp_in: int = int(tx["timeStamp"])

                #     gas_total: float = int(tx["gasUsed"]) * int(tx["gasPrice"]) / 10**18

                #     i += 1
                # else:
                #     i += 1
                #     continue
                # else:
                if "swap" in tx["functionName"].lower() or "execute" in tx["functionName"].lower() or "" == tx["functionName"].lower:
                    receipt: dict = self.web_client.get_transcation_receipt(tx_hash=tx["hash"])

                    logs = receipt.get("logs", []) or []
                    if not logs:
                        i += 1
                        continue

                    ERC20_TRANSFER_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
                    transfer_logs = [
                        log
                        for log in logs
                        if isinstance(log.get("topics", []), list)
                        and len(log["topics"]) >= 3
                        and (
                            (log["topics"][0].hex().lower() if hasattr(log["topics"][0], "hex") else str(log["topics"][0]).lower())
                            == ERC20_TRANSFER_TOPIC0
                        )
                    ]
                    if not transfer_logs:
                        i += 1
                        continue

                    from_addr = (receipt.get("from") or "").lower()
                    last_to_topic = transfer_logs[-1]["topics"][2]
                    to_addr = "0x" + (last_to_topic.hex().lower()[-40:] if hasattr(last_to_topic, "hex") else str(last_to_topic).lower()[-40:])

                    if from_addr != wallet_address and to_addr != wallet_address:
                        i += 1
                        continue

                    token_contract_out: str = transfer_logs[0]["address"]
                    token_symbol_out: str = self.web_client.get_token_meta(token_contract_out)[0]
                    token_decimal_out: int = self.web_client.get_token_meta(token_contract_out)[1]
                    data_field = transfer_logs[0].get("data", "0x0")
                    try:
                        raw_hex = data_field.hex() if hasattr(data_field, "hex") else str(data_field)
                        amount_out: float = int(raw_hex, 16) / 10**token_decimal_out
                    except Exception:
                        amount_out = 0.0

                    token_symbol_in: str = tx["tokenSymbol"]
                    token_decimal_in: int = int(tx["tokenDecimal"])
                    token_contract_in: str = tx["contractAddress"]
                    amount_in: float = int(tx["value"]) / 10**token_decimal_in
                    timestamp_in: int = int(tx["timeStamp"])

                    gas_total: float = int(tx["gasUsed"]) * int(tx["gasPrice"]) / 10**18

                    i += 1
                else:
                    i += 1
                    continue

            zero_addr = "0x" + "0" * 40
            try:
                price_token_out = self.get_price_token(blockchain="ethereum", token_address=token_contract_out, timestamp=timestamp_in)
            except Exception:
                price_token_out = None

            if price_token_out is None:
                # fallback: derive price at nearest block via on-chain DEX (UniV3/V2)
                try:
                    block_num = int(tx_next["blockNumber"]) if tx_next else int(tx["blockNumber"])
                    price_token_out = self.web_client.get_token_price_usd_at_block(token_contract_out, block_num)
                    logger.info(f"pool_price | out {token_contract_out} | block {block_num} | price {price_token_out}")
                    if price_token_out is not None:
                        try:
                            addr_key = token_contract_out.lower()
                            ts_ms = int(timestamp_in) * 1000
                            DB.insert_quotes_rows(addr_key, [(ts_ms, float(price_token_out))], blockchain="ethereum")
                        except Exception:
                            pass
                except Exception:
                    price_token_out = None
            # no auto-exclude here; keep processing
            try:
                price_token_in = self.get_price_token(blockchain="ethereum", token_address=token_contract_in, timestamp=timestamp_in)
            except Exception:
                price_token_in = None
            if price_token_in is None:
                try:
                    block_num = int(tx_next["blockNumber"]) if tx_next else int(tx["blockNumber"])
                    price_token_in = self.web_client.get_token_price_usd_at_block(token_contract_in, block_num)
                    logger.info(f"pool_price | in {token_contract_in} | block {block_num} | price {price_token_in}")
                    if price_token_in is not None:
                        try:
                            addr_key = token_contract_in.lower()
                            ts_ms = int(timestamp_in) * 1000
                            DB.insert_quotes_rows(addr_key, [(ts_ms, float(price_token_in))], blockchain="ethereum")
                        except Exception:
                            pass
                except Exception:
                    price_token_in = None
            # no auto-exclude here; keep processing
            try:
                price_gas = self.get_price_token(blockchain="ethereum", token_id="ethereum", token_address=zero_addr, timestamp=timestamp_in)
            except Exception:
                price_gas = None

            if price_token_out is None or price_token_in is None or price_gas is None:
                # exclude tokens lacking price from both sources
                try:
                    zero_addr = "0x" + "0" * 40
                    if token_contract_out and token_contract_out.lower() != zero_addr:
                        DB.add_excluded(token_contract_out.lower(), "no_price: cg_and_onchain", blockchain="ethereum")
                    if token_contract_in and token_contract_in.lower() != zero_addr:
                        DB.add_excluded(token_contract_in.lower(), "no_price: cg_and_onchain", blockchain="ethereum")
                except Exception:
                    pass
                continue

            offer_out_usd = price_token_out * amount_out
            offer_in_usd = price_token_in * amount_in
            gas_usd = gas_total * price_gas

            counting_data["total_out, $"] += offer_out_usd
            counting_data["total_in, $"] += offer_in_usd
            counting_data["total_gas, $"] += gas_usd

            swaps_count += 1
            swap_days.add(datetime.fromtimestamp(timestamp_in).date())
            trade_values.append(max(offer_out_usd, offer_in_usd))
            token_volume_by_symbol[token_symbol_out] = token_volume_by_symbol.get(token_symbol_out, 0.0) + offer_out_usd
            token_volume_by_symbol[token_symbol_in] = token_volume_by_symbol.get(token_symbol_in, 0.0) + offer_in_usd
            unique_tokens.add(token_contract_out)
            unique_tokens.add(token_contract_in)
            gas_values.append(gas_usd)

        # * Calculate some stats
        days_since_last_transaction = self.get_days_since_last_transaction(ERC20_TXS)

        zero_addr = "0x" + "0" * 40
        yesterday_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        price_eth = self.get_price_token(
            blockchain="ethereum",
            token_id="ethereum",
            token_address=zero_addr,
            timestamp=int(yesterday_start.timestamp()),
        )

        if price_eth is None:
            logger.warning("Price of ETH is not found")
            return None

        wallet_balance = wallet_balance * price_eth

        # * Derived simple metrics
        dex_days_active = len(swap_days)
        txs_per_active_day = round(swaps_count / dex_days_active, 5) if dex_days_active else 0.0

        avg_trade = round(mean(trade_values), 5) if trade_values else 0.0
        median_trade = round(median(trade_values), 5) if trade_values else 0.0
        max_trade = round(max(trade_values), 5) if trade_values else 0.0

        unique_tokens_traded = len(unique_tokens)

        total_token_volume = sum(token_volume_by_symbol.values())
        if total_token_volume > 0:
            top_token_symbol, top_token_volume = max(token_volume_by_symbol.items(), key=lambda kv: kv[1])
            top_token_share = round(top_token_volume * 100.0 / total_token_volume, 5)
        else:
            top_token_symbol, top_token_share = "", 0.0

        STABLES = {"USDT", "USDC", "DAI", "TUSD", "BUSD", "USDP", "FRAX"}
        stable_volume = sum(v for s, v in token_volume_by_symbol.items() if s in STABLES)
        stable_volume_share = round(stable_volume * 100.0 / total_token_volume, 5) if total_token_volume else 0.0

        avg_gas_per_swap = round(mean(gas_values), 5) if gas_values else 0.0
        denom = counting_data["total_out, $"] + counting_data["total_in, $"]
        gas_share = round(counting_data["total_gas, $"] * 100.0 / denom, 5) if denom else 0.0

        # Core metrics for df (no derived ratios)
        descriptive_data = {
            # * Account info
            "wallet_age": self.get_wallet_age(ERC20_TXS),
            "frequency_of_transactions": self.get_frequency_of_transactions(ERC20_TXS),
            "last_activity": days_since_last_transaction,
            "total_balance": wallet_balance,
            # * Counting data
            "total_out, $": counting_data["total_out, $"],
            "total_in, $": counting_data["total_in, $"],
            "total_gas, $": counting_data["total_gas, $"],
            "count_ERC20_TXS": LENGTH,
            # * DEX activity
            "dex_days_active": dex_days_active,
            "txs_per_active_day(dex)": txs_per_active_day,
            # * Trade size stats
            "avg_trade, $": avg_trade,
            "median_trade, $": median_trade,
            "max_trade, $": max_trade,
            # * Tokens
            "unique_tokens_traded": unique_tokens_traded,
            "top_token_symbol": top_token_symbol,
            "top_token_share, %": top_token_share,
            "stable_volume_share, %": stable_volume_share,
            # * Gas
            "avg_gas_per_swap, $": avg_gas_per_swap,
            "gas_share, %": gas_share,
        }

        # Aggregate data
        data = {"address": wallet_address} | descriptive_data

        return data
