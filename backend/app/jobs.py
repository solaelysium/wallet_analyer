from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

from sqlalchemy import func, select

from .analytics import FeatureCalculator, safe_int
from .database import Database
from .models import (
    InternalTransaction,
    Job,
    JobItem,
    NormalTransaction,
    TokenTransfer,
    Wallet,
    utcnow,
)
from .providers import ProviderBundle, TooManyTransactionsError
from .repositories import (
    JobRepository,
    TokenRepository,
    _DB_WRITE_LOCK,
    log_event,
    sqlite_upsert,
)
from .token_rules import STABLECOINS, canonical_decimals

MAX_WALLET_EVENTS = 25_000
PERSIST_CHUNK_SIZE = 400


class JobCancelled(RuntimeError):
    pass


class CollectionJobManager:
    def __init__(
        self,
        database: Database,
        providers: ProviderBundle,
        max_workers: int = 4,
    ) -> None:
        self.database = database
        self.providers = providers
        self.max_workers = max(1, max_workers)
        self.executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="wallet-collector",
        )
        self._lock = threading.Lock()
        self._futures: dict[int, set[Future]] = {}
        self._starting: set[int] = set()

    def start(self, job_id: int) -> None:
        with self._lock:
            if job_id in self._starting or self._futures.get(job_id):
                return
            self._starting.add(job_id)
        idle = False
        try:
            with self.database.session() as session:
                job = session.get(Job, job_id)
                if job is None:
                    raise ValueError("Задача не найдена")
                if job.state not in {"queued", "running"}:
                    return
                job.state = "running"
                job.started_at = job.started_at or utcnow()
                item_ids = list(
                    session.scalars(
                        select(JobItem.id).where(
                            JobItem.job_id == job_id,
                            JobItem.state == "queued",
                        )
                    ).all()
                )
                phase = str((job.parameters or {}).get("phase", "collection"))
                log_event(
                    session, "info", "job.started",
                    (
                        f"Features job started with {len(item_ids)} queued items"
                        if phase == "features"
                        else f"Collection job started with {len(item_ids)} queued items"
                    ),
                    job_id=job_id,
                )
            futures = set()
            for item_id in item_ids:
                future = self.executor.submit(self._run_item, job_id, item_id)
                future.add_done_callback(
                    lambda completed, active_job=job_id: self._future_done(
                        active_job, completed
                    )
                )
                futures.add(future)
            with self._lock:
                self._futures.setdefault(job_id, set()).update(futures)
            idle = not item_ids
        finally:
            with self._lock:
                self._starting.discard(job_id)
        if idle:
            # Must run after _starting is cleared, otherwise features phase is skipped.
            self._on_job_idle(job_id)

    def advance_to_features(self, job_id: int) -> Job:
        """Fail non-terminal items and start features for collected wallets."""
        with self._lock:
            if job_id in self._starting:
                raise ValueError("Задача уже активна")
            # Drop tracking for abandoned workers; process restart is preferred,
            # but this unblocks a live stuck job after orphans were marked failed.
            self._futures.pop(job_id, None)
        with self.database.session() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise ValueError("Задача не найдена")
            parameters = dict(job.parameters or {})
            if parameters.get("phase") == "features":
                raise ValueError("Фаза признаков уже запущена")
            stalled = list(
                session.scalars(
                    select(JobItem).where(
                        JobItem.job_id == job_id,
                        JobItem.state.in_(("queued", "running")),
                    )
                ).all()
            )
            for item in stalled:
                item.state = "failed"
                item.stage = "failed"
                item.error = (
                    "Stalled worker — marked failed to unblock features phase"
                )[:4000]
                item.finished_at = utcnow()
            JobRepository(session).update_progress(job_id)
            log_event(
                session,
                "warning",
                "job.advance_features",
                f"Advancing to features; failed {len(stalled)} stalled items",
                job_id=job_id,
                context={"stalled": len(stalled)},
            )
        if not self._begin_features_phase(job_id):
            raise ValueError("Не удалось запустить фазу признаков")
        with self.database.session() as session:
            return session.get(Job, job_id)

    def resume(
        self,
        job_id: int,
        retry_only: bool = False,
        reprocess_completed: bool = False,
    ) -> Job:
        with self._lock:
            if job_id in self._starting or self._futures.get(job_id):
                raise ValueError("Задача уже активна")
        with self.database.session() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise ValueError("Задача не найдена")
            if reprocess_completed:
                states = {"completed", "failed", "cancelled"}
            elif retry_only:
                states = {"failed", "cancelled"}
            else:
                states = {"queued", "running", "failed", "cancelled"}
            items = session.scalars(
                select(JobItem).where(
                    JobItem.job_id == job_id, JobItem.state.in_(states)
                )
            ).all()
            if not items:
                raise ValueError("В задаче нет элементов для повторной обработки")
            for item in items:
                item.state = "queued"
                item.stage = "queued"
                item.error = None
                item.started_at = None
                item.finished_at = None
            if reprocess_completed:
                parameters = dict(job.parameters or {})
                parameters["phase"] = "collection"
                job.parameters = parameters
            completed = session.scalar(
                select(func.count(JobItem.id)).where(
                    JobItem.job_id == job_id, JobItem.state == "completed"
                )
            )
            job.state = "queued"
            job.cancel_requested = False
            job.error = None
            job.started_at = None
            job.finished_at = None
            job.progress_done = int(completed or 0)
            job.progress_total = int(
                session.scalar(
                    select(func.count(JobItem.id)).where(JobItem.job_id == job_id)
                ) or 0
            )
            log_event(
                session, "info", "job.resumed",
                f"Requeued {len(items)} items", job_id=job_id,
                context={
                    "retry_only": retry_only,
                    "reprocess_completed": reprocess_completed,
                },
            )
        self.start(job_id)
        with self.database.session() as session:
            return session.get(Job, job_id)

    def resume_pending(self) -> None:
        with self.database.session() as session:
            job_ids = list(
                session.scalars(
                    select(Job.id).where(
                        Job.kind == "collection",
                        Job.state.in_(("queued", "running")),
                    )
                ).all()
            )
        for job_id in job_ids:
            self.start(job_id)

    def cancel(self, job_id: int) -> Job:
        with self.database.session() as session:
            job = JobRepository(session).request_cancel(job_id)
            if job is None:
                raise ValueError("Задача не найдена")
            session.query(JobItem).filter(
                JobItem.job_id == job_id, JobItem.state == "queued"
            ).update(
                {
                    JobItem.state: "cancelled",
                    JobItem.finished_at: utcnow(),
                    JobItem.stage: "cancelled",
                },
                synchronize_session=False,
            )
            log_event(
                session,
                "info",
                "job.cancel_requested",
                "Cancellation requested",
                job_id=job_id,
            )
            return job

    def reconfigure_workers(self, max_workers: int) -> bool:
        with self._lock:
            if self._starting or any(self._futures.values()):
                return False
            self.max_workers = max(1, max_workers)
            old_executor = self.executor
            self.executor = ThreadPoolExecutor(
                max_workers=self.max_workers,
                thread_name_prefix="wallet-collector",
            )
        old_executor.shutdown(wait=False, cancel_futures=False)
        return True

    def _cancel_check(self, job_id: int) -> None:
        with self.database.session() as session:
            requested = session.scalar(
                select(Job.cancel_requested).where(Job.id == job_id)
            )
        if requested:
            raise JobCancelled("Cancellation requested")

    def _set_stage(self, item_id: int, stage: str, checkpoint: dict | None = None) -> None:
        with self.database.session() as session:
            item = session.get(JobItem, item_id)
            if item:
                item.stage = stage
                if checkpoint is not None:
                    item.checkpoint = checkpoint

    def _run_item(self, job_id: int, item_id: int) -> None:
        try:
            with self.database.session() as session:
                item = session.get(JobItem, item_id)
                if item is None or item.state != "queued":
                    return
                job = session.get(Job, job_id)
                phase = str((job.parameters or {}).get("phase", "collection"))
                item.state = "running"
                item.stage = "features" if phase == "features" else "rpc"
                item.attempts += 1
                item.started_at = utcnow()
                wallet_id = item.wallet_id
                checkpoint = dict(item.checkpoint or {})
                wallet = session.get(Wallet, wallet_id)
                address = wallet.address if wallet else ""
                log_event(
                    session,
                    "info",
                    "job_item.started",
                    (
                        f"Computing features for {address}"
                        if phase == "features"
                        else f"Collecting {address}"
                    ),
                    job_id=job_id,
                    job_item_id=item_id,
                )
            if not address:
                raise ValueError("Кошелёк не найден")
            if phase == "features":
                self._run_features_item(
                    job_id, item_id, wallet_id, address, checkpoint
                )
            else:
                self._run_collection_item(job_id, item_id, wallet_id, address)
        except JobCancelled as exc:
            self._fail_item(job_id, item_id, "cancelled", str(exc))
        except TooManyTransactionsError as exc:
            self._fail_item(
                job_id,
                item_id,
                "skipped",
                (
                    f"Более {MAX_WALLET_EVENTS:,} транзакций "
                    f"({exc.count:,})"
                ).replace(",", " "),
                checkpoint={
                    "event_count": exc.count,
                    "skip_reason": "too_many_transactions",
                },
            )
        except Exception as exc:
            self._fail_item(
                job_id,
                item_id,
                "failed",
                f"{type(exc).__name__}: {exc}",
            )
        finally:
            self._finalize(job_id)

    def _run_collection_item(
        self,
        job_id: int,
        item_id: int,
        wallet_id: int,
        address: str,
    ) -> None:
        self._cancel_check(job_id)
        latest_block = self.providers.rpc.latest_block()
        is_wallet = self.providers.rpc.is_wallet(address)
        if is_wallet is None:
            raise RuntimeError("Не удалось определить тип адреса")
        if not is_wallet:
            raise ValueError("Адрес относится к смарт-контракту, а не к кошельку")
        balance_wei = self.providers.rpc.balance_wei(address)

        collected = 0
        remaining = MAX_WALLET_EVENTS
        try:
            self._set_stage(item_id, "normal_transactions")
            normal_rows = self.providers.explorer.normal_transactions(
                address,
                lambda: self._cancel_check(job_id),
                max_rows=remaining,
            )
            collected = len(normal_rows)
            remaining = MAX_WALLET_EVENTS - collected

            self._cancel_check(job_id)
            self._set_stage(item_id, "internal_transactions")
            internal_rows = self.providers.explorer.internal_transactions(
                address,
                lambda: self._cancel_check(job_id),
                max_rows=remaining,
            )
            collected += len(internal_rows)
            remaining = MAX_WALLET_EVENTS - collected

            self._cancel_check(job_id)
            self._set_stage(item_id, "token_transfers")
            transfer_rows = self.providers.explorer.token_transfers(
                address,
                lambda: self._cancel_check(job_id),
                max_rows=remaining,
            )
            collected += len(transfer_rows)
        except TooManyTransactionsError as exc:
            # Provider count is for the overflowing stream only; include prior phases.
            at_least = collected + max(int(exc.count), 1)
            if at_least <= MAX_WALLET_EVENTS:
                at_least = MAX_WALLET_EVENTS + 1
            raise TooManyTransactionsError(at_least) from exc

        self._persist_normals(wallet_id, normal_rows)
        self._persist_internals(wallet_id, internal_rows)
        self._persist_transfers(wallet_id, transfer_rows)

        self._cancel_check(job_id)
        with _DB_WRITE_LOCK:
            with self.database.session() as session:
                wallet = session.get(Wallet, wallet_id)
                if wallet is None:
                    raise ValueError("Кошелёк исчез во время сбора данных")
                wallet.is_eoa = True
                wallet.last_collected_at = utcnow()
                item = session.get(JobItem, item_id)
                item.state = "completed"
                item.stage = "collected"
                item.checkpoint = {
                    "as_of_block": latest_block,
                    "balance_wei": str(balance_wei),
                    "event_count": (
                        len(normal_rows) + len(internal_rows) + len(transfer_rows)
                    ),
                    "normal": len(normal_rows),
                    "internal": len(internal_rows),
                    "transfers": len(transfer_rows),
                }
                item.finished_at = utcnow()
                log_event(
                    session,
                    "info",
                    "job_item.completed",
                    f"Collected {wallet.address}",
                    job_id=job_id,
                    job_item_id=item_id,
                    context={
                        "normal": len(normal_rows),
                        "internal": len(internal_rows),
                        "transfers": len(transfer_rows),
                    },
                )

    def _run_features_item(
        self,
        job_id: int,
        item_id: int,
        wallet_id: int,
        address: str,
        checkpoint: dict,
    ) -> None:
        self._cancel_check(job_id)
        self._set_stage(item_id, "features")
        as_of_block = safe_int(checkpoint.get("as_of_block"))
        if as_of_block <= 0:
            as_of_block = self.providers.rpc.latest_block()
        balance_raw = checkpoint.get("balance_wei")
        if balance_raw is None:
            balance_wei = self.providers.rpc.balance_wei(address)
        else:
            balance_wei = safe_int(balance_raw)

        # Serialize SQLite writes: parallel feature sessions cause "database is locked"
        # on job_items / logs / snapshots under ATTACH.
        last_error: Exception | None = None
        for attempt in range(8):
            try:
                with _DB_WRITE_LOCK:
                    with self.database.session() as session:
                        wallet = session.get(Wallet, wallet_id)
                        if wallet is None:
                            raise ValueError("Кошелёк исчез во время расчёта признаков")
                        calculator = FeatureCalculator(
                            session, self.providers.prices, self.providers.rpc
                        )
                        calculator.calculate(wallet, as_of_block, balance_wei)
                        item = session.get(JobItem, item_id)
                        item.state = "completed"
                        item.stage = "completed"
                        item.error = None
                        item.checkpoint = {
                            **checkpoint,
                            "as_of_block": as_of_block,
                            "balance_wei": str(balance_wei),
                            "features": True,
                        }
                        item.finished_at = utcnow()
                        log_event(
                            session,
                            "info",
                            "job_item.completed",
                            f"Features computed for {wallet.address}",
                            job_id=job_id,
                            job_item_id=item_id,
                        )
                return
            except Exception as exc:
                last_error = exc
                message = str(exc).lower()
                if "locked" not in message and "busy" not in message:
                    raise
                time.sleep(min(0.05 * (1.7**attempt), 2.0))
        raise last_error or RuntimeError("Features write failed")

    def _fail_item(
        self,
        job_id: int,
        item_id: int,
        state: str,
        message: str,
        checkpoint: dict | None = None,
    ) -> None:
        for attempt in range(8):
            try:
                with _DB_WRITE_LOCK:
                    with self.database.session() as session:
                        item = session.get(JobItem, item_id)
                        if item:
                            item.state = state
                            item.stage = state
                            item.error = message[:4000]
                            item.finished_at = utcnow()
                            if checkpoint:
                                item.checkpoint = {
                                    **(item.checkpoint or {}),
                                    **checkpoint,
                                }
                        level = {
                            "cancelled": "warning",
                            "skipped": "warning",
                        }.get(state, "error")
                        log_event(
                            session,
                            level,
                            f"job_item.{state}",
                            message,
                            job_id=job_id,
                            job_item_id=item_id,
                        )
                return
            except Exception as exc:
                text = str(exc).lower()
                if "locked" not in text and "busy" not in text:
                    raise
                time.sleep(min(0.05 * (1.7**attempt), 2.0))

    def _persist_normals(self, wallet_id: int, rows: list[dict]) -> None:
        if not rows:
            return
        chain_id = self._wallet_chain_id(wallet_id)
        for start in range(0, len(rows), PERSIST_CHUNK_SIZE):
            chunk = rows[start : start + PERSIST_CHUNK_SIZE]
            with _DB_WRITE_LOCK:
                with self.database.session() as session:
                    for row in chunk:
                        values = {
                            "chain_id": chain_id,
                            "wallet_id": wallet_id,
                            "tx_hash": str(row.get("hash", "")).lower(),
                            "block_number": safe_int(row.get("blockNumber")),
                            "timestamp": safe_int(row.get("timeStamp")),
                            "from_address": str(row.get("from", "")).lower(),
                            "to_address": str(row.get("to", "")).lower() or None,
                            "value_wei": str(row.get("value", "0")),
                            "gas_used": str(row.get("gasUsed", "0")),
                            "gas_price": str(row.get("gasPrice", "0")),
                            "success": str(row.get("isError", "0")) == "0",
                            "raw": row,
                        }
                        if values["tx_hash"]:
                            sqlite_upsert(
                                session,
                                NormalTransaction,
                                values,
                                ["wallet_id", "tx_hash"],
                            )

    def _persist_internals(self, wallet_id: int, rows: list[dict]) -> None:
        if not rows:
            return
        chain_id = self._wallet_chain_id(wallet_id)
        for start in range(0, len(rows), PERSIST_CHUNK_SIZE):
            chunk = rows[start : start + PERSIST_CHUNK_SIZE]
            with _DB_WRITE_LOCK:
                with self.database.session() as session:
                    for offset, row in enumerate(chunk):
                        index = start + offset
                        values = {
                            "chain_id": chain_id,
                            "wallet_id": wallet_id,
                            "tx_hash": str(row.get("hash", "")).lower(),
                            "trace_id": str(row.get("traceId", index)),
                            "block_number": safe_int(row.get("blockNumber")),
                            "timestamp": safe_int(row.get("timeStamp")),
                            "from_address": str(row.get("from", "")).lower(),
                            "to_address": str(row.get("to", "")).lower() or None,
                            "value_wei": str(row.get("value", "0")),
                            "success": str(row.get("isError", "0")) == "0",
                            "raw": row,
                        }
                        if values["tx_hash"]:
                            sqlite_upsert(
                                session,
                                InternalTransaction,
                                values,
                                ["wallet_id", "tx_hash", "trace_id"],
                            )

    def _wallet_chain_id(self, wallet_id: int) -> int:
        with _DB_WRITE_LOCK:
            with self.database.session() as session:
                wallet = session.get(Wallet, wallet_id)
                if wallet is None:
                    raise ValueError("Кошелёк не найден")
                return int(wallet.chain_id)

    def _resolve_transfer_token_specs(self, rows: list[dict]) -> list[dict]:
        """Resolve unique token metadata/decimals before opening a write session."""
        specs: dict[str, dict] = {}
        for row in rows:
            token_address = str(row.get("contractAddress", "")).lower()
            if not token_address or token_address in specs:
                continue
            decimals = canonical_decimals(token_address)
            if decimals is None:
                try:
                    decimals = self.providers.rpc.token_decimals(
                        token_address,
                        safe_int(row.get("blockNumber")) or "latest",
                    )
                except Exception:
                    decimals = None
            reported_decimals = safe_int(row.get("tokenDecimal"), -1)
            if decimals is None:
                decimals = (
                    reported_decimals if 0 <= reported_decimals <= 255 else 18
                )
            stable_metadata = STABLECOINS.get(token_address)
            specs[token_address] = {
                "address": token_address,
                "symbol": (
                    stable_metadata[0]
                    if stable_metadata
                    else (str(row.get("tokenSymbol", "")) or None)
                ),
                "name": str(row.get("tokenName", "")) or None,
                "decimals": decimals,
            }
        return list(specs.values())

    def _persist_transfers(self, wallet_id: int, rows: list[dict]) -> None:
        if not rows:
            return
        chain_id = self._wallet_chain_id(wallet_id)
        token_specs = self._resolve_transfer_token_specs(rows)
        with _DB_WRITE_LOCK:
            with self.database.session() as session:
                token_map = TokenRepository(session).ensure_many(
                    chain_id, token_specs
                )
                token_ids = {
                    address: token.id for address, token in token_map.items()
                }

        for start in range(0, len(rows), PERSIST_CHUNK_SIZE):
            chunk = rows[start : start + PERSIST_CHUNK_SIZE]
            with _DB_WRITE_LOCK:
                with self.database.session() as session:
                    for offset, row in enumerate(chunk):
                        index = start + offset
                        token_address = str(row.get("contractAddress", "")).lower()
                        tx_hash = str(row.get("hash", "")).lower()
                        if not token_address or not tx_hash:
                            continue
                        token_id = token_ids.get(token_address)
                        if token_id is None:
                            raise RuntimeError(
                                f"Token was not preloaded for {token_address}"
                            )
                        values = {
                            "chain_id": chain_id,
                            "wallet_id": wallet_id,
                            "token_id": token_id,
                            "tx_hash": tx_hash,
                            "log_index": safe_int(
                                row.get(
                                    "logIndex",
                                    row.get("transactionIndex", index),
                                )
                            ),
                            "block_number": safe_int(row.get("blockNumber")),
                            "timestamp": safe_int(row.get("timeStamp")),
                            "from_address": str(row.get("from", "")).lower(),
                            "to_address": str(row.get("to", "")).lower(),
                            "raw_value": str(row.get("value", "0")),
                            "raw": row,
                        }
                        sqlite_upsert(
                            session,
                            TokenTransfer,
                            values,
                            ["wallet_id", "tx_hash", "log_index"],
                        )

    def _finalize(self, job_id: int) -> None:
        with self.database.session() as session:
            JobRepository(session).update_progress(job_id)

    def _on_job_idle(self, job_id: int) -> None:
        with self.database.session() as session:
            JobRepository(session).update_progress(job_id)
            job = session.get(Job, job_id)
            if job is None:
                return
            state = job.state
            phase = str((job.parameters or {}).get("phase", "collection"))
            ready_for_features = (
                job.kind == "collection"
                and phase != "features"
                and not job.cancel_requested
                and state == "running"
                and session.scalar(
                    select(func.count(JobItem.id)).where(
                        JobItem.job_id == job_id,
                        JobItem.state.in_(("queued", "running")),
                    )
                )
                == 0
            )
        if ready_for_features and self._begin_features_phase(job_id):
            return
        if state not in {"completed", "completed_with_errors", "cancelled"}:
            return
        with self.database.session() as session:
            job = session.get(Job, job_id)
            if job is None or job.state not in {
                "completed",
                "completed_with_errors",
                "cancelled",
            }:
                return
            log_event(
                session,
                "info",
                "job.finished",
                f"Job finished with state {job.state}",
                job_id=job_id,
            )

    def _begin_features_phase(self, job_id: int) -> bool:
        with self._lock:
            if self._futures.get(job_id) or job_id in self._starting:
                return False
            with self.database.session() as session:
                job = session.get(Job, job_id)
                if job is None or job.cancel_requested:
                    return False
                parameters = dict(job.parameters or {})
                if parameters.get("phase") == "features":
                    return False
                items = list(
                    session.scalars(
                        select(JobItem).where(
                            JobItem.job_id == job_id,
                            JobItem.state == "completed",
                        )
                    ).all()
                )
                if not items:
                    return False
                for item in items:
                    item.state = "queued"
                    item.stage = "features"
                    item.error = None
                    item.started_at = None
                    item.finished_at = None
                already_done = session.scalar(
                    select(func.count(JobItem.id)).where(
                        JobItem.job_id == job_id,
                        JobItem.state.in_(("failed", "cancelled", "skipped")),
                    )
                )
                parameters["phase"] = "features"
                job.parameters = parameters
                job.state = "queued"
                job.finished_at = None
                job.error = None
                job.progress_done = int(already_done or 0)
                log_event(
                    session,
                    "info",
                    "job.features_phase",
                    f"Starting features phase for {len(items)} wallets",
                    job_id=job_id,
                    context={"wallets": len(items)},
                )
        self.start(job_id)
        return True

    def _future_done(self, job_id: int, future: Future) -> None:
        idle = False
        with self._lock:
            active = self._futures.get(job_id)
            if active is not None:
                active.discard(future)
                if not active:
                    self._futures.pop(job_id, None)
                    idle = True
        if idle:
            self._on_job_idle(job_id)

    def shutdown(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=False)
