import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class CryptoConfig:
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
class CoingeckoConfig:
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
class GlobalConfig:
    """
    Global configuration class
    """

    CRYPTO: CryptoConfig
    COINGECKO: CoingeckoConfig

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
    crypto_config = CryptoConfig(
        INFURA_API_KEYS=infura_keys,
        ETHERSCAN_API_KEYS=etherscan_keys,
        COVALENT_API_KEYS=cov_keys,
    )

    # For coingecko
    coingecko_config = CoingeckoConfig(API_KEY=os.getenv("COINGECKO_API_KEY"))

    # Aggregated config
    global_config = GlobalConfig(
        CRYPTO=crypto_config,
        COINGECKO=coingecko_config,
    )

    return global_config


# Global environment variable
CONFIGS = load_config()
