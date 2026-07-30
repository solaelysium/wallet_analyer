from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import datetime

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import String, cast, delete, func, or_, select
from sqlalchemy.orm import Session

from .config import SecretBox
from .imports import ConfirmImport, confirm_import, preview_import
from .ml import ClusterRequest
from .models import (
    ApiKey,
    AppSettings,
    AppLog,
    ClusterAssignment,
    ClusterRun,
    InternalTransaction,
    Job,
    JobItem,
    NormalTransaction,
    TokenTransfer,
    Wallet,
    WalletFeatureSnapshot,
    WalletImport,
    WalletImportMember,
)
from .repositories import FeatureRepository


router = APIRouter()


def get_session(request: Request):
    with request.app.state.database.session() as session:
        yield session


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


SENSITIVE_LOG_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "key",
    "password",
    "secret",
}


def is_sensitive_log_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return normalized in SENSITIVE_LOG_KEYS or normalized.endswith(
        ("_api_key", "_password", "_secret", "_token")
    )


def sanitize_log_context(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if is_sensitive_log_key(key)
                else sanitize_log_context(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_log_context(item) for item in value]
    return value


def serialize_log(row: AppLog) -> dict:
    return {
        "id": row.id,
        "level": row.level,
        "event": row.event,
        "message": row.message,
        "context": sanitize_log_context(row.context or {}),
        "job_id": row.job_id,
        "job_item_id": row.job_item_id,
        "cluster_run_id": row.cluster_run_id,
        "created_at": iso(row.created_at),
    }


def serialize_job(job: Job) -> dict:
    return {
        "id": job.id,
        "kind": job.kind,
        "state": job.state,
        "wallet_import_id": job.wallet_import_id,
        "cancel_requested": job.cancel_requested,
        "progress_done": job.progress_done,
        "progress_total": job.progress_total,
        "error": job.error,
        "parameters": job.parameters,
        "created_at": iso(job.created_at),
        "started_at": iso(job.started_at),
        "finished_at": iso(job.finished_at),
    }


def serialize_run(run: ClusterRun) -> dict:
    progress = 100 if run.state in {"completed", "failed", "cancelled"} else (
        25 if run.state == "running" else 0
    )
    return {
        "id": run.id,
        "state": run.state,
        "algorithm": run.algorithm,
        "reducer": run.reducer,
        "feature_version": run.feature_version,
        "parameters": run.parameters,
        "feature_names": run.feature_names,
        "metrics": run.metrics,
        "profiles": run.profiles,
        "stage": run.stage,
        "progress_percent": run.progress_percent,
        "error": run.error,
        "cancel_requested": run.cancel_requested,
        "created_at": iso(run.created_at),
        "started_at": iso(run.started_at),
        "finished_at": iso(run.finished_at),
        "progress_percent": progress,
        "poll_after_ms": 1000 if progress < 100 else None,
    }


@router.get("/health")
def health(request: Request) -> dict:
    try:
        with request.app.state.database.engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        database_status = "ok"
    except Exception as exc:
        database_status = f"error: {type(exc).__name__}"
    pool_health = request.app.state.providers.key_pool.health()
    return {
        "status": "ok" if database_status == "ok" else "degraded",
        "database": database_status,
        "providers": {
            service: {
                "configured": len(keys),
                "healthy": sum(
                    key["enabled"] and key["cooldown_seconds"] == 0 for key in keys
                ),
            }
            for service, keys in pool_health.items()
        },
    }


@router.get("/api/logs")
def logs_list(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    level: str | None = Query(default=None, max_length=16),
    event: str | None = Query(default=None, max_length=128),
    search: str | None = Query(default=None, max_length=200),
    job_id: int | None = Query(default=None, ge=1),
    cluster_run_id: int | None = Query(default=None, ge=1),
    session: Session = Depends(get_session),
) -> dict:
    filters = []
    if level and level.strip():
        filters.append(func.lower(AppLog.level) == level.strip().lower())
    if event and event.strip():
        filters.append(AppLog.event.ilike(f"%{event.strip()}%"))
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        filters.append(
            or_(
                AppLog.event.ilike(pattern),
                AppLog.message.ilike(pattern),
                cast(AppLog.context, String).ilike(pattern),
            )
        )
    if job_id is not None:
        filters.append(AppLog.job_id == job_id)
    if cluster_run_id is not None:
        filters.append(AppLog.cluster_run_id == cluster_run_id)

    count_query = select(func.count(AppLog.id))
    rows_query = select(AppLog)
    if filters:
        count_query = count_query.where(*filters)
        rows_query = rows_query.where(*filters)
    total = int(session.scalar(count_query) or 0)
    rows = session.scalars(
        rows_query.order_by(AppLog.created_at.desc(), AppLog.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    return {
        "items": [serialize_log(row) for row in rows],
        "total": total,
        "page": page,
        "size": size,
    }


@router.post("/api/imports/preview")
async def imports_preview(
    request: Request,
    files: list[UploadFile] = File(default=[]),
    manual_text: str = Form(default=""),
) -> dict:
    try:
        result = await preview_import(
            files,
            manual_text,
            SecretBox(request.app.state.settings.app_secret_key),
        )
        return result.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/imports/confirm", status_code=201)
def imports_confirm(
    payload: ConfirmImport,
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    try:
        batch, job = confirm_import(
            session,
            payload,
            SecretBox(request.app.state.settings.app_secret_key),
        )
        session.commit()
        request.app.state.jobs.start(job.id)
        return {
            "import": {
                "id": batch.id,
                "name": batch.name,
                "wallet_count": batch.wallet_count,
                "created_at": iso(batch.created_at),
            },
            "job": serialize_job(job),
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/imports")
def imports_history(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict:
    total = int(session.scalar(select(func.count(WalletImport.id))) or 0)
    rows = session.scalars(
        select(WalletImport).order_by(WalletImport.created_at.desc())
        .offset((page - 1) * size).limit(size)
    ).all()
    return {
        "items": [
            {
                "id": row.id, "name": row.name, "wallet_count": row.wallet_count,
                "source_summary": row.source_summary,
                "created_at": iso(row.created_at), "updated_at": iso(row.updated_at),
            }
            for row in rows
        ],
        "total": total, "page": page, "size": size,
    }


@router.delete("/api/imports/{import_id}")
def import_delete(
    import_id: int, session: Session = Depends(get_session)
) -> dict:
    batch = session.get(WalletImport, import_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Пакет не найден")
    active_job = session.scalar(
        select(Job.id).where(
            Job.wallet_import_id == import_id,
            Job.state.in_(("queued", "running", "cancelling")),
        ).limit(1)
    )
    if active_job is not None:
        raise HTTPException(
            status_code=409,
            detail="Нельзя удалить пакет с активной задачей",
        )
    job_ids = list(
        session.scalars(
            select(Job.id).where(Job.wallet_import_id == import_id)
        ).all()
    )
    item_ids = list(
        session.scalars(
            select(JobItem.id).where(JobItem.job_id.in_(job_ids))
        ).all()
    ) if job_ids else []
    log_filters = []
    if job_ids:
        log_filters.append(AppLog.job_id.in_(job_ids))
    if item_ids:
        log_filters.append(AppLog.job_item_id.in_(item_ids))
    deleted_logs = 0
    if log_filters:
        deleted_logs = session.execute(
            delete(AppLog).where(or_(*log_filters))
        ).rowcount
    deleted_items = 0
    deleted_jobs = 0
    if job_ids:
        deleted_items = session.execute(
            delete(JobItem).where(JobItem.job_id.in_(job_ids))
        ).rowcount
        deleted_jobs = session.execute(
            delete(Job).where(Job.id.in_(job_ids))
        ).rowcount
    deleted_members = session.execute(
        delete(WalletImportMember).where(
            WalletImportMember.wallet_import_id == import_id
        )
    ).rowcount
    session.delete(batch)
    return {
        "deleted": True,
        "import_id": import_id,
        "cascade": {
            "app_logs": deleted_logs,
            "job_items": deleted_items,
            "jobs": deleted_jobs,
            "wallet_import_members": deleted_members,
        },
        "retained": ["wallets", "events", "features", "clusters"],
    }


@router.get("/api/wallets")
def wallets_list(
    search: str | None = None,
    import_id: int | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict:
    query = select(Wallet)
    count_query = select(func.count(func.distinct(Wallet.id)))
    if import_id is not None:
        query = query.join(WalletImportMember).where(
            WalletImportMember.wallet_import_id == import_id
        )
        count_query = count_query.join(WalletImportMember).where(
            WalletImportMember.wallet_import_id == import_id
        )
    if search:
        pattern = f"%{search.lower()}%"
        query = query.where(Wallet.address.like(pattern))
        count_query = count_query.where(Wallet.address.like(pattern))
    total = int(session.scalar(count_query) or 0)
    wallets = session.scalars(
        query.distinct().order_by(Wallet.created_at.desc())
        .offset((page - 1) * size).limit(size)
    ).all()
    return {
        "items": [
            {
                "id": wallet.id, "address": wallet.address,
                "checksum_address": wallet.checksum_address,
                "is_eoa": wallet.is_eoa,
                "last_collected_at": iso(wallet.last_collected_at),
                "created_at": iso(wallet.created_at),
            }
            for wallet in wallets
        ],
        "total": total, "page": page, "size": size,
    }


@router.delete("/api/wallets/{wallet_id}")
def wallet_delete(
    wallet_id: int, session: Session = Depends(get_session)
) -> dict:
    wallet = session.get(Wallet, wallet_id)
    if wallet is None:
        raise HTTPException(status_code=404, detail="Кошелёк не найден")
    active = session.scalar(
        select(JobItem.id).join(Job).where(
            JobItem.wallet_id == wallet_id,
            Job.state.in_(("queued", "running", "cancelling")),
        ).limit(1)
    )
    if active is not None:
        raise HTTPException(
            status_code=409, detail="Кошелёк относится к активной задаче сбора данных"
        )
    item_ids = list(
        session.scalars(
            select(JobItem.id).where(JobItem.wallet_id == wallet_id)
        ).all()
    )
    job_ids = list(
        session.scalars(
            select(JobItem.job_id).where(JobItem.wallet_id == wallet_id)
        ).all()
    )
    import_ids = list(
        session.scalars(
            select(WalletImportMember.wallet_import_id).where(
                WalletImportMember.wallet_id == wallet_id
            )
        ).all()
    )
    log_result = session.execute(
        delete(AppLog).where(AppLog.job_item_id.in_(item_ids))
    ) if item_ids else None
    models = [
        ClusterAssignment, WalletFeatureSnapshot, TokenTransfer,
        InternalTransaction, NormalTransaction, WalletImportMember, JobItem,
    ]
    deleted = {}
    deleted["app_logs"] = log_result.rowcount if log_result else 0
    for model in models:
        result = session.execute(delete(model).where(model.wallet_id == wallet_id))
        deleted[model.__tablename__] = result.rowcount
    session.delete(wallet)
    session.flush()
    for job_id in set(job_ids):
        job = session.get(Job, job_id)
        if job:
            job.progress_total = int(
                session.scalar(
                    select(func.count(JobItem.id)).where(JobItem.job_id == job_id)
                ) or 0
            )
            job.progress_done = int(
                session.scalar(
                    select(func.count(JobItem.id)).where(
                        JobItem.job_id == job_id,
                        JobItem.state.in_(("completed", "failed", "cancelled")),
                    )
                ) or 0
            )
    for import_id in set(import_ids):
        batch = session.get(WalletImport, import_id)
        if batch:
            batch.wallet_count = int(
                session.scalar(
                    select(func.count(WalletImportMember.id)).where(
                        WalletImportMember.wallet_import_id == import_id
                    )
                ) or 0
            )
    return {
        "deleted": True, "wallet_id": wallet_id,
        "cascade": deleted,
        "retained": ["jobs", "wallet_imports", "tokens", "token_prices"],
    }


@router.get("/api/jobs")
def jobs_list(
    state: str | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict:
    query = select(Job)
    count_query = select(func.count(Job.id))
    if state:
        query = query.where(Job.state == state)
        count_query = count_query.where(Job.state == state)
    total = int(session.scalar(count_query) or 0)
    rows = session.scalars(
        query.order_by(Job.created_at.desc()).offset((page - 1) * size).limit(size)
    ).all()
    return {"items": [serialize_job(row) for row in rows], "total": total}


@router.get("/api/jobs/{job_id}")
def job_detail(job_id: int, session: Session = Depends(get_session)) -> dict:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    items = session.execute(
        select(JobItem, Wallet)
        .join(Wallet, Wallet.id == JobItem.wallet_id)
        .where(JobItem.job_id == job_id)
        .order_by(JobItem.id)
    ).all()
    return serialize_job(job) | {
        "items": [
            {
                "id": item.id,
                "wallet_id": item.wallet_id,
                "address": wallet.address,
                "state": item.state,
                "stage": item.stage,
                "attempts": item.attempts,
                "error": item.error,
                "checkpoint": item.checkpoint,
                "started_at": iso(item.started_at),
                "finished_at": iso(item.finished_at),
            }
            for item, wallet in items
        ]
    }


@router.post("/api/jobs/{job_id}/cancel")
def job_cancel(job_id: int, request: Request) -> dict:
    try:
        return serialize_job(request.app.state.jobs.cancel(job_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/jobs/{job_id}/stop")
def job_stop(job_id: int, request: Request) -> dict:
    return job_cancel(job_id, request)


@router.post("/api/jobs/{job_id}/resume")
def job_resume(job_id: int, request: Request) -> dict:
    try:
        return serialize_job(request.app.state.jobs.resume(job_id))
    except ValueError as exc:
        status = 409 if "актив" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.post("/api/jobs/{job_id}/retry")
def job_retry(job_id: int, request: Request) -> dict:
    try:
        return serialize_job(request.app.state.jobs.resume(job_id, retry_only=True))
    except ValueError as exc:
        status = 409 if "актив" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.post("/api/jobs/{job_id}/recalculate")
def job_recalculate(job_id: int, request: Request) -> dict:
    try:
        return serialize_job(
            request.app.state.jobs.resume(job_id, reprocess_completed=True)
        )
    except ValueError as exc:
        status = 409 if "актив" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.get("/api/jobs/{job_id}/events")
async def job_events(job_id: int, request: Request) -> StreamingResponse:
    with request.app.state.database.session() as session:
        if session.get(Job, job_id) is None:
            raise HTTPException(status_code=404, detail="Задача не найдена")

    async def stream():
        last_id = 0
        while True:
            if await request.is_disconnected():
                return
            with request.app.state.database.session() as session:
                logs = session.scalars(
                    select(AppLog)
                    .where(AppLog.job_id == job_id, AppLog.id > last_id)
                    .order_by(AppLog.id)
                ).all()
                job = session.get(Job, job_id)
                job_state = job.state if job else "missing"
                progress = {
                    "done": job.progress_done if job else 0,
                    "total": job.progress_total if job else 0,
                }
            for row in logs:
                last_id = row.id
                payload = {
                    "id": row.id,
                    "level": row.level,
                    "event": row.event,
                    "message": row.message,
                    "context": row.context,
                    "created_at": iso(row.created_at),
                    "job_state": job_state,
                    "progress": progress,
                }
                yield f"id: {row.id}\nevent: {row.event}\ndata: {json.dumps(payload)}\n\n"
            if job_state in {"completed", "completed_with_errors", "failed", "cancelled", "missing"}:
                yield f"event: end\ndata: {json.dumps({'state': job_state, 'progress': progress})}\n\n"
                return
            if not logs:
                yield ": keep-alive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class ApiKeyCreate(BaseModel):
    service: str
    label: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1)

    @field_validator("service")
    @classmethod
    def known_service(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"etherscan", "infura", "coingecko"}:
            raise ValueError("Неизвестный сервис провайдера")
        return normalized


class ApiKeyPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=128)
    value: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None


class RuntimeSettingsPatch(BaseModel):
    job_workers: int | None = Field(default=None, ge=1, le=64)
    provider_timeout_seconds: float | None = Field(default=None, gt=0, le=300)
    provider_max_retries: int | None = Field(default=None, ge=1, le=20)
    provider_cooldown_seconds: float | None = Field(default=None, ge=0, le=3600)
    etherscan_rps: float | None = Field(default=None, gt=0, le=1000)
    infura_rps: float | None = Field(default=None, gt=0, le=1000)
    coingecko_rps: float | None = Field(default=None, gt=0, le=1000)
    key_concurrency: int | None = Field(default=None, ge=1, le=100)


def mask_key(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * min(12, len(value) - 8)}{value[-4:]}"


def serialize_api_key(row: ApiKey) -> dict:
    return {
        "id": row.id,
        "service": row.service,
        "label": row.label,
        "value": mask_key(row.value),
        "masked_value": mask_key(row.value),
        "enabled": row.enabled,
        "error_count": row.error_count,
        "last_used_at": iso(row.last_used_at),
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
    }


def serialize_settings(row: AppSettings) -> dict:
    return {
        name: getattr(row, name)
        for name in RuntimeSettingsPatch.model_fields
    }


def settings_response(
    row: AppSettings,
    request: Request,
    message: str | None = None,
) -> dict:
    pool_health = request.app.state.providers.key_pool.health()
    provider_health = {}
    for service in ("etherscan", "infura", "coingecko"):
        keys = pool_health.get(service, [])
        enabled = sum(bool(key["enabled"]) for key in keys)
        healthy = sum(
            bool(key["enabled"])
            and key["cooldown_seconds"] == 0
            and not key["last_error"]
            for key in keys
        )
        provider_health[service] = {
            "total_keys": len(keys),
            "enabled_keys": enabled,
            "healthy_keys": healthy,
            "status": (
                "unavailable"
                if enabled == 0
                else "degraded"
                if healthy < enabled
                else "ready"
            ),
        }
    response = serialize_settings(row) | {"provider_health": provider_health}
    if message:
        response["message"] = message
    return response


def reconfigure_providers(request: Request, session: Session) -> None:
    settings = session.get(AppSettings, 1)
    keys = list(session.scalars(select(ApiKey)).all())
    request.app.state.providers.reconfigure(settings, keys)
    request.app.state.runtime_settings = settings


@router.get("/api/api-keys")
def api_keys_list(session: Session = Depends(get_session)) -> list[dict]:
    rows = session.scalars(select(ApiKey).order_by(ApiKey.service, ApiKey.label)).all()
    return [serialize_api_key(row) for row in rows]


@router.post("/api/api-keys", status_code=201)
def api_key_create(
    payload: ApiKeyCreate,
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    row = session.scalar(
        select(ApiKey).where(
            ApiKey.service == payload.service,
            ApiKey.value == payload.value,
        )
    )
    if row is None:
        row = ApiKey(
            service=payload.service,
            label=payload.label,
            value=payload.value,
            enabled=True,
        )
        session.add(row)
    else:
        row.label = payload.label
        row.enabled = True
    session.flush()
    reconfigure_providers(request, session)
    return serialize_api_key(row)


@router.patch("/api/api-keys/{key_id}")
def api_key_update(
    key_id: int,
    payload: ApiKeyPatch,
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    row = session.get(ApiKey, key_id)
    if row is None:
        raise HTTPException(status_code=404, detail="API-ключ не найден")
    changes = payload.model_dump(exclude_unset=True)
    if "value" in changes:
        duplicate = session.scalar(
            select(ApiKey.id).where(
                ApiKey.service == row.service,
                ApiKey.value == changes["value"],
                ApiKey.id != row.id,
            )
        )
        if duplicate is not None:
            raise HTTPException(
                status_code=409,
                detail="Такой ключ провайдера уже существует",
            )
    for name, value in changes.items():
        setattr(row, name, value)
    session.flush()
    reconfigure_providers(request, session)
    return serialize_api_key(row)


@router.delete("/api/api-keys/{key_id}", status_code=204)
def api_key_delete(
    key_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> None:
    row = session.get(ApiKey, key_id)
    if row is None:
        raise HTTPException(status_code=404, detail="API-ключ не найден")
    session.delete(row)
    session.flush()
    reconfigure_providers(request, session)


@router.get("/api/settings")
def settings_get(
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    row = session.get(AppSettings, 1)
    if row is None:
        raise HTTPException(status_code=503, detail="Рабочие настройки недоступны")
    return settings_response(row, request)


@router.patch("/api/settings")
def settings_update(
    payload: RuntimeSettingsPatch,
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    row = session.get(AppSettings, 1)
    if row is None:
        raise HTTPException(status_code=503, detail="Рабочие настройки недоступны")
    changes = payload.model_dump(exclude_unset=True)
    if "job_workers" in changes and changes["job_workers"] != row.job_workers:
        active_job = session.scalar(
            select(Job.id).where(
                Job.state.in_(("queued", "running", "cancelling"))
            ).limit(1)
        )
        if active_job is not None:
            raise HTTPException(
                status_code=409,
                detail="Нельзя изменить число обработчиков во время активного сбора данных",
            )
        if not request.app.state.jobs.reconfigure_workers(changes["job_workers"]):
            raise HTTPException(
                status_code=409,
                detail="Нельзя изменить число обработчиков во время активного сбора данных",
            )
    for name, value in changes.items():
        setattr(row, name, value)
    session.flush()
    reconfigure_providers(request, session)
    return settings_response(row, request, "Настройки применены.")


def _feature_rows(
    session: Session,
    version: str | None,
    search: str | None,
    filters: str | None,
    sort_by: str,
    sort_order: str,
) -> list[dict]:
    snapshots = session.scalars(FeatureRepository(session).latest_query(version)).all()
    wallets = {
        wallet.id: wallet
        for wallet in session.scalars(
            select(Wallet).where(
                Wallet.id.in_([snapshot.wallet_id for snapshot in snapshots])
            )
        ).all()
    }
    rows = [
        {
            "snapshot_id": snapshot.id,
            "wallet_id": snapshot.wallet_id,
            "address": wallets[snapshot.wallet_id].address,
            "version": snapshot.version,
            "as_of_block": snapshot.as_of_block,
            "created_at": iso(snapshot.created_at),
            "features": snapshot.features,
            "quality": snapshot.quality,
        }
        for snapshot in snapshots
        if snapshot.wallet_id in wallets
    ]
    if search:
        lowered = search.lower()
        rows = [row for row in rows if lowered in row["address"]]
    if filters:
        try:
            parsed = json.loads(filters)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="Фильтры должны содержать корректный JSON") from exc
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=422, detail="Фильтры должны быть объектом")
        for name, limits in parsed.items():
            if not isinstance(limits, dict):
                continue
            minimum = limits.get("min")
            maximum = limits.get("max")
            rows = [
                row
                for row in rows
                if isinstance(row["features"].get(name), (int, float))
                and (minimum is None or row["features"][name] >= minimum)
                and (maximum is None or row["features"][name] <= maximum)
            ]

    def sort_value(row: dict):
        if sort_by in {"address", "created_at", "as_of_block", "version"}:
            value = row[sort_by]
        else:
            value = row["features"].get(sort_by)
        return (value is None, value if isinstance(value, (int, float, str)) else str(value))

    rows.sort(key=sort_value, reverse=sort_order == "desc")
    return rows


@router.get("/api/features")
def features_list(
    version: str | None = None,
    search: str | None = None,
    filters: str | None = None,
    sort_by: str = "created_at",
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict:
    rows = _feature_rows(
        session, version, search, filters, sort_by, sort_order
    )
    start = (page - 1) * size
    return {"items": rows[start : start + size], "total": len(rows), "page": page, "size": size}


def safe_cell(value: object) -> object:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def tabular_feature_rows(rows: list[dict]) -> tuple[list[str], list[list[object]]]:
    feature_names = sorted(
        {name for row in rows for name in row["features"].keys()}
    )
    headers = ["address", "version", "as_of_block", "created_at", *feature_names]
    values = [
        [
            safe_cell(row["address"]),
            row["version"],
            row["as_of_block"],
            row["created_at"],
            *[safe_cell(row["features"].get(name, "")) for name in feature_names],
        ]
        for row in rows
    ]
    return headers, values


def export_table(headers: list[str], rows: list[list[object]], file_format: str):
    if file_format == "csv":
        text = io.StringIO(newline="")
        writer = csv.writer(text)
        writer.writerow(headers)
        writer.writerows(rows)
        content = io.BytesIO(text.getvalue().encode("utf-8-sig"))
        media_type = "text/csv; charset=utf-8"
        extension = "csv"
    else:
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet("data")
        sheet.append(headers)
        for row in rows:
            sheet.append(row)
        content = io.BytesIO()
        workbook.save(content)
        content.seek(0)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        extension = "xlsx"
    content.seek(0)
    return StreamingResponse(
        content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="export.{extension}"'},
    )


@router.get("/api/features/export")
def features_export(
    file_format: str = Query(default="csv", pattern="^(csv|xlsx)$"),
    version: str | None = None,
    search: str | None = None,
    filters: str | None = None,
    sort_by: str = "created_at",
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    session: Session = Depends(get_session),
):
    rows = _feature_rows(
        session, version, search, filters, sort_by, sort_order
    )
    headers, values = tabular_feature_rows(rows)
    return export_table(headers, values, file_format)


@router.post("/api/clusters", status_code=202)
def cluster_start(payload: ClusterRequest, request: Request) -> dict:
    try:
        return serialize_run(request.app.state.ml.start(payload))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/clusters")
def cluster_history(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict:
    total = int(session.scalar(select(func.count(ClusterRun.id))) or 0)
    rows = session.scalars(
        select(ClusterRun)
        .order_by(ClusterRun.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    return {"items": [serialize_run(row) for row in rows], "total": total}


@router.get("/api/clusters/{run_id}")
def cluster_result(run_id: int, session: Session = Depends(get_session)) -> dict:
    run = session.get(ClusterRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Запуск кластеризации не найден")
    assignments = session.execute(
        select(ClusterAssignment, Wallet)
        .join(Wallet, Wallet.id == ClusterAssignment.wallet_id)
        .where(ClusterAssignment.cluster_run_id == run_id)
        .order_by(ClusterAssignment.id)
    ).all()
    snapshots = session.scalars(
        FeatureRepository(session).latest_query(run.feature_version).where(
            WalletFeatureSnapshot.wallet_id.in_(
                [assignment.wallet_id for assignment, _ in assignments]
            )
        )
    ).all()
    feature_map = {snapshot.wallet_id: snapshot.features for snapshot in snapshots}
    return serialize_run(run) | {
        "assignments": [
            {
                "wallet_id": assignment.wallet_id,
                "address": wallet.address,
                "cluster": assignment.cluster_label,
                "probability": assignment.probability,
                "x": assignment.x,
                "y": assignment.y,
                "features": {
                    name: feature_map.get(assignment.wallet_id, {}).get(name)
                    for name in run.feature_names
                },
            }
            for assignment, wallet in assignments
        ]
    }


@router.post("/api/clusters/{run_id}/stop")
def cluster_stop(run_id: int, request: Request) -> dict:
    try:
        return serialize_run(request.app.state.ml.cancel(run_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/clusters/{run_id}/export")
def cluster_export(
    run_id: int,
    file_format: str = Query(default="csv", pattern="^(csv|xlsx)$"),
    session: Session = Depends(get_session),
):
    run = session.get(ClusterRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Запуск кластеризации не найден")
    assignments = session.execute(
        select(ClusterAssignment, Wallet)
        .join(Wallet, Wallet.id == ClusterAssignment.wallet_id)
        .where(ClusterAssignment.cluster_run_id == run_id)
        .order_by(ClusterAssignment.id)
    ).all()
    snapshots = session.scalars(
        FeatureRepository(session).latest_query(run.feature_version).where(
            WalletFeatureSnapshot.wallet_id.in_(
                [assignment.wallet_id for assignment, _ in assignments]
            )
        )
    ).all()
    feature_map = {snapshot.wallet_id: snapshot.features for snapshot in snapshots}
    headers = ["address", "cluster", "probability", "x", "y", *run.feature_names]
    rows = [
        [
            safe_cell(wallet.address),
            assignment.cluster_label,
            assignment.probability,
            assignment.x,
            assignment.y,
            *[
                feature_map.get(assignment.wallet_id, {}).get(name, "")
                for name in run.feature_names
            ],
        ]
        for assignment, wallet in assignments
    ]
    return export_table(headers, rows, file_format)
