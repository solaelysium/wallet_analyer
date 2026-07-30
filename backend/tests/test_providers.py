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
