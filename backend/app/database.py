from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from .config import SecretBox, Settings, get_settings
from .models import (
    ApiKey,
    AppSettings,
    Base,
    Chain,
    ClusterRun,
    DATABASE_TABLES,
    Job,
    JobItem,
    utcnow,
)
from .token_rules import is_suspicious_token_symbol


class Database:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.data_dir = self.settings.data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.paths = {
            name: self.data_dir / f"{name}.sqlite3" for name in DATABASE_TABLES
        }
        # File-backed hub + NullPool: memory sqlite:// uses SingletonThreadPool,
        # which closes in-use connections once unique threads exceed pool_size (5).
        hub = self.data_dir / "hub.sqlite3"
        self.engine = create_engine(
            f"sqlite:///{hub.as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 60},
            poolclass=NullPool,
            pool_pre_ping=True,
        )
        event.listen(self.engine, "connect", self._configure_sqlite)
        self.session_factory = sessionmaker(
            bind=self.engine, expire_on_commit=False, class_=Session
        )

    def _configure_sqlite(self, dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=60000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        for name, path in self.paths.items():
            escaped = str(path).replace("'", "''")
            cursor.execute(f"ATTACH DATABASE '{escaped}' AS {name}")
            cursor.execute(f"PRAGMA {name}.journal_mode=WAL")
            cursor.execute(f"PRAGMA {name}.synchronous=NORMAL")
        cursor.close()

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)
        self._upgrade_schema()
        with self.engine.begin() as connection:
            for name in self.paths:
                connection.execute(
                    text(
                        f"CREATE TABLE IF NOT EXISTS {name}.schema_version "
                        "(version INTEGER NOT NULL)"
                    )
                )
                count = connection.scalar(
                    text(f"SELECT COUNT(*) FROM {name}.schema_version")
                )
                if not count:
                    connection.execute(
                        text(f"INSERT INTO {name}.schema_version VALUES (1)")
                    )
            connection.execute(text("UPDATE tokens.schema_version SET version = 2"))
            connection.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS keys.migration_state "
                    "(name TEXT PRIMARY KEY, completed_at TEXT NOT NULL)"
                )
            )
        self._migrate_legacy()
        self._backfill_suspicious_tokens()
        with self.session() as session:
            ethereum = session.scalar(select(Chain).where(Chain.slug == "ethereum"))
            if ethereum is None:
                session.add(
                    Chain(
                        slug="ethereum",
                        name="Ethereum",
                        chain_id=1,
                        native_symbol="ETH",
                    )
                )
                session.flush()
                ethereum = session.scalar(select(Chain).where(Chain.slug == "ethereum"))
            if session.get(AppSettings, 1) is None:
                session.add(
                    AppSettings(
                        id=1,
                        job_workers=self.settings.job_workers,
                        provider_timeout_seconds=self.settings.provider_timeout_seconds,
                        provider_max_retries=self.settings.provider_max_retries,
                        provider_cooldown_seconds=self.settings.provider_cooldown_seconds,
                        etherscan_rps=self.settings.etherscan_rps,
                        infura_rps=self.settings.infura_rps,
                        coingecko_rps=self.settings.coingecko_rps,
                        key_concurrency=self.settings.key_concurrency,
                    )
                )
            self._seed_common_tokens(session, ethereum.id)

    def _seed_common_tokens(self, session: Session, chain_id: int) -> None:
        from .repositories import TokenRepository
        from .token_rules import NATIVE_ADDRESS, STABLECOINS, WETH_ADDRESS

        tokens = TokenRepository(session)
        tokens.get_or_create(chain_id, NATIVE_ADDRESS, "ETH", "Ether", 18)
        tokens.get_or_create(chain_id, WETH_ADDRESS, "WETH", "Wrapped Ether", 18)
        for address, (symbol, decimals) in STABLECOINS.items():
            tokens.get_or_create(chain_id, address, symbol, symbol, decimals)

    def _upgrade_schema(self) -> None:
        with self.engine.begin() as connection:
            columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA tokens.table_info('tokens')"
                )
            }
            if "suspicious" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE tokens.tokens "
                    "ADD COLUMN suspicious BOOLEAN NOT NULL DEFAULT 0"
                )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS tokens.ix_tokens_tokens_suspicious "
                "ON tokens (suspicious)"
            )

    def _backfill_suspicious_tokens(self) -> None:
        with self.engine.begin() as connection:
            rows = connection.exec_driver_sql(
                "SELECT id, symbol FROM tokens.tokens"
            ).all()
            for token_id, symbol in rows:
                connection.exec_driver_sql(
                    "UPDATE tokens.tokens SET suspicious = ? WHERE id = ?",
                    (int(is_suspicious_token_symbol(symbol)), token_id),
                )

    def _migrate_legacy(self) -> None:
        legacy_path = self.settings.legacy_database_path
        if not legacy_path.exists() or legacy_path in self.paths.values():
            return
        with sqlite3.connect(legacy_path) as legacy:
            legacy_tables = {
                row[0]
                for row in legacy.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        if not legacy_tables:
            return
        with self.engine.connect() as connection:
            migrated = connection.scalar(
                text(
                    "SELECT 1 FROM keys.migration_state "
                    "WHERE name = 'legacy-unified-v1'"
                )
            )
        if migrated:
            return
        with self.engine.begin() as connection:
            escaped = str(legacy_path).replace("'", "''")
            connection.exec_driver_sql(f"ATTACH DATABASE '{escaped}' AS legacy")
            for database_name, models in DATABASE_TABLES.items():
                for model in models:
                    table_name = model.__tablename__
                    if table_name == "api_keys" or table_name not in legacy_tables:
                        continue
                    target_columns = [column.name for column in model.__table__.columns]
                    legacy_columns = {
                        row[1]
                        for row in connection.exec_driver_sql(
                            f"PRAGMA legacy.table_info('{table_name}')"
                        )
                    }
                    columns = [
                        name for name in target_columns if name in legacy_columns
                    ]
                    if not columns:
                        continue
                    names = ", ".join(f'"{name}"' for name in columns)
                    connection.exec_driver_sql(
                        f"INSERT OR IGNORE INTO {database_name}.{table_name} "
                        f"({names}) SELECT {names} FROM legacy.{table_name}"
                    )
                    source_count = connection.exec_driver_sql(
                        f"SELECT COUNT(*) FROM legacy.{table_name}"
                    ).scalar_one()
                    target_count = connection.exec_driver_sql(
                        f"SELECT COUNT(*) FROM {database_name}.{table_name}"
                    ).scalar_one()
                    if target_count < source_count:
                        raise RuntimeError(
                            f"Legacy migration count check failed for {table_name}: "
                            f"{source_count} source rows, {target_count} target rows"
                        )
            self._migrate_legacy_keys(connection, legacy_tables)
            connection.execute(
                text(
                    "INSERT INTO keys.migration_state(name, completed_at) "
                    "VALUES ('legacy-unified-v1', :completed_at)"
                ),
                {"completed_at": utcnow().isoformat()},
            )
        self._migrate_environment_keys()

    def _legacy_secrets(self) -> list[str]:
        secrets = []
        candidates = [self.data_dir.parent / ".env", Path.cwd() / ".env"]
        for path in candidates:
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("APP_SECRET_KEY="):
                    secrets.append(line.split("=", 1)[1].strip().strip("\"'"))
        secrets.extend(
            [
                self.settings.app_secret_key,
                "local-development-change-me",
            ]
        )
        return list(dict.fromkeys(secret for secret in secrets if secret))

    def _migrate_legacy_keys(self, connection, legacy_tables: set[str]) -> None:
        if "api_keys" not in legacy_tables:
            return
        secret_boxes = [SecretBox(secret) for secret in self._legacy_secrets()]
        rows = connection.exec_driver_sql(
            "SELECT id, service, label, encrypted_value, enabled, last_used_at, "
            "error_count, created_at, updated_at FROM legacy.api_keys"
        ).mappings()
        failures = []
        for row in rows:
            value = None
            for secret_box in secret_boxes:
                try:
                    value = secret_box.decrypt(row["encrypted_value"])
                    break
                except ValueError:
                    continue
            if value is None:
                failures.append(row["id"])
                continue
            connection.execute(
                text(
                    "INSERT OR IGNORE INTO keys.api_keys "
                    "(id, service, label, value, enabled, last_used_at, error_count, "
                    "created_at, updated_at) VALUES (:id, :service, :label, :value, "
                    ":enabled, :last_used_at, :error_count, :created_at, :updated_at)"
                ),
                dict(row) | {"value": value},
            )
        if failures:
            ids = ", ".join(str(key_id) for key_id in failures)
            raise RuntimeError(
                "Legacy API keys could not be decrypted; verify APP_SECRET_KEY "
                f"in the legacy .env file. Key IDs: {ids}"
            )

    def _migrate_environment_keys(self) -> None:
        with self.engine.begin() as connection:
            migrated = connection.scalar(
                text(
                    "SELECT 1 FROM keys.migration_state "
                    "WHERE name = 'legacy-environment-v1'"
                )
            )
            if migrated:
                return
            values = self._read_legacy_environment()
            for service, keys in values.items():
                for index, value in enumerate(keys, 1):
                    connection.execute(
                        text(
                            "INSERT OR IGNORE INTO keys.api_keys "
                            "(service, label, value, enabled, error_count, created_at, "
                            "updated_at) VALUES (:service, :label, :value, 1, 0, "
                            ":created_at, :updated_at)"
                        ),
                        {
                            "service": service,
                            "label": f"legacy-environment-{index}",
                            "value": value,
                            "created_at": utcnow(),
                            "updated_at": utcnow(),
                        },
                    )
            connection.execute(
                text(
                    "INSERT INTO keys.migration_state(name, completed_at) "
                    "VALUES ('legacy-environment-v1', :completed_at)"
                ),
                {"completed_at": utcnow().isoformat()},
            )

    def _read_legacy_environment(self) -> dict[str, list[str]]:
        raw: dict[str, str] = {}
        for path in (self.data_dir.parent / ".env", Path.cwd() / ".env"):
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    raw.setdefault(key.strip(), value.strip().strip("\"'"))
        result = {}
        for service in ("infura", "etherscan", "coingecko"):
            plural = raw.get(f"{service.upper()}_API_KEYS")
            single = raw.get(f"{service.upper()}_API_KEY")
            value = plural or single or ""
            result[service] = [item.strip() for item in value.split(",") if item.strip()]
        return result

    def runtime_settings(self) -> AppSettings:
        with self.session() as session:
            row = session.get(AppSettings, 1)
            if row is None:
                raise RuntimeError("Runtime settings are not initialized")
            session.expunge(row)
            return row

    def recover_interrupted(self) -> None:
        with self.session() as session:
            jobs = session.scalars(
                select(Job).where(Job.state.in_(("running", "cancelling")))
            ).all()
            for job in jobs:
                job.state = "queued" if not job.cancel_requested else "cancelled"
                if job.state == "cancelled":
                    job.finished_at = utcnow()
            items = session.scalars(
                select(JobItem).where(JobItem.state.in_(("queued", "running")))
            ).all()
            for item in items:
                job = session.get(Job, item.job_id)
                if job and job.state == "cancelled":
                    item.state = "cancelled"
                    item.stage = "cancelled"
                    item.finished_at = utcnow()
                else:
                    item.state = "queued"
                    item.stage = "recovered"
            runs = session.scalars(
                select(ClusterRun).where(
                    ClusterRun.state.in_(("queued", "running", "cancelling"))
                )
            ).all()
            for run in runs:
                run.state = "failed"
                run.stage = "failed"
                run.error = "Interrupted by backend restart"
                run.finished_at = utcnow()

    @contextmanager
    def session(self):
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dependency(self):
        with self.session() as session:
            yield session


db = Database()
