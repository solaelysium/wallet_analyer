from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from .api import router
from .config import Settings, get_settings
from .database import Database
from .jobs import CollectionJobManager
from .ml import MLManager
from .models import ApiKey, utcnow
from .providers import ProviderBundle


def create_app(
    settings: Settings | None = None,
    database: Database | None = None,
    providers: ProviderBundle | None = None,
) -> FastAPI:
    active_settings = settings or get_settings()
    active_database = database or Database(active_settings)

    def mark_api_key_used(key_id: int | None, service: str, value: str) -> None:
        with active_database.session() as session:
            row = None
            if key_id is not None:
                row = session.get(ApiKey, key_id)
            if row is None:
                row = session.scalar(
                    select(ApiKey).where(
                        ApiKey.service == service,
                        ApiKey.value == value,
                    )
                )
            if row is None:
                return
            row.last_used_at = utcnow()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        active_database.initialize()
        active_database.recover_interrupted()
        runtime_settings = active_database.runtime_settings()
        with active_database.session() as session:
            stored_keys = list(session.scalars(select(ApiKey)).all())
        active_providers = providers or ProviderBundle.from_settings(runtime_settings)
        active_providers.reconfigure(runtime_settings, stored_keys)
        active_providers.key_pool.set_on_key_used(mark_api_key_used)
        jobs = CollectionJobManager(
            active_database,
            active_providers,
            max_workers=runtime_settings.job_workers,
        )
        ml = MLManager(active_database)
        app.state.jobs = jobs
        app.state.ml = ml
        app.state.providers = active_providers
        app.state.runtime_settings = runtime_settings
        jobs.resume_pending()
        try:
            yield
        finally:
            jobs.shutdown()
            ml.shutdown()
            active_database.engine.dispose()

    app = FastAPI(
        title=active_settings.app_name,
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = active_settings
    app.state.database = active_database
    app.state.providers = providers
    app.add_middleware(
        CORSMiddleware,
        allow_origins=active_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
