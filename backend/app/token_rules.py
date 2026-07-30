def is_suspicious_token_symbol(symbol: str | None) -> bool:
    """Mark symbols with non-ASCII characters as suspicious."""
    return bool(symbol) and any(ord(character) > 127 for character in symbol)
