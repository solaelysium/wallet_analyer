import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class configCrypto:
    """
    Crypto configuration class
    """

    INFURA_API_KEYS: list[str]
    ETHERSCAN_API_KEYS: list[str]
    COVALENT_API_KEYS: list[str] | None = None

    ETHEREUM_MAINNET: str = None
    BSC_MAINNET: str = None
    ARBITRUM_MAINNET: str = None

    ETHEREUM_NETWORK: str = "ethereum"
    BSC_NETWORK: str = "bsc"
    ARBITRUM_NETWORK: str = "arbitrum"

    ETHEREUM_CHAIN_NAME: str = "eth-mainnet"
    BSC_CHAIN_NAME: str = "bsc-mainnet"
    ARBITRUM_CHAIN_NAME: str = "arbitrum-mainnet"

    # Exceptions handling
    def __post_init__(self):
        # Check if infura key is provided
        if not self.INFURA_API_KEYS:
            raise ValueError("Infura keys are required")

        # default to first key for these prebuilt URLs; not used when multi-key is active
        first = self.INFURA_API_KEYS[0]
        self.ETHEREUM_MAINNET = f"https://mainnet.infura.io/v3/{first}"
        self.BSC_MAINNET = f"https://bsc-mainnet.infura.io/v3/{first}"
        self.ARBITRUM_MAINNET = f"https://arbitrum-mainnet.infura.io/v3/{first}"

        # Covalent is optional now; allow empty keys
        if self.COVALENT_API_KEYS is None:
            self.COVALENT_API_KEYS = []


@dataclass
class configUniswapABI:
    """
    Uniswap ABI configuration class
    """

    V3_FACTORY_ABI: list[dict]
    V2_FACTORY_ABI: list[dict]
    V3_POOL_ABI: list[dict]
    V2_PAIR_ABI: list[dict]

    def __post_init__(self):
        # Check if abi is provided
        if not self.V3_FACTORY_ABI:
            raise ValueError("V3 factory abi is required")
        if not self.V2_FACTORY_ABI:
            raise ValueError("V2 factory abi is required")
        if not self.V3_POOL_ABI:
            raise ValueError("V3 pool abi is required")
        if not self.V2_PAIR_ABI:
            raise ValueError("V2 pair abi is required")


@dataclass
class configCoingecko:
    """
    Coingecko configuration class
    """

    API_KEY: str

    # Exceptions handling
    def __post_init__(self):
        # Check if api key is provided
        if not self.API_KEY:
            raise ValueError("API key is required")


@dataclass
class configGlobal:
    """
    Global configuration class
    """

    CRYPTO: configCrypto
    COINGECKO: configCoingecko
    UNISWAP_ABI: configUniswapABI

    OUTPUT_DIR: str = "outputs"

    # Exceptions handling
    def __post_init__(self):
        # Create output directory if it doesn't exist
        os.makedirs(self.OUTPUT_DIR, exist_ok=True)


def load_config():
    cov_keys_env = os.getenv("COVALENT_API_KEY", "") or ""
    cov_keys = [key.strip() for key in cov_keys_env.split(",") if key.strip()]
    infura_env = os.getenv("INFURA_API_KEYS", "") or os.getenv("INFURA_API_KEY", "") or ""
    infura_keys = [k.strip() for k in infura_env.split(",") if k.strip()]
    etherscan_env = os.getenv("ETHERSCAN_API_KEYS", "") or os.getenv("ETHERSCAN_API_KEY", "") or ""
    etherscan_keys = [k.strip() for k in etherscan_env.split(",") if k.strip()]
    crypto_config = configCrypto(
        INFURA_API_KEYS=infura_keys,
        ETHERSCAN_API_KEYS=etherscan_keys,
        COVALENT_API_KEYS=cov_keys,
    )

    # --- Uniswap ABI config from web3_client.py (lines 34-115) ---
    v3_factory_abi = [
        {
            "inputs": [
                {"type": "address", "name": "tA"},
                {"type": "address", "name": "tB"},
                {"type": "uint24", "name": "fee"},
            ],
            "name": "getPool",
            "outputs": [
                {"type": "address", "name": ""}
            ],
            "type": "function",
        }
    ]

    v2_factory_abi = [
        {
            "inputs": [
                {"type": "address", "name": "tA"},
                {"type": "address", "name": "tB"},
            ],
            "name": "getPair",
            "outputs": [
                {"type": "address", "name": ""}
            ],
            "type": "function",
        }
    ]

    v3_pool_abi = [
        {
            "inputs": [],
            "name": "slot0",
            "outputs": [
                {"type": "uint160", "name": "sqrtPriceX96"},
                {"type": "int24"},
                {"type": "uint16"},
                {"type": "uint16"},
                {"type": "uint16"},
                {"type": "uint8"},
                {"type": "bool"},
            ],
            "type": "function",
        },
        {
            "inputs": [],
            "name": "liquidity",
            "outputs": [
                {"type": "uint128", "name": ""}
            ],
            "type": "function",
        },
        {
            "inputs": [],
            "name": "token0",
            "outputs": [
                {"type": "address", "name": ""}
            ],
            "type": "function",
        },
    ]

    v2_pair_abi = [
        {
            "inputs": [],
            "name": "getReserves",
            "outputs": [
                {"type": "uint112", "name": "r0"},
                {"type": "uint112", "name": "r1"},
                {"type": "uint32", "name": "ts"},
            ],
            "type": "function",
        },
        {
            "inputs": [],
            "name": "token0",
            "outputs": [
                {"type": "address", "name": ""}
            ],
            "type": "function",
        },
    ]

    uniswap_ABI_config = configUniswapABI(
        V3_FACTORY_ABI=v3_factory_abi,
        V2_FACTORY_ABI=v2_factory_abi,
        V3_POOL_ABI=v3_pool_abi,
        V2_PAIR_ABI=v2_pair_abi,
    )

    # For coingecko
    coingecko_config = configCoingecko(API_KEY=os.getenv("COINGECKO_API_KEY"))

    # Aggregated config
    global_config = configGlobal(
        CRYPTO=crypto_config,
        COINGECKO=coingecko_config,
        UNISWAP_ABI=uniswap_ABI_config,
    )

    return global_config


# Global environment variable
CONFIGS = load_config()
