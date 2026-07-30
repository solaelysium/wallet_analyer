from __future__ import annotations

import threading
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
from .providers import ProviderBundle
from .repositories import JobRepository, TokenRepository, log_event, sqlite_upsert


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
        self.executor = ThreadPoolExecutor(
            max_workers=max(1, max_workers),
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
                log_event(
                    session, "info", "job.started",
                    f"Collection job started with {len(item_ids)} queued items",
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
            if not item_ids:
                self._finalize(job_id)
        finally:
            with self._lock:
                self._starting.discard(job_id)

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
                item.state = "running"
                item.stage = "rpc"
                item.attempts += 1
                item.started_at = utcnow()
                wallet_id = item.wallet_id
                wallet = session.get(Wallet, wallet_id)
                address = wallet.address if wallet else ""
                log_event(
                    session,
                    "info",
                    "job_item.started",
                    f"Collecting {address}",
                    job_id=job_id,
                    job_item_id=item_id,
                )
            if not address:
                raise ValueError("Кошелёк не найден")
            self._cancel_check(job_id)
            latest_block = self.providers.rpc.latest_block()
            is_wallet = self.providers.rpc.is_wallet(address)
            if is_wallet is None:
                raise RuntimeError("Не удалось определить тип адреса")
            if not is_wallet:
                raise ValueError("Адрес относится к смарт-контракту, а не к кошельку")
            balance_wei = self.providers.rpc.balance_wei(address)

            self._set_stage(item_id, "normal_transactions")
            normal_rows = self.providers.explorer.normal_transactions(
                address, lambda: self._cancel_check(job_id)
            )
            self._persist_normals(wallet_id, normal_rows)

            self._cancel_check(job_id)
            self._set_stage(item_id, "internal_transactions")
            internal_rows = self.providers.explorer.internal_transactions(
                address, lambda: self._cancel_check(job_id)
            )
            self._persist_internals(wallet_id, internal_rows)

            self._cancel_check(job_id)
            self._set_stage(item_id, "token_transfers")
            transfer_rows = self.providers.explorer.token_transfers(
                address, lambda: self._cancel_check(job_id)
            )
            self._persist_transfers(wallet_id, transfer_rows)

            self._cancel_check(job_id)
            self._set_stage(item_id, "features")
            with self.database.session() as session:
                wallet = session.get(Wallet, wallet_id)
                if wallet is None:
                    raise ValueError("Кошелёк исчез во время сбора данных")
                wallet.is_eoa = True
                calculator = FeatureCalculator(
                    session, self.providers.prices, self.providers.rpc
                )
                calculator.calculate(wallet, latest_block, balance_wei)
                wallet.last_collected_at = utcnow()
                item = session.get(JobItem, item_id)
                item.state = "completed"
                item.stage = "completed"
                item.checkpoint = {"as_of_block": latest_block}
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
        except JobCancelled as exc:
            self._fail_item(job_id, item_id, "cancelled", str(exc))
        except Exception as exc:
            self._fail_item(
                job_id,
                item_id,
                "failed",
                f"{type(exc).__name__}: {exc}",
            )
        finally:
            self._finalize(job_id)

    def _fail_item(
        self, job_id: int, item_id: int, state: str, message: str
    ) -> None:
        with self.database.session() as session:
            item = session.get(JobItem, item_id)
            if item:
                item.state = state
                item.stage = state
                item.error = message[:4000]
                item.finished_at = utcnow()
            log_event(
                session,
                "warning" if state == "cancelled" else "error",
                f"job_item.{state}",
                message,
                job_id=job_id,
                job_item_id=item_id,
            )

    def _persist_normals(self, wallet_id: int, rows: list[dict]) -> None:
        with self.database.session() as session:
            wallet = session.get(Wallet, wallet_id)
            for row in rows:
                values = {
                    "chain_id": wallet.chain_id,
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
                        session, NormalTransaction, values, ["wallet_id", "tx_hash"]
                    )

    def _persist_internals(self, wallet_id: int, rows: list[dict]) -> None:
        with self.database.session() as session:
            wallet = session.get(Wallet, wallet_id)
            for index, row in enumerate(rows):
                values = {
                    "chain_id": wallet.chain_id,
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

    def _persist_transfers(self, wallet_id: int, rows: list[dict]) -> None:
        with self.database.session() as session:
            wallet = session.get(Wallet, wallet_id)
            tokens = TokenRepository(session)
            for index, row in enumerate(rows):
                token_address = str(row.get("contractAddress", "")).lower()
                tx_hash = str(row.get("hash", "")).lower()
                if not token_address or not tx_hash:
                    continue
                token = tokens.get_or_create(
                    wallet.chain_id,
                    token_address,
                    str(row.get("tokenSymbol", "")) or None,
                    str(row.get("tokenName", "")) or None,
                    safe_int(row.get("tokenDecimal"), 18),
                )
                values = {
                    "chain_id": wallet.chain_id,
                    "wallet_id": wallet_id,
                    "token_id": token.id,
                    "tx_hash": tx_hash,
                    "log_index": safe_int(
                        row.get("logIndex", row.get("transactionIndex", index))
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
            repository = JobRepository(session)
            repository.update_progress(job_id)
            job = session.get(Job, job_id)
            if job and job.state in {
                "completed",
                "completed_with_errors",
                "cancelled",
            }:
                log_event(
                    session,
                    "info",
                    "job.finished",
                    f"Job finished with state {job.state}",
                    job_id=job_id,
                )

    def _future_done(self, job_id: int, future: Future) -> None:
        with self._lock:
            active = self._futures.get(job_id)
            if active is not None:
                active.discard(future)
                if not active:
                    self._futures.pop(job_id, None)

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=False)

    def reconfigure_workers(self, max_workers: int) -> bool:
        with self._lock:
            if self._starting or any(self._futures.values()):
                return False
            old_executor = self.executor
            self.executor = ThreadPoolExecutor(
                max_workers=max(1, max_workers),
                thread_name_prefix="wallet-collector",
            )
        old_executor.shutdown(wait=False, cancel_futures=False)
        return True
