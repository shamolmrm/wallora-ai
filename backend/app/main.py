from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import files
from app.container import Container, build_container
from app.core.config import AppEnv, get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.modules.catalog.router import router as catalog_router
from app.modules.credits.router import router as credits_router
from app.modules.studio.router import router as studio_router


def create_app(container: Container | None = None) -> FastAPI:
    settings = container.settings if container else get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        if not hasattr(app.state, "container"):
            app.state.container = build_container(settings)
        try:
            yield
        finally:
            await app.state.container.close()

    # Interactive API docs are only exposed outside production.
    docs_enabled = settings.app_env is not AppEnv.PROD
    app = FastAPI(
        title="Wallora AI API",
        version="0.1.0",
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=lifespan,
    )
    if container is not None:
        app.state.container = container
    install_error_handlers(app)

    @app.get("/health/live", tags=["system"])
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(catalog_router)
    app.include_router(studio_router)
    app.include_router(credits_router)
    app.include_router(files.router)
    return app
