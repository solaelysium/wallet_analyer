from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .models import (
    AppLog,
    Job,
    JobItem,
    Token,
    Wallet,
    WalletFeatureSnapshot,
    utcnow,
)
from .token_rules import is_suspicious_token_symbol


class Repository:
    def __init__(self, session: Session, model: type) -> None:
        self.session = session
        self.model = model

    def get(self, entity_id: int):
        return self.session.get(self.model, entity_id)

    def add(self, entity):
        self.session.add(entity)
        self.session.flush()
        return entity

    def list(self, statement: Select | None = None) -> list:
        query = statement if statement is not None else select(self.model)
        return list(self.session.scalars(query).all())


class WalletRepository(Repository):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Wallet)

    def get_or_create(self, chain_id: int, address: str, checksum: str | None = None) -> Wallet:
        wallet = self.session.scalar(
            select(Wallet).where(
                Wallet.chain_id == chain_id,
                Wallet.address == address.lower(),
            )
        )
        if wallet is None:
            wallet = Wallet(
                chain_id=chain_id,
                address=address.lower(),
                checksum_address=checksum,
            )
            self.add(wallet)
        return wallet


class TokenRepository(Repository):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Token)

    def get_or_create(
        self,
        chain_id: int,
        address: str,
        symbol: str | None,
        name: str | None,
        decimals: int,
    ) -> Token:
        valid_decimals = decimals if 0 <= decimals <= 255 else None
        token = self.session.scalar(
            select(Token).where(
                Token.chain_id == chain_id,
                Token.address == address.lower(),
            )
        )
        if token is None:
            token = Token(
                chain_id=chain_id,
                address=address.lower(),
                symbol=symbol,
                name=name,
                decimals=valid_decimals if valid_decimals is not None else 18,
                suspicious=is_suspicious_token_symbol(symbol),
            )
            self.add(token)
        else:
            token.symbol = symbol or token.symbol
            token.name = name or token.name
            if valid_decimals is not None:
                token.decimals = valid_decimals
            token.suspicious = is_suspicious_token_symbol(token.symbol)
        return token


class JobRepository(Repository):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Job)

    def request_cancel(self, job_id: int) -> Job | None:
        job = self.get(job_id)
        if job and job.state in {"queued", "running"}:
            job.cancel_requested = True
            job.state = "cancelling"
        return job

    def update_progress(self, job_id: int) -> None:
        job = self.get(job_id)
        if job is None:
            return
        done = self.session.scalar(
            select(func.count(JobItem.id)).where(
                JobItem.job_id == job_id,
                JobItem.state.in_(("completed", "failed", "cancelled")),
            )
        )
        job.progress_done = int(done or 0)
        if job.progress_done >= job.progress_total:
            failures = self.session.scalar(
                select(func.count(JobItem.id)).where(
                    JobItem.job_id == job_id, JobItem.state == "failed"
                )
            )
            cancelled = self.session.scalar(
                select(func.count(JobItem.id)).where(
                    JobItem.job_id == job_id, JobItem.state == "cancelled"
                )
            )
            if cancelled and job.cancel_requested:
                job.state = "cancelled"
            elif failures:
                job.state = "completed_with_errors"
            else:
                job.state = "completed"
            job.finished_at = utcnow()


class FeatureRepository(Repository):
    def __init__(self, session: Session) -> None:
        super().__init__(session, WalletFeatureSnapshot)

    def latest_query(self, version: str | None = None):
        latest_query = select(
            WalletFeatureSnapshot.wallet_id,
            func.max(WalletFeatureSnapshot.created_at).label("latest"),
        )
        if version:
            latest_query = latest_query.where(
                WalletFeatureSnapshot.version == version
            )
        latest = latest_query.group_by(WalletFeatureSnapshot.wallet_id).subquery()
        query = select(WalletFeatureSnapshot).join(
            latest,
            (WalletFeatureSnapshot.wallet_id == latest.c.wallet_id)
            & (WalletFeatureSnapshot.created_at == latest.c.latest),
        )
        return query


def log_event(
    session: Session,
    level: str,
    event: str,
    message: str,
    *,
    job_id: int | None = None,
    job_item_id: int | None = None,
    cluster_run_id: int | None = None,
    context: dict | None = None,
) -> AppLog:
    row = AppLog(
        level=level,
        event=event,
        message=message,
        job_id=job_id,
        job_item_id=job_item_id,
        cluster_run_id=cluster_run_id,
        context=context or {},
    )
    session.add(row)
    session.flush()
    return row


def sqlite_upsert(session: Session, model: type, values: dict, keys: list[str]) -> None:
    statement = sqlite_insert(model).values(**values)
    updates = {column: value for column, value in values.items() if column not in keys}
    statement = statement.on_conflict_do_update(index_elements=keys, set_=updates)
    session.execute(statement)
