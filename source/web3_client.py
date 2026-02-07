import asyncio
import os
import threading
import time
from decimal import Decimal
from queue import LifoQueue

from etherscan import Etherscan
from web3 import Web3

from .coingecko_client import CoingeckoClient
from .database_helper import DB
from .settings import CONFIGS, Logger

# Chain configuration constants
CHAIN_CONSTANTS = {
    "ethereum": {
        "MULTICALL3": "0xcA11bde05977b3631167028862bE2a173976CA11",
        "UNISWAP_V3_FACTORY": "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        "UNISWAP_V2_FACTORY": "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
        "WETH": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    },
    # Add other chains here
}

# ABIs 
# # This is older version
# v3_factory_abi = [{"inputs": [{"type": "address", "name": "tA"}, {"type": "address", "name": "tB"}, {"type": "uint24", "name": "fee"}], "name": "getPool", "outputs": [{"type": "address", "name": ""}], "type": "function"}]
# v2_factory_abi = [{"inputs": [{"type": "address", "name": "tA"}, {"type": "address", "name": "tB"}], "name": "getPair", "outputs": [{"type": "address", "name": ""}], "type": "function"}]
# v3_pool_abi = [{"inputs": [], "name": "slot0", "outputs": [{"type": "uint160", "name": "sqrtPriceX96"}, {"type": "int24"}, {"type": "uint16"}, {"type": "uint16"}, {"type": "uint16"}, {"type": "uint8"}, {"type": "bool"}], "type": "function"}, {"inputs": [], "name": "liquidity", "outputs": [{"type": "uint128", "name": ""}], "type": "function"}, {"inputs": [], "name": "token0", "outputs": [{"type": "address", "name": ""}], "type": "function"}]
# v2_pair_abi = [{"inputs": [], "name": "getReserves", "outputs": [{"type": "uint112", "name": "r0"}, {"type": "uint112", "name": "r1"}, {"type": "uint32", "name": "ts"}], "type": "function"}, {"inputs": [], "name": "token0", "outputs": [{"type": "address", "name": ""}], "type": "function"}]

v3_factory_abi = CONFIGS.UNISWAP_ABI.V3_FACTORY_ABI
v2_factory_abi = CONFIGS.UNISWAP_ABI.V2_FACTORY_ABI
v3_pool_abi = CONFIGS.UNISWAP_ABI.V3_POOL_ABI
v2_pair_abi = CONFIGS.UNISWAP_ABI.V2_PAIR_ABI

logger = Logger()

# Global LIFO stack to limit concurrent external requests
_CALL_QUEUE: LifoQueue = LifoQueue()
_WORKERS_STARTED = False
_WORKERS_LOCK = threading.Lock()


def _request_worker():
    while True:
        job = _CALL_QUEUE.get()
        try:
            if job is None:
                return
            func, event, ref = job
            try:
                ref["value"] = func()
                ref["error"] = None
            except Exception as e:
                ref["error"] = e
            finally:
                event.set()
        finally:
            _CALL_QUEUE.task_done()


def _ensure_request_workers():
    global _WORKERS_STARTED
    with _WORKERS_LOCK:
        if _WORKERS_STARTED:
            return
        try:
            n = int(os.getenv("GLOBAL_REQUEST_WORKERS", "5"))
        except Exception:
            n = 5
        n = max(1, n)
        for i in range(n):
            t = threading.Thread(target=_request_worker, name=f"request_worker_{i+1}", daemon=True)
            t.start()
        _WORKERS_STARTED = True


class WebClient:
    def __init__(self, RPC_URL: str, NETWORK: str, ETHERSCAN_API_KEY: str | None = None) -> None:
        """
        Initialize Web3Client with RPC URL

        Args:
        - `RPC_URL (str)`: RPC endpoint URL (represented in `Web3Config.RPC_URL`)
        - `NETWORK (str)`: Network of the token, default: `"ethereum"`
        """
        self.RPC_URL: str = RPC_URL
        self.NETWORK: str = NETWORK
        try:
            self._api_delay_sec: float = float(os.getenv("API_DELAY", "0.5"))
        except Exception:
            self._api_delay_sec = 0.5

        self.w3: Web3 = None
        self.__connect()

        self.chain_id: int = self.__get_chain_id()

        # * Documentation: https://github.com/pcko1/etherscan-python
        # ! BUT I CHANGED ORIGINAL CODE IN `etherscan.py` FILE TO ADD MULTI-CHAIN SUPPORT
        # Rotate Etherscan keys per client instance
        api_keys = CONFIGS.CRYPTO.ETHERSCAN_API_KEYS or []
        api_key = ETHERSCAN_API_KEY if ETHERSCAN_API_KEY is not None else (api_keys[0] if api_keys else "")
        self.etherscan_client: Etherscan = Etherscan(api_key=api_key, use_v2=True, chain_id=self.chain_id)

        # * Documentation: https://docs.coingecko.com/v3.0.1/reference/introduction
        self.coingecko_client: CoingeckoClient = CoingeckoClient(API_KEY=CONFIGS.COINGECKO.API_KEY)

        # In-memory caches
        self._token_meta_cache: dict[str, tuple[str, int]] = {}

        # Load Chain Constants
        config = CHAIN_CONSTANTS.get(self.NETWORK.lower())
        if not config:
            logger.warning(f"Network {self.NETWORK} not found in CHAIN_CONSTANTS. Using Ethereum defaults.")
            config = CHAIN_CONSTANTS["ethereum"]

        self.UNISWAP_V3_FACTORY = self.w3.to_checksum_address(config["UNISWAP_V3_FACTORY"])
        self.UNISWAP_V2_FACTORY = self.w3.to_checksum_address(config["UNISWAP_V2_FACTORY"])
        self.ETH = '0x0000000000000000000000000000000000000000'
        self.WETH = self.w3.to_checksum_address(config["WETH"])
        self.USDC = self.w3.to_checksum_address(config["USDC"])
        self.MULTICALL_ADDR = self.w3.to_checksum_address(config["MULTICALL3"])
        self.UNI_V3_FEE_TIERS = [500, 3000, 10000]

    def _throttle(self):
        delay = self._api_delay_sec
        if delay and delay > 0:
            time.sleep(delay)

    def _call_with_retry_on_429(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            msg = str(e)
            if "429" in msg or "Too Many Requests" in msg:
                logger.warning("429 received; retrying in 4.0s once")
                time.sleep(4)
                return fn(*args, **kwargs)
            raise

    def _submit_via_stack(self, func):
        _ensure_request_workers()
        event = threading.Event()
        ref: dict = {}
        _CALL_QUEUE.put((func, event, ref))
        event.wait()
        err = ref.get("error")
        if err is not None:
            raise err
        return ref.get("value")

    def _queued_etherscan(self, fn, *args, **kwargs):
        def _job():
            self._throttle()
            return self._call_with_retry_on_429(fn, *args, **kwargs)

        return self._submit_via_stack(_job)

    def _queued_rpc(self, fn, *args, **kwargs):
        def _job():
            self._throttle()
            return self._call_with_retry_on_429(fn, *args, **kwargs)

        return self._submit_via_stack(_job)

    def __connect(self):
        """
        Initialize the web3 client

        Documentation web3.eth API: https://web3py.readthedocs.io/en/stable/web3.eth.html#

        Raises:
        - `ConnectionError`: If the connection fails
        """

        # Add request timeout to avoid indefinite hangs on RPC calls
        try:
            rpc_timeout = float(os.getenv("ETH_RPC_TIMEOUT", "30"))
        except Exception:
            rpc_timeout = 30.0
        self.w3 = Web3(Web3.HTTPProvider(self.RPC_URL, request_kwargs={"timeout": rpc_timeout}))

        # Check if the connection is successful
        if not self.w3.is_connected():
            logger.error(f"Failed to connect to {self.RPC_URL}")
            raise ConnectionError(f"Failed to connect to {self.RPC_URL}")
        else:
            logger.info(f"Connected to {self.RPC_URL}")
            logger.info(f"Network: {self.NETWORK}")

    def __get_chain_id(self) -> int:
        """
        Get the chain id

        About method: https://web3py.readthedocs.io/en/stable/web3.eth.html#web3.eth.Eth.chain_id

        Returns:
                `int`: Chain id
        """
        return self.w3.eth.chain_id

    def get_decimals(self, token_address: str) -> int:
        """
        Get the decimals of a token

        About contract: https://web3py.readthedocs.io/en/stable/web3.eth.html#web3.eth.Eth.contract
        About .call() method: https://web3py.readthedocs.io/en/stable/web3.contract.html#web3.contract.ContractFunction.call

        Args:
        - `token_address (str)`: Token contract address

        Returns:
        - `int`: Decimal of the token
        """
        # Convert the token address to checksum address
        token_address = self.w3.to_checksum_address(token_address)

        # Define the minimal ABI for the decimals function
        minimal_abi = [{"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"}]

        # Create the contract object
        contract = self.w3.eth.contract(address=token_address, abi=minimal_abi)

        # Call the decimals function
        try:
            decimals = contract.functions.decimals().call()
            return decimals
        except Exception as e:
            logger.error(f"Error getting decimals: {e}")
            raise Exception(f"Error getting decimals: {e}")

    def get_latest_block(self) -> int:
        """
        Get the latest block number

        About endpoint: https://web3py.readthedocs.io/en/stable/web3.eth.html#web3.eth.Eth.get_block_number

        Returns:
        - `int`: Latest block number
        """
        return int(self._queued_rpc(lambda: self.w3.eth.get_block_number()))

    def get_block_timestamp(self, block_number: int) -> int:
        """
        Get block timestamp

        Args:
        - `block_number (int)`: Block number

        Returns:
        - `int`: Block timestamp
        """
        block = self._queued_rpc(lambda: self.w3.eth.get_block(block_number))
        return int(block["timestamp"])

    def get_symbol(self, token_address: str) -> str:
        """
        Get token symbol using cached metadata

        Args:
        - `token_address (str)`: Token address

        Returns:
        - `str`: Token symbol
        """
        sym, _ = self.get_token_meta(token_address)
        return sym

    def get_token_meta(self, token_address: str) -> tuple[str, int]:
        """
        Return (symbol, decimals) using tokens.db (sqlite) with in-memory cache

        Args:
        - `token_address (str)`: Token address

        Returns:
        - `tuple[str, int]`: (symbol, decimals)

        Fallback to on-chain fetch when absent, then upsert into DB.
        """
        addr_checksum = self.w3.to_checksum_address(token_address)
        if addr_checksum in self._token_meta_cache:
            return self._token_meta_cache[addr_checksum]

        addr_key = addr_checksum.lower()

        # 1) Try sqlite tokens.db
        try:
            got = DB.get_token(addr_key)
            if got is not None:
                self._token_meta_cache[addr_checksum] = got
                return got
        except Exception:
            pass

        # 2) Fallback to on-chain fetch via minimal ABIs
        abi_dec = [{"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"}]
        abi_sym = [{"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"}]
        c_dec = self.w3.eth.contract(address=addr_checksum, abi=abi_dec)
        c_sym = self.w3.eth.contract(address=addr_checksum, abi=abi_sym)
        try:
            dec = int(c_dec.functions.decimals().call())
        except Exception:
            dec = 18
        try:
            sym = c_sym.functions.symbol().call()
        except Exception:
            sym = addr_checksum

        # 3) Upsert into sqlite
        try:
            DB.upsert_token(addr_key, sym, int(dec))
        except Exception:
            pass

        self._token_meta_cache[addr_checksum] = (sym, dec)
        return self._token_meta_cache[addr_checksum]

    def is_wallet(self, address: str) -> bool | None:
        """
        Check if address is wallet or not

        Args:
        - `address (str)`: Given address

        Returns:
            `bool`: True for the wallet, False for the contract
        """
        if not address:
            logger.error('Given address is empty')
            raise ValueError('Given address is empty')
        try:
            code = self.w3.eth.get_code(address).hex()
            return len(code) <= 46
        except Exception as e:
            logger.error(f"Error getting code for address {address}: {e}")
            return None

    def get_balance(self, address: str | list[str]) -> float | dict[str, float]:
        """
        Get the balance of an account or multiple accounts (in ETH units)

        About single balance endpoint: https://docs.etherscan.io/api-endpoints/accounts#get-ether-balance-for-a-single-address
        About multiple balances endpoint: https://docs.etherscan.io/api-endpoints/accounts#get-ether-balance-for-multiple-addresses-in-a-single-call

        Args:
        - `address (str | list[str])`: Address of the account, variable can be a single address or a list of addresses

        Returns:
        - `float`: Balance of the account if `address` is a single address
        - `dict[str, float]`: Balance of multiple accounts if `address` is a list of addresses

        This balance is in basement USD units

        Example:
        >>> web3_client.get_balance("0x742d35Cc6634C0532925a3b844Bc454e4438f44e")
        10.851112656372348154

        >>> web3_client.get_balance(["0x742d35Cc6634C0532925a3b844Bc454e4438f44e", "0xE8CaF3c8dbA8D6Cfd4c2c253E21bB0F0227Ccd59"])
        [{'account': '0x742d35Cc6634C0532925a3b844Bc454e4438f44e',
          'balance': 104.793851112656372348154},
         {'account': '0xE8CaF3c8dbA8D6Cfd4c2c253E21bB0F0227Ccd59',
          'balance': 0.0}]
        """
        # If the address is a single address
        if isinstance(address, str):
            address = self.w3.to_checksum_address(address)

            try:
                response = self._queued_etherscan(self.etherscan_client.get_eth_balance, address=address)
                response = float(response) / 10**18 * self.get_token_price_usd_at_block(self.ETH, self.get_latest_block())
            except Exception as e:
                logger.error(f"Error getting balance: {e}")
                raise Exception(f"Error getting balance: {e}")

            return response

        # If the address is a list of addresses
        elif isinstance(address, list):

            # It's necessary step cause Etherscan API only supports up to 20 addresses at a time
            chunks = [address[i : i + 20] for i in range(0, len(address), 20)]

            # Get the balance from each account
            for chunk in chunks:
                addresses = [self.w3.to_checksum_address(addr) for addr in chunk]

                try:
                    response = self._queued_etherscan(self.etherscan_client.get_eth_balance_multiple, addresses=addresses)
                    for resp in response:
                        resp["balance"] = float(resp["balance"]) / 10**18 * self.get_token_price_usd_at_block(self.ETH, self.get_latest_block())
                except Exception as e:
                    logger.error(f"Error getting balance: {e}")
                    raise Exception(f"Error getting balance: {e}")

                for resp in response:
                    if resp["balance"] == 0.0:
                        logger.warning(f"Address {resp['account']} has 0 balance")

                return response

    async def get_erc20_txs_by_block_range_async(
        self,
        wallet_address: str | None = None,
        contract_address: str | None = None,
        startblock: int | None = None,
        endblock: int | None = None,
        sort: str = "asc",
        timeout_sec: float | None = None,
    ) -> list[dict]:
        """
        Async wrapper for get_erc20_txs_by_block_range using a thread offload.

        Args:
        - `wallet_address (str | None)`: Address of the wallet
        - `contract_address (str | None)`: Address of the contract
        - `startblock (int | None)`: Start block, default: `latest block - 10_000_000`
        - `endblock (int | None)`: End block, default: `latest block`
        - `sort (str)`: Sort order, default: `asc` cause i need oldest transactions
        - `timeout_sec (float | None)`: Timeout in seconds, default: `60`

        Returns:
        - `list[dict]`: List of ERC20 transactions

        Example:
        >>> response = await web3_client.get_erc20_txs_by_block_range_async(
                                wallet_address="0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
                                sort="asc",
                                timeout_sec=60)
        >>> print(json.dumps(response[0], indent=4))
                {
                        'blockNumber': '22696579',
                        'timeStamp': '1749829043',
                        'hash': '0xb006a759922dd538f1fc8920b5438ec6931cf4a136778f9ed98643cbc1b3d2a7',
                        'nonce': '463',
                        'blockHash': '0x246ced3e61473033abfcc349538889fad7d769eda6854c5614a0e7493a4bf483',
                        'from': '0x9568f4c0c084f20064a6d0d8b2337c82835a41d4',
                        'contractAddress': '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
                        'to': '0x4cd1f03edba39e034e328a65aff62d2bc3aa37c8',
                        'value': '350954271',
                        'tokenName': 'USDC',
                        'tokenSymbol': 'USDC',
                        'tokenDecimal': '6',
                        'transactionIndex': '150',
                        'gas': '78805',
                        'gasPrice': '2958096827',
                        'gasUsed': '57460',
                        'cumulativeGasUsed': '14532693',
                        'input': 'deprecated',
                        'methodId': '0xa9059cbb',
                        'functionName': 'transfer(address _to, uint256 _value)',
                        'confirmations': '844154'
                }

        """
        try:
            if timeout_sec is None:
                try:
                    timeout_sec = float(os.getenv("ETHERSCAN_TIMEOUT", "60"))
                except Exception:
                    timeout_sec = 60.0
            return await asyncio.wait_for(
                asyncio.to_thread(self.get_erc20_txs_by_block_range, wallet_address, contract_address, startblock, endblock, sort),
                timeout=timeout_sec,
            )
        except asyncio.TimeoutError:
            raise Exception(f"Etherscan fetch timed out after {timeout_sec}s")

    # TODO: Improve searching logic, I had not done this function
    def get_erc20_txs_by_block_range(
        self,
        wallet_address: str | None = None,
        contract_address: str | None = None,
        startblock: int | None = None,
        endblock: int | None = None,
        sort: str = "asc",
    ) -> list[dict]:
        """
        Get all ERC20 transactions for an address by block range either by contract address or by wallet address or by both
        Searhing via Etherscan API

        About endpoint: https://docs.etherscan.io/api-endpoints/accounts#get-a-list-of-normal-transactions-by-address

        Args:
        - `wallet_address (str | None)`: Address of the wallet
        - `contract_address (str | None)`: Address of the contract
        - `startblock (int | None)`: Start block, default: `latest block - 10_000_000`
        - `endblock (int | None)`: End block, default: `latest block`
        - `sort (str)`: Sort order, default: `asc` cause i need oldest transactions

        Returns:
        - `list[dict]`: List of ERC20 transactions

        Example:
        >>> response = web3_client.get_erc20_txs_by_block_range(wallet_address="0x742d35Cc6634C0532925a3b844Bc454e4438f44e", sort="asc")
        >>> print(json.dumps(response[0], indent=4))
                {
                        'blockNumber': '22696579',
                        'timeStamp': '1749829043',
                        'hash': '0xb006a759922dd538f1fc8920b5438ec6931cf4a136778f9ed98643cbc1b3d2a7',
                        'nonce': '463',
                        'blockHash': '0x246ced3e61473033abfcc349538889fad7d769eda6854c5614a0e7493a4bf483',
                        'from': '0x9568f4c0c084f20064a6d0d8b2337c82835a41d4',
                        'contractAddress': '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
                        'to': '0x4cd1f03edba39e034e328a65aff62d2bc3aa37c8',
                        'value': '350954271',
                        'tokenName': 'USDC',
                        'tokenSymbol': 'USDC',
                        'tokenDecimal': '6',
                        'transactionIndex': '150',
                        'gas': '78805',
                        'gasPrice': '2958096827',
                        'gasUsed': '57460',
                        'cumulativeGasUsed': '14532693',
                        'input': 'deprecated',
                        'methodId': '0xa9059cbb',
                        'functionName': 'transfer(address _to, uint256 _value)',
                        'confirmations': '844154'
                }
        """
        # Get block numbers
        endblock = self.get_latest_block() if endblock is None else endblock
        # startblock = endblock - 10_000_000 if startblock is None else startblock
        startblock = 0 if startblock is None else startblock

        if wallet_address is None and contract_address is None:
            logger.error("Either wallet_address or contract_address must be provided")
            raise ValueError("Either wallet_address or contract_address must be provided")
        elif wallet_address is not None and contract_address is None:
            search_type = "SEARCH_BY_WALLET"
        elif wallet_address is None and contract_address is not None:
            search_type = "SEARCH_BY_CONTRACT"
        else:
            search_type = "SEARCH_BY_BOTH"

        # * For all types of search we return all ERC20 token transfer events

        if search_type == "SEARCH_BY_WALLET":
            try:
                results: list[dict] = []
                cur_start = int(startblock)
                cur_end = int(endblock)
                last_boundary = None

                while True:
                    response = self._queued_etherscan(
                        self.etherscan_client.get_erc20_token_transfer_events_by_address,
                        address=wallet_address,
                        startblock=cur_start,
                        endblock=cur_end,
                        sort=sort,
                    )
                    if not response:
                        break

                    results.extend(response)
                    if sort == "asc":
                        boundary = int(response[-1].get("blockNumber", cur_start))
                        if last_boundary is not None and boundary <= last_boundary:
                            break
                        last_boundary = boundary
                        cur_start = boundary + 1
                        if cur_start > cur_end:
                            break
                    else:
                        boundary = int(response[0].get("blockNumber", cur_end))
                        if last_boundary is not None and boundary >= last_boundary:
                            break
                        last_boundary = boundary
                        cur_end = boundary - 1
                        if cur_end < cur_start:
                            break

                return results
            except Exception as e:
                err_name = type(e).__name__
                err_msg = str(e)
                logger.error(f"Etherscan fetch failed ({err_name}): {err_msg} | wallet_address={wallet_address}")
                raise Exception(f"Error getting transactions: {err_name}: {err_msg}")
        elif search_type == "SEARCH_BY_CONTRACT":
            # TODO: Implement search by contract
            pass
        else:
            # TODO: Implement search by both
            pass
    
    async def get_internal_txs_by_block_range_async(
        self,
        wallet_address: str | None = None,
        startblock: int | None = None,
        endblock: int | None = None,
        sort: str = "asc",
        timeout_sec: float | None = None,
    ) -> list[dict]:
        """
        Async wrapper for get_internal_txs_by_block_range using a thread offload.

        Args:
        - `wallet_address (str | None)`: Address of the wallet
        - `startblock (int | None)`: Start block, default: `0`
        - `endblock (int | None)`: End block, default: `latest block`
        - `sort (str)`: Sort order, default: `asc`
        - `timeout_sec (float | None)`: Timeout in seconds, default: `60`

        Returns:
        - `list[dict]`: List of internal transactions
        """
        try:
            if timeout_sec is None:
                try:
                    timeout_sec = float(os.getenv("ETHERSCAN_TIMEOUT", "60"))
                except Exception:
                    timeout_sec = 60.0
            return await asyncio.wait_for(
                asyncio.to_thread(self.get_internal_txs_by_block_range, wallet_address, startblock, endblock, sort),
                timeout=timeout_sec,
            )
        except asyncio.TimeoutError:
            raise Exception(f"Etherscan fetch timed out after {timeout_sec}s")

    def get_internal_txs_by_block_range(
        self,
        wallet_address: str | None = None,
        startblock: int | None = None,
        endblock: int | None = None,
        sort: str = "asc",
    ) -> list[dict]:
        """
        Get internal transactions for an address by block range using Etherscan API.

        About endpoint: https://docs.etherscan.io/api-endpoints/accounts#get-internal-transactions-by-address

        Args:
        - `wallet_address (str | None)`: Address of the wallet
        - `startblock (int | None)`: Start block, default: `0`
        - `endblock (int | None)`: End block, default: `latest block`
        - `sort (str)`: Sort order, default: `asc`

        Returns:
        - `list[dict]`: List of internal transactions
        """
        endblock = self.get_latest_block() if endblock is None else endblock
        startblock = 0 if startblock is None else startblock

        if wallet_address is None:
            logger.error("wallet_address must be provided")
            raise ValueError("wallet_address must be provided")

        try:
            response = self._queued_etherscan(
                self.etherscan_client.get_internal_txs_by_address,
                address=wallet_address,
                startblock=startblock,
                endblock=endblock,
                sort=sort,
            )
            return response
        except Exception as e:
            err_name = type(e).__name__
            err_msg = str(e)
            logger.error(f"Etherscan fetch failed ({err_name}): {err_msg} | wallet_address={wallet_address}")
            raise Exception(f"Error getting internal transactions: {err_name}: {err_msg}")

    def get_transcation_receipt(self, tx_hash: str) -> dict:
        """
        Get the transaction receipt (include chain-of-events)
        Important: for use all addresses in receipt you need to convert them to checksum address

        Args:
        - `tx_hash (str)`: Transaction hash

        Returns:
        - `dict`: Transaction receipt

        Example of return:
        >>> response = web3_client.get_transcation_receipt(tx_hash="0xfae4760ea4a280da95d061caccf47be43b6a45e53607ae4d4a7eec34ecc554e4")
        >>> print(json.dumps(response, indent=4))
            {'blockHash': '0xa99833efada03afc95d0f31b1ed3ada9d42baab2d9abfbaac23396f792a8124e',
                'blockNumber': '0x15ecb06',
                'contractAddress': None,
                'cumulativeGasUsed': '0x837d01',
                'effectiveGasPrice': '0x4e672bbc',
                'from': '0x9568f4c0c084f20064a6d0d8b2337c82835a41d4',
                'gasUsed': '0x2ec05',
                'logs': [{'address': '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
                'topics': ['0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef',
                    '0x0000000000000000000000009568f4c0c084f20064a6d0d8b2337c82835a41d4',
                    '0x0000000000000000000000005141b82f5ffda4c6fe1e372978f1c5427640a190'],
                'data': '0x000000000000000000000000000000000000000000000000000000001f4add40',
                'blockNumber': '0x15ecb06',
                'transactionHash': '0xfae4760ea4a280da95d061caccf47be43b6a45e53607ae4d4a7eec34ecc554e4',
                'transactionIndex': '0x69',
                'blockHash': '0xa99833efada03afc95d0f31b1ed3ada9d42baab2d9abfbaac23396f792a8124e',
                'logIndex': '0xb5',
                'removed': False},
                ...],
                'logsBloom': ... (Very long string),
                'status': '0x1',
                'to': '0x1111111254eeb25477b68fb85ed929f73a960582',
                'transactionHash': '0xfae4760ea4a280da95d061caccf47be43b6a45e53607ae4d4a7eec34ecc554e4',
                'transactionIndex': '0x69',
                'type': '0x2'}
        """
        try:
            response = self._queued_rpc(lambda: self.w3.eth.get_transaction_receipt(tx_hash))
            return response
        except Exception as e:
            logger.error(f"Error getting transaction receipt: {e}")
            raise Exception(f"Error getting transaction receipt: {e}")

    def _multicall(self, calls: list[tuple[str, str]], block_identifier: int | str = "latest") -> list[bytes]:
        """
        Execute Multicall3 aggregate call.
        Args:
            calls: list of (target_address, calldata_bytes)
            block_identifier: block number
        Returns:
            list of raw bytes results. If a call fails, returns b'' for that item.
        """
        multicall_abi = [
            {
                "inputs": [{"components": [{"name": "target", "type": "address"}, {"name": "callData", "type": "bytes"}], "name": "calls", "type": "tuple[]"}],
                "name": "aggregate",
                "outputs": [{"name": "blockNumber", "type": "uint256"}, {"name": "returnData", "type": "bytes[]"}],
                "stateMutability": "view",
                "type": "function",
            }
        ]
        
        # Prepare inputs for aggregate((address, bytes)[])
        formatted_calls = [{"target": target, "callData": data} for target, data in calls]
        
        try:
            contract = self.w3.eth.contract(address=self.MULTICALL_ADDR, abi=multicall_abi)
            _, return_data = contract.functions.aggregate(formatted_calls).call(block_identifier=block_identifier)
            return return_data
        except Exception as e:
            logger.error(f"Multicall failed: {e}")
            return [b""] * len(calls)

    def get_token_price_usd_at_block(self, token_address: str, block_number: int) -> float | None:
        """
        Spot price via Uniswap V3/V2 using Multicall for efficiency.
        Strategy:
          1. Batch 1: Get all potential pool addresses (V3 tiers, V2 pairs).
          2. Batch 2: Get metadata (slot0, reserves) for found pools.
          3. Calculate price locally.
        """
        eth_zero = "0x0000000000000000000000000000000000000000"
        try:
            if str(token_address).lower() == eth_zero:
                token = self.WETH
            else:
                token = self.w3.to_checksum_address(token_address)
        except Exception:
            return None
        token_lower = token.lower()
 
        # Contracts
        v3_fact = self.w3.eth.contract(address=self.UNISWAP_V3_FACTORY, abi=v3_factory_abi)
        v2_fact = self.w3.eth.contract(address=self.UNISWAP_V2_FACTORY, abi=v2_factory_abi)

        # --- BATCH 1: Discovery (Get Pool Addresses) ---
        # Map: request_id -> (type, tokenA, tokenB, extra)
        discovery_map = []
        calls_batch_1 = []

        # Helper to add call
        def add_discovery(contract, func_name, args, meta):
            data = contract.get_function_by_name(func_name)(*args)._encode_transaction_data()
            calls_batch_1.append((contract.address, data))
            discovery_map.append(meta)

        # V3 Token-WETH
        for fee in self.UNI_V3_FEE_TIERS:
            add_discovery(v3_fact, "getPool", [token, self.WETH, fee], {"type": "v3", "pair": "T-W", "fee": fee})
        # V3 WETH-USDC
        for fee in self.UNI_V3_FEE_TIERS:
            add_discovery(v3_fact, "getPool", [self.WETH, self.USDC, fee], {"type": "v3", "pair": "W-U", "fee": fee})
        # V2 Token-WETH
        add_discovery(v2_fact, "getPair", [token, self.WETH], {"type": "v2", "pair": "T-W"})
        # V2 WETH-USDC
        add_discovery(v2_fact, "getPair", [self.WETH, self.USDC], {"type": "v2", "pair": "W-U"})
        # V2 Token-USDC (Direct)
        add_discovery(v2_fact, "getPair", [token, self.USDC], {"type": "v2", "pair": "T-U"})

        # EXECUTE BATCH 1
        results_1 = self._multicall(calls_batch_1, block_number)
        
        # --- BATCH 2: Data Fetching (Slot0/Reserves) ---
        valid_pools = {} # key -> address
        calls_batch_2 = []
        data_map = [] # index -> (pool_key, data_type)

        def add_data_call(addr, abi, method, pool_key, data_type):
            tmp_c = self.w3.eth.contract(address=addr, abi=abi)
            calls_batch_2.append((addr, tmp_c.get_function_by_name(method)()._encode_transaction_data()))
            data_map.append({"key": pool_key, "type": data_type})

        # Process addresses
        for i, res in enumerate(results_1):
            if not res or len(res) < 20: continue # Invalid
            addr = self.w3.to_checksum_address(self.w3.eth.codec.decode(["address"], res)[0])
            if int(addr, 16) == 0: continue
            
            meta = discovery_map[i]
            # Create a unique key for this pool choice, e.g. "v3_T-W_3000"
            pkey = f"{meta['type']}_{meta['pair']}" + (f"_{meta['fee']}" if 'fee' in meta else "")
            valid_pools[pkey] = addr

            if meta["type"] == "v3":
                add_data_call(addr, v3_pool_abi, "slot0", pkey, "slot0")
                add_data_call(addr, v3_pool_abi, "liquidity", pkey, "liquidity")
            else: # v2
                add_data_call(addr, v2_pair_abi, "getReserves", pkey, "reserves")

        if not calls_batch_2:
            return None

        # EXECUTE BATCH 2
        results_2 = self._multicall(calls_batch_2, block_number)

        # Parse Data
        pool_data = {} # pkey -> {slot0: ..., token0: ...}

        for i, res in enumerate(results_2):
            if not res: continue
            meta = data_map[i]
            pkey = meta["key"]
            if pkey not in pool_data: pool_data[pkey] = {}
            
            try:
                if meta["type"] == "slot0":
                    # (sqrtPriceX96, tick, ...)
                    decoded = self.w3.eth.codec.decode(["uint160", "int24", "uint16", "uint16", "uint16", "uint8", "bool"], res)
                    pool_data[pkey]["sqrtPriceX96"] = decoded[0]
                elif meta["type"] == "liquidity":
                    pool_data[pkey]["liquidity"] = self.w3.eth.codec.decode(["uint128"], res)[0]
                elif meta["type"] == "reserves":
                    decoded = self.w3.eth.codec.decode(["uint112", "uint112", "uint32"], res)
                    pool_data[pkey]["reserves"] = (decoded[0], decoded[1])
            except Exception:
                pass

        # Helpers for Calc
        def get_dec(addr):
            try:
                # Assuming decimals are cached/fast enough or we could have multicalled them too.
                # For now using existing method to keep scope sane.
                _, d = self.get_token_meta(addr)
                return int(d)
            except:
                return 18

        dec_token = get_dec(token)

        def sqrt_to_price(sqrt, d0, d1):
            s = Decimal(sqrt)
            return ((s / Decimal(2)**96) ** 2) * (Decimal(10) ** (d0 - d1))

        # --- CALCULATION LOGIC ---
        
        # 1. Try V3 Token->WETH
        best_price_weth = None
        # Check all fee tiers
        for fee in self.UNI_V3_FEE_TIERS:
            k = f"v3_T-W_{fee}"
            d = pool_data.get(k)
            if d and d.get("liquidity", 0) > 0 and "sqrtPriceX96" in d:
                t0_is_token = token_lower < self.WETH.lower()
                if t0_is_token:
                    p = sqrt_to_price(d["sqrtPriceX96"], dec_token, 18)
                else:
                    p = Decimal(1) / sqrt_to_price(d["sqrtPriceX96"], 18, dec_token)
                best_price_weth = p
                break
        
        # If not found V3, try V2 Token->WETH
        if not best_price_weth:
            d = pool_data.get("v2_T-W")
            if d and "reserves" in d:
                r0, r1 = d["reserves"]
                if r0 > 0 and r1 > 0:
                    t0_is_token = token_lower < self.WETH.lower()
                    R0 = Decimal(r0) / Decimal(10)**(dec_token if t0_is_token else 18)
                    R1 = Decimal(r1) / Decimal(10)**(18 if t0_is_token else dec_token)
                    best_price_weth = R1/R0 if t0_is_token else R0/R1

        # Calculate WETH->USDC Price
        weth_usd_price = None
        # Try V3 WETH->USDC
        for fee in self.UNI_V3_FEE_TIERS:
            k = f"v3_W-U_{fee}"
            d = pool_data.get(k)
            if d and d.get("liquidity", 0) > 0:
                t0_is_weth = self.WETH.lower() < self.USDC.lower()
                d0, d1 = (18, 6) if t0_is_weth else (6, 18)
                raw = sqrt_to_price(d["sqrtPriceX96"], d0, d1)
                weth_usd_price = raw if t0_is_weth else Decimal(1) / raw
                break
        
        # Try V2 WETH->USDC if needed
        if not weth_usd_price:
            d = pool_data.get("v2_W-U")
            if d and "reserves" in d:
                r0, r1 = d["reserves"]
                if r0 > 0 and r1 > 0:
                    t0_is_weth = self.WETH.lower() < self.USDC.lower()
                    d0, d1 = (18, 6) if t0_is_weth else (6, 18)
                    R0 = Decimal(r0) / Decimal(10)**d0
                    R1 = Decimal(r1) / Decimal(10)**d1
                    weth_usd_price = R1/R0 if t0_is_weth else R0/R1

        # Final Calc
        if token.lower() == self.WETH.lower():
            if weth_usd_price:
                return float(weth_usd_price)
        if best_price_weth and weth_usd_price:
            return float(best_price_weth * weth_usd_price)

        # 3. Direct V2 Token->USDC fallback
        d = pool_data.get("v2_T-U")
        if d and "reserves" in d:
            r0, r1 = d["reserves"]
            if r0 > 0 and r1 > 0:
                t0_is_token = token_lower < self.USDC.lower()
                d0, d1 = (dec_token, 6) if t0_is_token else (6, dec_token)
                R0 = Decimal(r0) / Decimal(10)**d0
                R1 = Decimal(r1) / Decimal(10)**d1
                p = R1/R0 if t0_is_token else R0/R1
                return float(p)

        return None