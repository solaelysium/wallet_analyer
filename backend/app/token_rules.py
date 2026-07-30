NATIVE_ADDRESS = "0x0000000000000000000000000000000000000000"
WETH_ADDRESS = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
STABLECOINS = {
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6),
    "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6),
    "0x6b175474e89094c44da98b954eedeac495271d0f": ("DAI", 18),
}
ETH_LIKE_ADDRESSES = {NATIVE_ADDRESS, WETH_ADDRESS}


def is_suspicious_token_symbol(symbol: str | None) -> bool:
    """Mark symbols with non-ASCII characters as suspicious."""
    return bool(symbol) and any(ord(character) > 127 for character in symbol)


def canonical_decimals(address: str) -> int | None:
    metadata = STABLECOINS.get(address.lower())
    if metadata:
        return metadata[1]
    if address.lower() in ETH_LIKE_ADDRESSES:
        return 18
    return None


def asset_kind(address: str) -> str:
    normalized = address.lower()
    if normalized == NATIVE_ADDRESS:
        return "native"
    if normalized == WETH_ADDRESS:
        return "weth"
    if normalized in STABLECOINS:
        return "stable"
    return "token"


def is_trusted_quote_asset(address: str) -> bool:
    return asset_kind(address) in {"native", "weth", "stable"}
