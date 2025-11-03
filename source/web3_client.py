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
    def __init__(self, RPC_URL: str, NETWORK: str, COVALENT_API_KEY: str, etherscan_api_key: str | None = None) -> None:
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
        #!!!! BUT I CHANGED ORIGINAL CODE IN `etherscan.py` FILE TO ADD MULTI-CHAIN SUPPORT
        # Rotate Etherscan keys per client instance
        api_keys = CONFIGS.CRYPTO.ETHERSCAN_API_KEYS or []
        api_key = etherscan_api_key if etherscan_api_key is not None else (api_keys[0] if api_keys else "")
        self.etherscan_client: Etherscan = Etherscan(api_key=api_key, use_v2=True, chain_id=self.chain_id)

        # * Documentation: https://docs.coingecko.com/v3.0.1/reference/introduction
        self.coingecko_client: CoingeckoClient = CoingeckoClient(API_KEY=CONFIGS.COINGECKO.API_KEY)

        # In-memory caches
        self._token_meta_cache: dict[str, tuple[str, int]] = {}

        # Uniswap factories and common tokens (Ethereum mainnet)
        self.UNISWAP_V3_FACTORY = self.w3.to_checksum_address("0x1F98431c8aD98523631AE4a59f267346ea31F984")
        self.UNISWAP_V2_FACTORY = self.w3.to_checksum_address("0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f")
        self.WETH = self.w3.to_checksum_address("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")
        self.USDC = self.w3.to_checksum_address("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")
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

    def get_is_wallet(self, address: str) -> bool:
        """
        Get if the address is a wallet

        Args:
        - `address (str)`: Address of the account

        Returns:
        - `bool`: True if the address is a wallet, False otherwise
        """
        try:
            code = self.w3.eth.get_code(self.w3.to_checksum_address(address))
        except Exception as e:
            logger.error(f"Error getting code for address {address}: {e}")
            return None
        return code == b"" or code == b"0x"

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

        This balance is in basement units

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
                response = float(response) / 10**18
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
                        resp["balance"] = float(resp["balance"]) / 10**18
                except Exception as e:
                    logger.error(f"Error getting balance: {e}")
                    raise Exception(f"Error getting balance: {e}")

                for resp in response:
                    if resp["balance"] == 0.0:
                        logger.warning(f"Address {resp['account']} has 0 balance")

                return response

    async def get_erc20_transactions_by_block_range_async(
        self,
        wallet_address: str | None = None,
        contract_address: str | None = None,
        startblock: int | None = None,
        endblock: int | None = None,
        sort: str = "asc",
        timeout_sec: float | None = None,
    ) -> list[dict]:
        """
        Async wrapper for get_erc20_transactions_by_block_range using a thread offload.

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
        >>> response = await web3_client.get_erc20_transactions_by_block_range_async(
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
                asyncio.to_thread(self.get_erc20_transactions_by_block_range, wallet_address, contract_address, startblock, endblock, sort),
                timeout=timeout_sec,
            )
        except asyncio.TimeoutError:
            raise Exception(f"Etherscan fetch timed out after {timeout_sec}s")

    # TODO: Improve searching logic, I had not done this function
    def get_erc20_transactions_by_block_range(
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
        >>> response = web3_client.get_erc20_transactions_by_block_range(wallet_address="0x742d35Cc6634C0532925a3b844Bc454e4438f44e", sort="asc")
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
                response = self._queued_etherscan(
                    self.etherscan_client.get_erc20_token_transfer_events_by_address,
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
                raise Exception(f"Error getting transactions: {err_name}: {err_msg}")
        elif search_type == "SEARCH_BY_CONTRACT":
            # TODO: Implement search by contract
            pass
        else:
            # TODO: Implement search by both
            pass

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

    def get_token_price_usd_at_block(self, token_address: str, block_number: int) -> float | None:
        """
        Spot price via Uniswap V3/V2 composed routes at a given block.
        Strategy:
          1) V3: token<->WETH pool across common fee tiers; compose with WETH<->USDC V3
          2) V2: token<->WETH, then WETH<->USDC
          3) V2: token<->USDC direct
        Returns float price in USD or None if not found/illiquid.
        """
        try:
            token = self.w3.to_checksum_address(token_address)
        except Exception:
            return None

        # minimal ABIs
        v3_factory_abi = [
            {
                "inputs": [
                    {"internalType": "address", "name": "tokenA", "type": "address"},
                    {"internalType": "address", "name": "tokenB", "type": "address"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                ],
                "name": "getPool",
                "outputs": [{"internalType": "address", "name": "pool", "type": "address"}],
                "stateMutability": "view",
                "type": "function",
            }
        ]
        v2_factory_abi = [
            {
                "inputs": [
                    {"internalType": "address", "name": "tokenA", "type": "address"},
                    {"internalType": "address", "name": "tokenB", "type": "address"},
                ],
                "name": "getPair",
                "outputs": [{"internalType": "address", "name": "pair", "type": "address"}],
                "stateMutability": "view",
                "type": "function",
            }
        ]
        v3_pool_abi = [
            {
                "name": "slot0",
                "outputs": [
                    {"type": "uint160", "name": "sqrtPriceX96"},
                    {"type": "int24", "name": "tick"},
                    {"type": "uint16", "name": "observationIndex"},
                    {"type": "uint16", "name": "observationCardinality"},
                    {"type": "uint16", "name": "observationCardinalityNext"},
                    {"type": "uint8", "name": "feeProtocol"},
                    {"type": "bool", "name": "unlocked"},
                ],
                "stateMutability": "view",
                "type": "function",
            },
            {"name": "token0", "outputs": [{"type": "address", "name": ""}], "stateMutability": "view", "type": "function"},
            {"name": "token1", "outputs": [{"type": "address", "name": ""}], "stateMutability": "view", "type": "function"},
            {"name": "liquidity", "outputs": [{"type": "uint128", "name": ""}], "stateMutability": "view", "type": "function"},
        ]
        v2_pair_abi = [
            {
                "name": "getReserves",
                "outputs": [
                    {"type": "uint112", "name": "_reserve0"},
                    {"type": "uint112", "name": "_reserve1"},
                    {"type": "uint32", "name": "_blockTimestampLast"},
                ],
                "stateMutability": "view",
                "type": "function",
            },
            {"name": "token0", "outputs": [{"type": "address", "name": ""}], "stateMutability": "view", "type": "function"},
            {"name": "token1", "outputs": [{"type": "address", "name": ""}], "stateMutability": "view", "type": "function"},
        ]

        def sqrtprice_to_price(sqrtX96: int) -> Decimal:
            s = Decimal(int(sqrtX96))
            denom = Decimal(2) ** 96
            return (s / denom) ** 2

        def decimals_at(addr: str) -> int:
            try:
                # reuse meta cache/db
                _, dec = self.get_token_meta(addr)
                return int(dec)
            except Exception:
                return 18

        # 1) V3 token<->WETH
        v3_factory = self.w3.eth.contract(address=self.UNISWAP_V3_FACTORY, abi=v3_factory_abi)
        for fee in self.UNI_V3_FEE_TIERS:
            try:
                pool = v3_factory.functions.getPool(token, self.WETH, fee).call(block_identifier=block_number)
            except Exception:
                pool = None
            if not pool or int(pool, 16) == 0:
                continue
            try:
                pool_c = self.w3.eth.contract(address=pool, abi=v3_pool_abi)
                slot0 = pool_c.functions.slot0().call(block_identifier=block_number)
                liquidity = pool_c.functions.liquidity().call(block_identifier=block_number)
                if int(liquidity) == 0:
                    continue
                t0 = pool_c.functions.token0().call(block_identifier=block_number)
                t1 = pool_c.functions.token1().call(block_identifier=block_number)
                price_raw = sqrtprice_to_price(slot0[0])  # token1 per token0
                d0 = decimals_at(t0)
                d1 = decimals_at(t1)
                price_adj = price_raw * (Decimal(10) ** (d0 - d1))
                if token.lower() == t0.lower():
                    token_in_weth = price_adj
                else:
                    token_in_weth = Decimal(1) / price_adj

                # WETH -> USDC via V3
                weth_usd = None
                for f2 in self.UNI_V3_FEE_TIERS:
                    try:
                        pool2 = v3_factory.functions.getPool(self.WETH, self.USDC, f2).call(block_identifier=block_number)
                    except Exception:
                        pool2 = None
                    if not pool2 or int(pool2, 16) == 0:
                        continue
                    pc2 = self.w3.eth.contract(address=pool2, abi=v3_pool_abi)
                    slot2 = pc2.functions.slot0().call(block_identifier=block_number)
                    liq2 = pc2.functions.liquidity().call(block_identifier=block_number)
                    if int(liq2) == 0:
                        continue
                    pr2 = sqrtprice_to_price(slot2[0])
                    dd0 = decimals_at(pc2.functions.token0().call(block_identifier=block_number))
                    dd1 = decimals_at(pc2.functions.token1().call(block_identifier=block_number))
                    pr2_adj = pr2 * (Decimal(10) ** (dd0 - dd1))
                    # if token0 is WETH, price is token1 per token0 => USDC per WETH
                    t0_2 = pc2.functions.token0().call(block_identifier=block_number)
                    if self.WETH.lower() == t0_2.lower():
                        weth_usd = pr2_adj
                    else:
                        weth_usd = Decimal(1) / pr2_adj
                    if weth_usd and weth_usd > 0:
                        break
                if weth_usd and weth_usd > 0:
                    return float((token_in_weth * weth_usd))
            except Exception:
                continue

        # 2) V2 token<->WETH then WETH<->USDC
        v2_factory = self.w3.eth.contract(address=self.UNISWAP_V2_FACTORY, abi=v2_factory_abi)
        try:
            pair_tw = v2_factory.functions.getPair(token, self.WETH).call(block_identifier=block_number)
        except Exception:
            pair_tw = None
        if pair_tw and int(pair_tw, 16) != 0:
            try:
                pair_abi_c = self.w3.eth.contract(address=pair_tw, abi=v2_pair_abi)
                r0, r1, _ = pair_abi_c.functions.getReserves().call(block_identifier=block_number)
                t0 = pair_abi_c.functions.token0().call(block_identifier=block_number)
                t1 = pair_abi_c.functions.token1().call(block_identifier=block_number)
                if int(r0) + int(r1) > 0:
                    dec0 = decimals_at(t0)
                    dec1 = decimals_at(t1)
                    R0 = Decimal(r0) / (Decimal(10) ** dec0)
                    R1 = Decimal(r1) / (Decimal(10) ** dec1)
                    if token.lower() == t0.lower():
                        price_in_weth = R1 / R0
                    else:
                        price_in_weth = R0 / R1

                    pair_wu = v2_factory.functions.getPair(self.WETH, self.USDC).call(block_identifier=block_number)
                    if pair_wu and int(pair_wu, 16) != 0:
                        pwu = self.w3.eth.contract(address=pair_wu, abi=v2_pair_abi)
                        rr0, rr1, _ = pwu.functions.getReserves().call(block_identifier=block_number)
                        tt0 = pwu.functions.token0().call(block_identifier=block_number)
                        tt1 = pwu.functions.token1().call(block_identifier=block_number)
                        if int(rr0) + int(rr1) > 0:
                            d0 = decimals_at(tt0)
                            d1 = decimals_at(tt1)
                            RR0 = Decimal(rr0) / (Decimal(10) ** d0)
                            RR1 = Decimal(rr1) / (Decimal(10) ** d1)
                            weth_usd = RR1 / RR0 if tt0.lower() == self.WETH.lower() else RR0 / RR1
                            if weth_usd and weth_usd > 0:
                                return float((price_in_weth * weth_usd))
            except Exception:
                pass

        # 3) V2 token<->USDC
        try:
            pair_tu = v2_factory.functions.getPair(token, self.USDC).call(block_identifier=block_number)
        except Exception:
            pair_tu = None
        if pair_tu and int(pair_tu, 16) != 0:
            try:
                ptu = self.w3.eth.contract(address=pair_tu, abi=v2_pair_abi)
                r0, r1, _ = ptu.functions.getReserves().call(block_identifier=block_number)
                t0 = ptu.functions.token0().call(block_identifier=block_number)
                t1 = ptu.functions.token1().call(block_identifier=block_number)
                if int(r0) + int(r1) > 0:
                    d0 = decimals_at(t0)
                    d1 = decimals_at(t1)
                    R0 = Decimal(r0) / (Decimal(10) ** d0)
                    R1 = Decimal(r1) / (Decimal(10) ** d1)
                    price_usd = R1 / R0 if t0.lower() == token.lower() else R0 / R1
                    return float(price_usd)
            except Exception:
                pass

        return None

    # Removed Uniswap spot fallback method
