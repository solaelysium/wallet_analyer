from __future__ import annotations

import hashlib
import random
import threading
import time
from dataclasses import dataclass, field


class NoProviderKeyError(RuntimeError):
    pass


class RateLimitedError(RuntimeError):
    pass


@dataclass
class KeyState:
    service: str
    value: str
    concurrency: int
    min_interval: float
    label: str = ""
    key_id: int | None = None
    active: int = 0
    next_allowed: float = 0.0
    cooldown_until: float = 0.0
    requests: int = 0
    successes: int = 0
    failures: int = 0
    rate_limits: int = 0
    last_error: str | None = None
    enabled: bool = True
    last_persisted_use: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def public_id(self) -> str:
        digest = hashlib.sha256(self.value.encode("utf-8")).hexdigest()[:10]
        return self.label or f"{self.service}-{digest}"


class KeyLease:
    def __init__(self, pool: "KeyPool", state: KeyState) -> None:
        self.pool = pool
        self.state = state

    def __enter__(self) -> str:
        return self.state.value

    def __exit__(self, *_: object) -> None:
        self.pool.release(self.state)


class KeyPool:
    def __init__(
        self,
        keys: dict[str, list[str]] | None = None,
        *,
        concurrency: int = 2,
        rps: dict[str, float] | None = None,
        cooldown_seconds: float = 30.0,
        max_retries: int = 4,
        usage_persist_interval: float = 15.0,
        on_key_used=None,
    ) -> None:
        self._condition = threading.Condition()
        self._cursor: dict[str, int] = {}
        self._states: dict[str, list[KeyState]] = {}
        self.cooldown_seconds = cooldown_seconds
        self.max_retries = max_retries
        self.usage_persist_interval = max(0.0, usage_persist_interval)
        self._on_key_used = on_key_used
        rps = rps or {}
        for service, values in (keys or {}).items():
            self._states[service] = [
                KeyState(
                    service=service,
                    value=value,
                    concurrency=max(1, concurrency),
                    min_interval=1.0 / max(rps.get(service, 1.0), 0.01),
                )
                for value in values
                if value
            ]

    def add_key(
        self,
        service: str,
        value: str,
        *,
        label: str = "",
        concurrency: int = 2,
        rps: float = 1.0,
    ) -> None:
        with self._condition:
            if any(
                state.value == value and state.enabled
                for state in self._states.get(service, [])
            ):
                return
            self._states.setdefault(service, []).append(
                KeyState(
                    service=service,
                    value=value,
                    label=label,
                    concurrency=max(1, concurrency),
                    min_interval=1.0 / max(rps, 0.01),
                )
            )
            self._condition.notify_all()

    def remove_key(self, service: str, value: str) -> None:
        with self._condition:
            states = self._states.get(service, [])
            for state in states:
                if state.value == value:
                    state.enabled = False
            self._states[service] = [state for state in states if state.enabled or state.active]
            self._condition.notify_all()

    def reconfigure(
        self,
        keys: dict[str, list[dict]],
        *,
        concurrency: int,
        rps: dict[str, float],
        cooldown_seconds: float,
        max_retries: int,
    ) -> None:
        with self._condition:
            for states in self._states.values():
                for state in states:
                    state.enabled = False
            self.cooldown_seconds = cooldown_seconds
            self.max_retries = max_retries
            for service, rows in keys.items():
                existing = {
                    state.value: state
                    for state in self._states.get(service, [])
                    if state.active == 0
                }
                configured = []
                for row in rows:
                    value = row["value"]
                    state = existing.get(value)
                    if state is None:
                        state = KeyState(
                            service=service,
                            value=value,
                            concurrency=max(1, concurrency),
                            min_interval=1.0 / max(rps.get(service, 1.0), 0.01),
                        )
                    state.label = row.get("label", "")
                    state.enabled = bool(row.get("enabled", True))
                    raw_id = row.get("id")
                    state.key_id = int(raw_id) if raw_id is not None else None
                    state.concurrency = max(1, concurrency)
                    state.min_interval = 1.0 / max(
                        rps.get(service, 1.0), 0.01
                    )
                    configured.append(state)
                active_old = [
                    state
                    for state in self._states.get(service, [])
                    if state.active > 0 and state not in configured
                ]
                self._states[service] = configured + active_old
                self._cursor[service] = 0
            self._condition.notify_all()

    def acquire(self, service: str, timeout: float | None = None) -> KeyLease:
        started = time.monotonic()
        with self._condition:
            while True:
                states = [
                    state
                    for state in self._states.get(service, [])
                    if state.enabled
                ]
                if not states:
                    raise NoProviderKeyError(f"No configured {service} API keys")
                now = time.monotonic()
                start = self._cursor.get(service, 0) % len(states)
                ordered = states[start:] + states[:start]
                available = [
                    state
                    for state in ordered
                    if state.active < state.concurrency
                    and state.cooldown_until <= now
                    and state.next_allowed <= now
                ]
                if available:
                    state = min(available, key=lambda item: (item.active, item.next_allowed))
                    state.active += 1
                    state.requests += 1
                    state.next_allowed = now + state.min_interval
                    self._cursor[service] = (states.index(state) + 1) % len(states)
                    return KeyLease(self, state)

                wake_candidates = [
                    max(state.cooldown_until, state.next_allowed)
                    for state in states
                    if state.active < state.concurrency
                ]
                wake_at = min(wake_candidates) if wake_candidates else now + 0.05
                wait = max(0.01, min(wake_at - now, 0.25))
                if timeout is not None:
                    remaining = timeout - (now - started)
                    if remaining <= 0:
                        raise TimeoutError(f"Timed out waiting for a {service} API key")
                    wait = min(wait, remaining)
                self._condition.wait(wait)

    def release(self, state: KeyState) -> None:
        with self._condition:
            state.active = max(0, state.active - 1)
            if not state.enabled and state.active == 0:
                states = self._states.get(state.service, [])
                self._states[state.service] = [item for item in states if item is not state]
            self._condition.notify_all()

    @staticmethod
    def _is_rate_limit(exc: Exception) -> bool:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        text = str(exc).lower()
        return status == 429 or "429" in text or "rate limit" in text

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        text = str(exc).lower()
        return (
            status in {429, 500, 502, 503, 504}
            or "timeout" in text
            or "temporar" in text
            or KeyPool._is_rate_limit(exc)
        )

    def set_on_key_used(self, callback) -> None:
        self._on_key_used = callback

    def _record_use(self, state: KeyState) -> None:
        callback = self._on_key_used
        if callback is None:
            return
        now = time.monotonic()
        if (
            self.usage_persist_interval > 0
            and now - state.last_persisted_use < self.usage_persist_interval
        ):
            return
        state.last_persisted_use = now
        try:
            callback(state.key_id, state.service, state.value)
        except Exception:
            # Usage tracking must never break provider calls.
            return

    def call(self, service: str, operation, *args, **kwargs):
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            lease = self.acquire(service)
            state = lease.state
            try:
                with lease as key:
                    result = operation(key, *args, **kwargs)
                state.successes += 1
                state.last_error = None
                self._record_use(state)
                return result
            except Exception as exc:
                last_error = exc
                state.failures += 1
                state.last_error = f"{type(exc).__name__}: {exc}"[:500]
                # Still mark the key as used — the request was made.
                self._record_use(state)
                if self._is_rate_limit(exc):
                    state.rate_limits += 1
                    state.cooldown_until = time.monotonic() + self.cooldown_seconds
                if attempt >= self.max_retries or not self._is_transient(exc):
                    raise
                delay = min(2**attempt, 30.0) + random.uniform(0.0, 0.5)
                time.sleep(delay)
        raise last_error or RuntimeError("Provider call failed")

    def health(self) -> dict[str, list[dict]]:
        now = time.monotonic()
        return {
            service: [
                {
                    "id": state.public_id,
                    "active": state.active,
                    "enabled": state.enabled,
                    "cooldown_seconds": round(max(0.0, state.cooldown_until - now), 3),
                    "requests": state.requests,
                    "successes": state.successes,
                    "failures": state.failures,
                    "rate_limits": state.rate_limits,
                    "last_error": state.last_error,
                }
                for state in states
            ]
            for service, states in self._states.items()
        }
