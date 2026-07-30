from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Chain(Base, TimestampMixin):
    __tablename__ = "chains"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    chain_id: Mapped[int] = mapped_column(Integer, unique=True)
    native_symbol: Mapped[str] = mapped_column(String(16), default="ETH")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ApiKey(Base, TimestampMixin):
    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("service", "value", name="uq_api_key_service_value"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    service: Mapped[str] = mapped_column(String(32), index=True)
    label: Mapped[str] = mapped_column(String(128))
    value: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_count: Mapped[int] = mapped_column(Integer, default=0)


class AppSettings(Base, TimestampMixin):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    job_workers: Mapped[int] = mapped_column(Integer, default=4)
    provider_timeout_seconds: Mapped[float] = mapped_column(Float, default=30.0)
    provider_max_retries: Mapped[int] = mapped_column(Integer, default=4)
    provider_cooldown_seconds: Mapped[float] = mapped_column(Float, default=30.0)
    etherscan_rps: Mapped[float] = mapped_column(Float, default=4.0)
    infura_rps: Mapped[float] = mapped_column(Float, default=8.0)
    coingecko_rps: Mapped[float] = mapped_column(Float, default=1.0)
    key_concurrency: Mapped[int] = mapped_column(Integer, default=2)


class WalletImport(Base, TimestampMixin):
    __tablename__ = "wallet_imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    chain_id: Mapped[int] = mapped_column(ForeignKey("chains.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    source_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    wallet_count: Mapped[int] = mapped_column(Integer, default=0)


class Wallet(Base, TimestampMixin):
    __tablename__ = "wallets"
    __table_args__ = (UniqueConstraint("chain_id", "address", name="uq_wallet_chain_address"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    chain_id: Mapped[int] = mapped_column(ForeignKey("chains.id"), index=True)
    address: Mapped[str] = mapped_column(String(42), index=True)
    checksum_address: Mapped[str | None] = mapped_column(String(42))
    is_eoa: Mapped[bool | None] = mapped_column(Boolean)
    last_collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WalletImportMember(Base):
    __tablename__ = "wallet_import_members"
    __table_args__ = (
        UniqueConstraint("wallet_import_id", "wallet_id", name="uq_import_wallet"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_import_id: Mapped[int] = mapped_column(
        ForeignKey("wallet_imports.id", ondelete="CASCADE"), index=True
    )
    wallet_id: Mapped[int] = mapped_column(
        ForeignKey("wallets.id", ondelete="CASCADE"), index=True
    )
    source_name: Mapped[str] = mapped_column(String(255))
    source_row: Mapped[int | None] = mapped_column(Integer)
    source_index: Mapped[str | None] = mapped_column(String(255))


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    state: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    wallet_import_id: Mapped[int | None] = mapped_column(
        ForeignKey("wallet_imports.id"), index=True
    )
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    progress_done: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobItem(Base, TimestampMixin):
    __tablename__ = "job_items"
    __table_args__ = (UniqueConstraint("job_id", "wallet_id", name="uq_job_wallet"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"), index=True)
    state: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    stage: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NormalTransaction(Base):
    __tablename__ = "normal_transactions"
    __table_args__ = (
        UniqueConstraint("wallet_id", "tx_hash", name="uq_normal_tx"),
        Index("ix_normal_wallet_time", "wallet_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain_id: Mapped[int] = mapped_column(Integer)
    wallet_id: Mapped[int] = mapped_column(Integer, index=True)
    tx_hash: Mapped[str] = mapped_column(String(66), index=True)
    block_number: Mapped[int] = mapped_column(Integer, index=True)
    timestamp: Mapped[int] = mapped_column(Integer)
    from_address: Mapped[str] = mapped_column(String(42))
    to_address: Mapped[str | None] = mapped_column(String(42))
    value_wei: Mapped[str] = mapped_column(String(80), default="0")
    gas_used: Mapped[str] = mapped_column(String(80), default="0")
    gas_price: Mapped[str] = mapped_column(String(80), default="0")
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class InternalTransaction(Base):
    __tablename__ = "internal_transactions"
    __table_args__ = (
        UniqueConstraint(
            "wallet_id", "tx_hash", "trace_id", name="uq_internal_tx_trace"
        ),
        Index("ix_internal_wallet_time", "wallet_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain_id: Mapped[int] = mapped_column(Integer)
    wallet_id: Mapped[int] = mapped_column(Integer, index=True)
    tx_hash: Mapped[str] = mapped_column(String(66), index=True)
    trace_id: Mapped[str] = mapped_column(String(128), default="")
    block_number: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[int] = mapped_column(Integer)
    from_address: Mapped[str] = mapped_column(String(42))
    to_address: Mapped[str | None] = mapped_column(String(42))
    value_wei: Mapped[str] = mapped_column(String(80), default="0")
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class Token(Base, TimestampMixin):
    __tablename__ = "tokens"
    __table_args__ = (
        UniqueConstraint("chain_id", "address", name="uq_token_chain_address"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain_id: Mapped[int] = mapped_column(Integer, index=True)
    address: Mapped[str] = mapped_column(String(42), index=True)
    symbol: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(255))
    decimals: Mapped[int] = mapped_column(Integer, default=18)
    coingecko_id: Mapped[str | None] = mapped_column(String(255))
    suspicious: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class TokenTransfer(Base):
    __tablename__ = "token_transfers"
    __table_args__ = (
        UniqueConstraint(
            "wallet_id", "tx_hash", "log_index", name="uq_token_transfer_log"
        ),
        Index("ix_transfer_wallet_time", "wallet_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain_id: Mapped[int] = mapped_column(Integer)
    wallet_id: Mapped[int] = mapped_column(Integer, index=True)
    token_id: Mapped[int] = mapped_column(Integer, index=True)
    tx_hash: Mapped[str] = mapped_column(String(66), index=True)
    log_index: Mapped[int] = mapped_column(Integer, default=0)
    block_number: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[int] = mapped_column(Integer)
    from_address: Mapped[str] = mapped_column(String(42))
    to_address: Mapped[str] = mapped_column(String(42))
    raw_value: Mapped[str] = mapped_column(String(100), default="0")
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class TokenPrice(Base, TimestampMixin):
    __tablename__ = "token_prices"
    __table_args__ = (
        UniqueConstraint("token_id", "timestamp", "source", name="uq_token_price"),
        Index("ix_price_token_time", "token_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id"), index=True)
    timestamp: Mapped[int] = mapped_column(Integer)
    price_usd: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(64))
    block_number: Mapped[int | None] = mapped_column(Integer)


class WalletFeatureSnapshot(Base, TimestampMixin):
    __tablename__ = "wallet_feature_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "wallet_id", "version", "as_of_block", name="uq_wallet_feature_version"
        ),
        Index("ix_feature_version_created", "version", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[int] = mapped_column(Integer, index=True)
    version: Mapped[str] = mapped_column(String(64), index=True)
    as_of_block: Mapped[int] = mapped_column(Integer, default=0)
    features: Mapped[dict] = mapped_column(JSON)
    quality: Mapped[dict] = mapped_column(JSON, default=dict)


class ClusterRun(Base, TimestampMixin):
    __tablename__ = "cluster_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    state: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    algorithm: Mapped[str] = mapped_column(String(32))
    reducer: Mapped[str] = mapped_column(String(32))
    feature_version: Mapped[str] = mapped_column(String(64))
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    feature_names: Mapped[list] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    profiles: Mapped[dict] = mapped_column(JSON, default=dict)
    stage: Mapped[str] = mapped_column(String(64), default="queued")
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClusterAssignment(Base):
    __tablename__ = "cluster_assignments"
    __table_args__ = (
        UniqueConstraint("cluster_run_id", "wallet_id", name="uq_cluster_wallet"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_run_id: Mapped[int] = mapped_column(
        ForeignKey("cluster_runs.id", ondelete="CASCADE"), index=True
    )
    wallet_id: Mapped[int] = mapped_column(Integer, index=True)
    cluster_label: Mapped[int] = mapped_column(Integer, index=True)
    probability: Mapped[float | None] = mapped_column(Float)
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)


class AppLog(Base):
    __tablename__ = "app_logs"
    __table_args__ = (
        Index("ix_log_job_created", "job_id", "created_at"),
        Index("ix_log_cluster_created", "cluster_run_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    level: Mapped[str] = mapped_column(String(16), index=True)
    event: Mapped[str] = mapped_column(String(128), index=True)
    message: Mapped[str] = mapped_column(Text)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    job_id: Mapped[int | None] = mapped_column(Integer, index=True)
    job_item_id: Mapped[int | None] = mapped_column(Integer, index=True)
    cluster_run_id: Mapped[int | None] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


DATABASE_TABLES = {
    "keys": (ApiKey, AppSettings),
    "wallets": (Chain, WalletImport, Wallet, WalletImportMember, Job, JobItem),
    "events": (NormalTransaction, InternalTransaction, TokenTransfer),
    "tokens": (Token, TokenPrice),
    "analytics": (WalletFeatureSnapshot, ClusterRun, ClusterAssignment),
    "logs": (AppLog,),
}

for database_name, model_group in DATABASE_TABLES.items():
    for model in model_group:
        model.__table__.schema = database_name
