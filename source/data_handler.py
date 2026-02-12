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

    def get_wallet_age(self, ERC20_TXS: list[dict] | None, internal_txs: list[dict] | None = None) -> float:
        """
        Get the age of the wallet (from first tx across ERC20 + internal)

        Args:
        - `ERC20_TXS (list[dict])`: List of ERC20 transactions info
        - `internal_txs (list[dict])`: List of internal transactions info

        Returns:
        - `float`: Age of the wallet, days
        """
        txs = [tx for tx in (ERC20_TXS or []) if tx] + [tx for tx in (internal_txs or []) if tx]
        if not txs:
            return 0
        timestamps = [int(tx.get("timeStamp", 0) or 0) for tx in txs]
        timestamps = [ts for ts in timestamps if ts > 0]
        if not timestamps:
            return 0
        start_date = datetime.fromtimestamp(min(timestamps))
        return (datetime.now() - start_date).days

    def get_frequency_of_transactions(self, ERC20_TXS: list[dict] | None, internal_txs: list[dict] | None = None) -> float:
        """
        Get the average frequency of transactions

        Args:
        - `ERC20_TXS (list[dict])`: List of ERC20 transactions info
        - `internal_txs (list[dict])`: List of internal transactions info

        Returns:
        - `float`: Average frequency of transactions, transactions/day
        """
        txs = [tx for tx in (ERC20_TXS or []) if tx] + [tx for tx in (internal_txs or []) if tx]
        if not txs:
            return 0
        timestamps = [int(tx.get("timeStamp", 0) or 0) for tx in txs]
        timestamps = [ts for ts in timestamps if ts > 0]
        if not timestamps:
            return 0
        start_date = datetime.fromtimestamp(min(timestamps))
        end_date = datetime.fromtimestamp(max(timestamps))
        days = (end_date - start_date).days
        if days == 0:
            return 0
        block_numbers = {int(tx.get("blockNumber", 0) or 0) for tx in txs if int(tx.get("blockNumber", 0) or 0) > 0}
        frequency = len(block_numbers) / days
        return round(frequency, 5)

    def get_days_since_last_transaction(self, ERC20_TXS: list[dict] | None, internal_txs: list[dict] | None = None) -> float:
        """
        Get the days since the last transaction

        Args:
        - `ERC20_TXS (list[dict])`: List of ERC20 transactions info ascending by block number
        - `internal_txs (list[dict])`: List of internal transactions info

        Returns:
        - `float`: Days since the last transaction
        """
        txs = [tx for tx in (ERC20_TXS or []) if tx] + [tx for tx in (internal_txs or []) if tx]
        if not txs:
            return 0
        timestamps = [int(tx.get("timeStamp", 0) or 0) for tx in txs]
        timestamps = [ts for ts in timestamps if ts > 0]
        if not timestamps:
            return 0
        last_ts = max(timestamps)
        return (datetime.now() - datetime.fromtimestamp(last_ts)).days

    # TODO: Add suppport for other networks
    # TODO: REPAIR FUCKING FUNCTION: in exp.ipynb i had written code properly, and now i have to add referent code in this function
    def get_price_token(
        self,
        network: str | None = None,
        blockchain: str = "ethereum",
        token_address: str | None = None,
        token_id: str | None = None,
        timestamp: int | None = None,
        block_number: int | None = None,
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
        - `block_number (int)`: Block number for on-chain price fallback

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
        window_ms = 30 * 60 * 1000
        bucket = int(target_ms // window_ms)

        active_blockchain = blockchain or network or getattr(getattr(self, "web_client", None), "NETWORK", "ethereum")
        cache_key = (active_blockchain, addr_key, bucket)
        cached = self._price_cache.get(cache_key)
        if cached is not None:
            return cached

        # 1) Exclusions (only for contract-address lookups)
        if token_id is None:
            try:
                if DB.is_excluded(addr_key, blockchain=active_blockchain):
                    return None
            except Exception:
                pass

        # 2) DB lookup in +/- window
        try:
            got = DB.query_price_window(addr_key, target_ms, window_ms, blockchain=active_blockchain)
            if got is not None:
                price = float(got)
                self._price_cache[cache_key] = price
                return price
        except Exception:
            pass

        # 3) Coingecko fallback (disabled for now)
        # dt = datetime.fromtimestamp(int(timestamp))
        # start_date = (dt - timedelta(days=1)).strftime("%d-%m-%Y")
        # end_date = (dt + timedelta(days=1)).strftime("%d-%m-%Y")
        #
        # cg_reason = None
        # cg_failed = False
        # try:
        #     cg = self.web_client.coingecko_client
        #     active_network = network or active_blockchain or getattr(getattr(self, "web_client", None), "NETWORK", "ethereum")
        #
        #     if token_id is not None:
        #         payload = cg.get_historical_price_by_id(token_id=token_id, start_date=start_date, end_date=end_date)
        #     else:
        #         payload = cg.get_historical_price(network=active_network, token_address=addr_key, start_date=start_date, end_date=end_date)
        #
        #     reason = payload.get("error")
        #     if reason is not None:
        #         cg_reason = reason
        #         cg_failed = True
        #     else:
        #         prices = payload.get("prices", [])
        #         rows = []
        #         for p in prices:
        #             if not p or len(p) < 2:
        #                 continue
        #             ts_ms, px = int(p[0]), float(p[1])
        #             rows.append((ts_ms, px))
        #
        #         if rows:
        #             try:
        #                 DB.insert_quotes_rows(addr_key, rows, blockchain=active_blockchain)
        #             except Exception:
        #                 pass
        #
        #         got = DB.query_price_window(addr_key, target_ms, window_ms, blockchain=active_blockchain)
        #         if got is not None:
        #             price = float(got)
        #             self._price_cache[cache_key] = price
        #             return price
        #         cg_failed = True
        # except Exception as e:
        #     cg_reason = str(e)
        #     cg_failed = True
        #     logger.warning(f"Coingecko fetch failed for {addr_key}: {e}")

        # 4) On-chain fallback via DEX price
        if block_number is not None:
            try:
                onchain_price = self.web_client.get_token_price_usd_at_block(token_address, int(block_number))
            except Exception:
                onchain_price = None
            if onchain_price is not None:
                price = float(onchain_price)
                try:
                    DB.insert_quotes_rows(addr_key, [(target_ms, price)], blockchain=active_blockchain)
                except Exception:
                    pass
                self._price_cache[cache_key] = price
                return price

        # if cg_failed and token_id is None and cg_reason is not None:
        #     if isinstance(cg_reason, str) and ("429" not in cg_reason and "Too Many Requests" not in cg_reason):
        #         try:
        #             DB.add_excluded(addr_key, cg_reason, blockchain=active_blockchain)
        #         except Exception:
        #             pass

        return None

    # TODO: REPAIR FUCKING FUNCTION: in exp.ipynb i had written code properly, and now i have to add referent code in this function
    def get_stats(
        self,
        wallet_address: str,
        wallet_balance: float,
        ERC20_TXS: list[dict] = None,
        internal_txs: list[dict] = None,
    ) -> dict:
        """
        Get full stats of the account

        Args:
        - `wallet_address (str)`: Address of the wallet
        - `ERC20_TXS (list[dict])`: List of ERC20 transactions info ascending by block number
        - `internal_txs (list[dict])`: List of internal transactions info
        - `balances_info (list[dict])`: List of token balances of the account

        Returns:
        - `dict`: Stats of the account
        """

        if self.web_client is None:
            logger.error("web_client is required for swap analysis")
            raise ValueError("web_client is required for swap analysis")

        erc20_txs = [tx for tx in (ERC20_TXS or []) if tx]
        internal_txs = [tx for tx in (internal_txs or []) if tx]
        if not erc20_txs and not internal_txs:
            logger.warning("No transactions to analyze")
            return None

        wallet_address = wallet_address.lower()
        zero_addr = "0x" + "0" * 40
        blockchain = getattr(getattr(self, "web_client", None), "NETWORK", "ethereum") or "ethereum"

        def _safe_int(val, default: int = 0) -> int:
            try:
                return int(val)
            except Exception:
                return default

        def _lower(val: str | None) -> str:
            return str(val).lower() if val is not None else ""

        def group_by_block(txs: list[dict]) -> dict[int, list[dict]]:
            grouped: dict[int, list[dict]] = {}
            for tx in txs:
                block = _safe_int(tx.get("blockNumber", 0))
                grouped.setdefault(block, []).append(tx)
            return grouped

        def pick_tx(txs: list[dict], field: str, value: str) -> dict | None:
            value = value.lower()
            for tx in txs:
                if _lower(tx.get(field, "")) == value:
                    return tx
            return txs[0] if txs else None

        def wei_to_eth(tx: dict | None) -> float:
            if not tx:
                return 0.0
            return _safe_int(tx.get("value", 0)) / 10**18

        def erc20_amount(tx: dict | None, addr: str | None = None) -> float:
            if not tx:
                return 0.0
            decimals = _safe_int(tx.get("tokenDecimal", 0) or 0)
            if decimals == 0 and addr:
                try:
                    _, meta_dec = self.web_client.get_token_meta(addr)
                    decimals = int(meta_dec or 0)
                except Exception:
                    decimals = 0
            return _safe_int(tx.get("value", 0)) / (10**decimals if decimals else 1)

        def token_symbol(tx: dict | None) -> str:
            if not tx:
                return ""
            return tx.get("tokenSymbol") or ""

        def token_address(tx: dict | None) -> str:
            if not tx:
                return ""
            return tx.get("contractAddress") or ""

        def gas_cost_eth(tx: dict | None) -> float:
            if not tx:
                return 0.0
            gas_used = _safe_int(tx.get("gasUsed", 0) or 0)
            gas_price = _safe_int(tx.get("gasPrice", 0) or 0)
            return (gas_used * gas_price) / 10**18

        def get_type_of_swap_transaction(address: str, internal_txs: list[dict], erc20_txs: list[dict]) -> dict:
            eoa = address.lower()
            internal_by_block = group_by_block(internal_txs)
            erc20_by_block = group_by_block(erc20_txs)
            unique_blocknumbers = sorted(set(internal_by_block) | set(erc20_by_block))
            swap_types: dict[int, int] = {}

            for blocknumber in unique_blocknumbers:
                internal_list = internal_by_block.get(blocknumber, [])
                erc20_list = erc20_by_block.get(blocknumber, [])

                if internal_list and erc20_list:
                    internal_from = _lower(internal_list[0].get("from"))
                    internal_to = _lower(internal_list[0].get("to"))
                    erc20_from = _lower(erc20_list[0].get("from"))
                    erc20_to = _lower(erc20_list[0].get("to"))

                    if internal_from == erc20_to:
                        swap_types[blocknumber] = 1
                    elif erc20_from == internal_to:
                        swap_types[blocknumber] = 2
                    else:
                        swap_types[blocknumber] = 0
                    continue

                if internal_list:
                    swap_types[blocknumber] = 0
                    continue

                if len(erc20_list) >= 2:
                    tx_out_from = _lower(erc20_list[0].get("from"))
                    tx_in_to = _lower(erc20_list[1].get("to"))
                    if tx_out_from == eoa and tx_in_to == eoa:
                        swap_types[blocknumber] = 3
                    else:
                        swap_types[blocknumber] = 0
                else:
                    swap_types[blocknumber] = 0

            return swap_types

        def get_swap_data(address: str, blocknumber_types: dict, internal_txs: list[dict], erc20_txs: list[dict]) -> dict:
            eoa = address.lower()
            internal_by_block = group_by_block(internal_txs)
            erc20_by_block = group_by_block(erc20_txs)
            result: dict[int, dict] = {}
            STABLES = {"USDT", "USDC", "DAI"}
            MAX_USD = 8_000.0

            for blocknumber, swap_type in blocknumber_types.items():
                internal_list = internal_by_block.get(blocknumber, [])
                erc20_list = erc20_by_block.get(blocknumber, [])

                if swap_type == 1:
                    internal_tx = pick_tx(internal_list, "from", eoa)
                    erc20_tx = pick_tx(erc20_list, "to", eoa)
                    gas_tx = internal_tx or erc20_tx

                    eth_amount = wei_to_eth(internal_tx)
                    token_addr = token_address(erc20_tx)
                    token_amount = erc20_amount(erc20_tx, token_addr)
                    token = token_symbol(erc20_tx)
                    ts = _safe_int((internal_tx or erc20_tx or {}).get("timeStamp", 0))
                    block_num = _safe_int((internal_tx or erc20_tx or {}).get("blockNumber", 0))

                    price_eth = self.get_price_token(
                        blockchain=blockchain,
                        token_id="ethereum",
                        token_address=zero_addr,
                        timestamp=ts,
                        block_number=block_num,
                    )
                    if price_eth is None:
                        continue
                    if price_eth > MAX_USD:
                        continue

                    gas_eth = gas_cost_eth(gas_tx)
                    gas_usd = gas_eth * price_eth
                    usd = eth_amount * price_eth
                    if usd > MAX_USD:
                        continue

                    result[blocknumber] = {
                        "type": "ETH → Token",
                        "send": {"token": "ETH", "token_address": zero_addr, "amount": eth_amount},
                        "receive": {"token": token, "token_address": token_addr, "amount": token_amount},
                        "usd_value": usd,
                        "usd_out": usd,
                        "usd_in": usd,
                        "gas": {"amount_eth": gas_eth, "usd_value": gas_usd},
                        "timestamp": ts,
                        "block_number": block_num,
                    }
                    logger.info(
                        f"swap {blocknumber} | ETH -> {token} | send {eth_amount} | recv {token_amount} | usd {usd}"
                    )

                elif swap_type == 2:
                    internal_tx = pick_tx(internal_list, "to", eoa)
                    erc20_tx = pick_tx(erc20_list, "from", eoa)
                    gas_tx = internal_tx or erc20_tx

                    eth_amount = wei_to_eth(internal_tx)
                    token_addr = token_address(erc20_tx)
                    token_amount = erc20_amount(erc20_tx, token_addr)
                    token = token_symbol(erc20_tx)
                    ts = _safe_int((internal_tx or erc20_tx or {}).get("timeStamp", 0))
                    block_num = _safe_int((internal_tx or erc20_tx or {}).get("blockNumber", 0))

                    price_eth = self.get_price_token(
                        blockchain=blockchain,
                        token_id="ethereum",
                        token_address=zero_addr,
                        timestamp=ts,
                        block_number=block_num,
                    )
                    if price_eth is None:
                        continue
                    if price_eth > MAX_USD:
                        continue

                    gas_eth = gas_cost_eth(gas_tx)
                    gas_usd = gas_eth * price_eth
                    usd = eth_amount * price_eth
                    if usd > MAX_USD:
                        continue

                    result[blocknumber] = {
                        "type": "Token → ETH",
                        "send": {"token": token, "token_address": token_addr, "amount": token_amount},
                        "receive": {"token": "ETH", "token_address": zero_addr, "amount": eth_amount},
                        "usd_value": usd,
                        "usd_out": usd,
                        "usd_in": usd,
                        "gas": {"amount_eth": gas_eth, "usd_value": gas_usd},
                        "timestamp": ts,
                        "block_number": block_num,
                    }
                    logger.info(
                        f"swap {blocknumber} | {token} -> ETH | send {token_amount} | recv {eth_amount} | usd {usd}"
                    )

                elif swap_type == 3:
                    erc20_out = pick_tx(erc20_list, "from", eoa)
                    erc20_in = pick_tx(erc20_list, "to", eoa)
                    gas_tx = erc20_out or erc20_in

                    out_token_addr = token_address(erc20_out)
                    in_token_addr = token_address(erc20_in)
                    out_amount = erc20_amount(erc20_out, out_token_addr)
                    in_amount = erc20_amount(erc20_in, in_token_addr)
                    out_token = token_symbol(erc20_out)
                    in_token = token_symbol(erc20_in)
                    ts = _safe_int((erc20_out or erc20_in or {}).get("timeStamp", 0))
                    block_num = _safe_int((erc20_out or erc20_in or {}).get("blockNumber", 0))

                    price_out = self.get_price_token(
                        blockchain=blockchain,
                        token_address=out_token_addr,
                        timestamp=ts,
                        block_number=block_num,
                    )
                    price_in = self.get_price_token(
                        blockchain=blockchain,
                        token_address=in_token_addr,
                        timestamp=ts,
                        block_number=block_num,
                    )
                    price_eth = self.get_price_token(
                        blockchain=blockchain,
                        token_id="ethereum",
                        token_address=zero_addr,
                        timestamp=ts,
                        block_number=block_num,
                    )
                    if price_out is None or price_in is None or price_eth is None:
                        continue
                    if price_out > MAX_USD or price_in > MAX_USD or price_eth > MAX_USD:
                        continue

                    usd_out = out_amount if out_token in STABLES else out_amount * price_out
                    usd_in = in_amount if in_token in STABLES else in_amount * price_in

                    gas_eth = gas_cost_eth(gas_tx)
                    gas_usd = gas_eth * price_eth
                    if max(usd_out, usd_in) > MAX_USD:
                        continue

                    result[blocknumber] = {
                        "type": "Token → Token",
                        "send": {"token": out_token, "token_address": out_token_addr, "amount": out_amount},
                        "receive": {"token": in_token, "token_address": in_token_addr, "amount": in_amount},
                        "usd_value": max(usd_out, usd_in),
                        "usd_out": usd_out,
                        "usd_in": usd_in,
                        "gas": {"amount_eth": gas_eth, "usd_value": gas_usd},
                        "timestamp": ts,
                        "block_number": block_num,
                    }
                    logger.info(
                        f"swap {blocknumber} | {out_token} -> {in_token} | send {out_amount} | recv {in_amount} | usd {max(usd_out, usd_in)}"
                    )

            return result

        def _compute_pnl_metrics(swaps: dict[int, dict]) -> dict:
            positions: dict[str, dict] = {}
            realized_pnl = 0.0
            total_gas = 0.0
            cash_out = 0.0
            cash_in = 0.0
            wins = 0
            losses = 0

            def _get_pos(addr: str) -> dict:
                if addr not in positions:
                    positions[addr] = {"qty": 0.0, "cost_usd": 0.0}
                return positions[addr]

            for block in sorted(swaps):
                item = swaps[block]
                t = item.get("type")
                gas_usd = float(item.get("gas", {}).get("usd_value", 0.0) or 0.0)
                total_gas += gas_usd

                if t == "ETH → Token":
                    buy_usd = float(item.get("usd_value", 0.0) or 0.0)
                    cash_out += buy_usd
                    recv = item.get("receive", {})
                    addr = (recv.get("token_address") or "").lower()
                    qty = float(recv.get("amount", 0.0) or 0.0)
                    pos = _get_pos(addr)
                    pos["qty"] += qty
                    pos["cost_usd"] += buy_usd + gas_usd
                elif t == "Token → ETH":
                    sell_usd = float(item.get("usd_value", 0.0) or 0.0)
                    cash_in += sell_usd
                    send = item.get("send", {})
                    addr = (send.get("token_address") or "").lower()
                    qty = float(send.get("amount", 0.0) or 0.0)
                    pos = _get_pos(addr)
                    if pos["qty"] > 0:
                        sell_qty = min(qty, pos["qty"])
                        avg_cost = pos["cost_usd"] / pos["qty"] if pos["qty"] else 0.0
                        cogs = avg_cost * sell_qty
                        trade_pnl = sell_usd - cogs - gas_usd
                        realized_pnl += trade_pnl
                        if trade_pnl > 0:
                            wins += 1
                        elif trade_pnl < 0:
                            losses += 1
                        pos["qty"] -= sell_qty
                        pos["cost_usd"] -= cogs
                    else:
                        trade_pnl = sell_usd - gas_usd
                        realized_pnl += trade_pnl
                        if trade_pnl > 0:
                            wins += 1
                        elif trade_pnl < 0:
                            losses += 1
                elif t == "Token → Token":
                    notional = float(item.get("usd_value", 0.0) or 0.0)

                    send = item.get("send", {})
                    send_addr = (send.get("token_address") or "").lower()
                    send_qty = float(send.get("amount", 0.0) or 0.0)
                    send_pos = _get_pos(send_addr)
                    if send_pos["qty"] > 0:
                        sell_qty = min(send_qty, send_pos["qty"])
                        avg_cost = send_pos["cost_usd"] / send_pos["qty"] if send_pos["qty"] else 0.0
                        cogs = avg_cost * sell_qty
                        trade_pnl = notional - cogs - gas_usd
                        realized_pnl += trade_pnl
                        if trade_pnl > 0:
                            wins += 1
                        elif trade_pnl < 0:
                            losses += 1
                        send_pos["qty"] -= sell_qty
                        send_pos["cost_usd"] -= cogs
                    else:
                        trade_pnl = notional - gas_usd
                        realized_pnl += trade_pnl
                        if trade_pnl > 0:
                            wins += 1
                        elif trade_pnl < 0:
                            losses += 1

                    recv = item.get("receive", {})
                    recv_addr = (recv.get("token_address") or "").lower()
                    recv_qty = float(recv.get("amount", 0.0) or 0.0)
                    recv_pos = _get_pos(recv_addr)
                    recv_pos["qty"] += recv_qty
                    recv_pos["cost_usd"] += notional

            unrealized_pnl = 0.0
            latest_block = self.web_client.get_latest_block()
            for addr, pos in positions.items():
                if pos["qty"] <= 0:
                    continue
                price = self.web_client.get_token_price_usd_at_block(addr, latest_block)
                if price is None:
                    continue
                unrealized_pnl += pos["qty"] * float(price) - pos["cost_usd"]

            total_pnl = realized_pnl + unrealized_pnl
            invested = max(cash_out - cash_in, 0.0)
            roi = total_pnl / invested if invested > 0 else 0.0
            total_trades = wins + losses
            winrate = wins / total_trades if total_trades > 0 else 0.0

            return {
                "realized_pnl_usd": realized_pnl,
                "unrealized_pnl_usd": unrealized_pnl,
                "total_pnl_usd": total_pnl,
                "roi": roi,
                "invested_usd": invested,
                "cash_out_usd": cash_out,
                "cash_in_usd": cash_in,
                "total_gas_usd": total_gas,
                "positions": positions,
                "wins": wins,
                "losses": losses,
                "winrate": winrate,
            }

        swap_types = get_type_of_swap_transaction(wallet_address, internal_txs, erc20_txs)
        swaps_by_block = get_swap_data(wallet_address, swap_types, internal_txs, erc20_txs)

        counting_data = {"total_out, $": 0.0, "total_in, $": 0.0, "total_gas, $": 0.0}
        swap_days = set()
        trade_values = []
        token_volume_by_symbol = {}
        unique_tokens = set()
        gas_values = []
        swaps_count = 0

        for block_num, swap in swaps_by_block.items():
            ts = _safe_int(swap.get("timestamp", 0))
            swap_type = swap.get("type")
            send = swap.get("send", {})
            recv = swap.get("receive", {})
            usd_value = float(swap.get("usd_value", 0.0) or 0.0)
            gas_usd = float(swap.get("gas", {}).get("usd_value", 0.0) or 0.0)

            offer_out_usd = 0.0
            offer_in_usd = 0.0

            if swap_type == "ETH → Token":
                offer_out_usd = float(swap.get("usd_out", 0.0) or 0.0)
                offer_in_usd = float(swap.get("usd_in", 0.0) or 0.0)
                token_volume_by_symbol["ETH"] = token_volume_by_symbol.get("ETH", 0.0) + usd_value
                token_volume_by_symbol[recv.get("token", "")] = token_volume_by_symbol.get(recv.get("token", ""), 0.0) + usd_value
            elif swap_type == "Token → ETH":
                offer_out_usd = float(swap.get("usd_out", 0.0) or 0.0)
                offer_in_usd = float(swap.get("usd_in", 0.0) or 0.0)
                token_volume_by_symbol[send.get("token", "")] = token_volume_by_symbol.get(send.get("token", ""), 0.0) + usd_value
                token_volume_by_symbol["ETH"] = token_volume_by_symbol.get("ETH", 0.0) + usd_value
            elif swap_type == "Token → Token":
                offer_out_usd = float(swap.get("usd_out", 0.0) or 0.0)
                offer_in_usd = float(swap.get("usd_in", 0.0) or 0.0)
                token_volume_by_symbol[send.get("token", "")] = token_volume_by_symbol.get(send.get("token", ""), 0.0) + offer_out_usd
                token_volume_by_symbol[recv.get("token", "")] = token_volume_by_symbol.get(recv.get("token", ""), 0.0) + offer_in_usd

            counting_data["total_out, $"] += offer_out_usd
            counting_data["total_in, $"] += offer_in_usd
            counting_data["total_gas, $"] += gas_usd

            if ts:
                swap_days.add(datetime.fromtimestamp(ts).date())
            trade_values.append(usd_value)
            unique_tokens.add(send.get("token_address"))
            unique_tokens.add(recv.get("token_address"))
            gas_values.append(gas_usd)
            swaps_count += 1

        all_txs = erc20_txs + internal_txs
        timestamps = [_safe_int(tx.get("timeStamp", 0)) for tx in all_txs if _safe_int(tx.get("timeStamp", 0)) > 0]
        block_numbers = {_safe_int(tx.get("blockNumber", 0)) for tx in all_txs if _safe_int(tx.get("blockNumber", 0)) > 0}
        if timestamps:
            first_ts = min(timestamps)
            last_ts = max(timestamps)
            days_span = max((datetime.fromtimestamp(last_ts) - datetime.fromtimestamp(first_ts)).days, 1)
            wallet_age = (datetime.now() - datetime.fromtimestamp(first_ts)).days
            frequency = round(len(block_numbers) / days_span, 5) if days_span > 0 else 0.0
            days_since_last_transaction = (datetime.now() - datetime.fromtimestamp(last_ts)).days
        else:
            wallet_age = 0
            frequency = 0.0
            days_since_last_transaction = 0

        latest_block = self.web_client.get_latest_block()
        now_ts = int(datetime.now().timestamp())
        price_eth = self.get_price_token(
            blockchain=blockchain,
            token_id="ethereum",
            token_address=zero_addr,
            timestamp=now_ts,
            block_number=latest_block,
        )

        if price_eth is None:
            logger.warning("Price of ETH is not found")
            return None

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

        pnl_metrics = _compute_pnl_metrics(swaps_by_block)

        # Core metrics for df (no derived ratios)
        descriptive_data = {
            # * Account info
            "wallet_age": wallet_age,
            "frequency_of_transactions": frequency,
            "last_activity": days_since_last_transaction,
            "total_balance": wallet_balance,
            # * Counting data
            "total_out, $": counting_data["total_out, $"],
            "total_in, $": counting_data["total_in, $"],
            "total_gas, $": counting_data["total_gas, $"],
            "count_ERC20_TXS": len(erc20_txs),
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
            # * PnL
            "realized_pnl_usd": pnl_metrics.get("realized_pnl_usd", 0.0),
            "unrealized_pnl_usd": pnl_metrics.get("unrealized_pnl_usd", 0.0),
            "total_pnl_usd": pnl_metrics.get("total_pnl_usd", 0.0),
            "roi": pnl_metrics.get("roi", 0.0),
            "invested_usd": pnl_metrics.get("invested_usd", 0.0),
            "cash_out_usd": pnl_metrics.get("cash_out_usd", 0.0),
            "cash_in_usd": pnl_metrics.get("cash_in_usd", 0.0),
            "total_gas_usd": pnl_metrics.get("total_gas_usd", 0.0),
            "wins": pnl_metrics.get("wins", 0),
            "losses": pnl_metrics.get("losses", 0),
            "winrate": pnl_metrics.get("winrate", 0.0),
        }

        # Aggregate data
        data = {"address": wallet_address} | descriptive_data

        return data
