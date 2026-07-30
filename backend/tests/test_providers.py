import pytest

from app.key_pool import KeyPool
from app.providers import InfuraProvider


def test_wallet_check_accepts_empty_and_short_bytecode(monkeypatch) -> None:
    provider = InfuraProvider(KeyPool({}))

    monkeypatch.setattr(provider, "rpc", lambda *_: "0x")
    assert provider.is_wallet("0x0000000000000000000000000000000000000001")

    monkeypatch.setattr(provider, "rpc", lambda *_: f"0x{'ab' * 23}")
    assert provider.is_wallet("0x0000000000000000000000000000000000000002")


def test_wallet_check_rejects_long_bytecode(monkeypatch) -> None:
    provider = InfuraProvider(KeyPool({}))
    monkeypatch.setattr(provider, "rpc", lambda *_: f"0x{'ab' * 24}")

    assert provider.is_wallet("0x0000000000000000000000000000000000000003") is False


def test_wallet_check_returns_none_on_rpc_error(monkeypatch) -> None:
    provider = InfuraProvider(KeyPool({}))

    def fail(*_):
        raise RuntimeError("RPC unavailable")

    monkeypatch.setattr(provider, "rpc", fail)

    assert provider.is_wallet("0x0000000000000000000000000000000000000004") is None


def test_wallet_check_rejects_empty_address() -> None:
    provider = InfuraProvider(KeyPool({}))

    with pytest.raises(ValueError, match="Адрес не указан"):
        provider.is_wallet("")


def test_token_balance_uses_erc20_balance_of(monkeypatch) -> None:
    provider = InfuraProvider(KeyPool({}))
    captured = {}

    def eth_call(to: str, data: str, block: int | str = "latest") -> str:
        captured.update({"to": to, "data": data, "block": block})
        return hex(123_000_000)

    monkeypatch.setattr(provider, "eth_call", eth_call)
    token = "0x00000000000000000000000000000000000000aa"
    wallet = "0x0000000000000000000000000000000000000011"

    assert provider.token_balance(token, wallet, 100) == 123_000_000
    assert captured == {
        "to": token,
        "data": "0x70a08231" + wallet[2:].zfill(64),
        "block": 100,
    }
