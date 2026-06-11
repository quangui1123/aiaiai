import os, asyncio, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app import repositories as repo
from app.routers import router, admin_router, auth_router, v1_router, public_router, get_token_user


logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ai-proxy")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing database...")
    await init_db()
    logger.info("Ensuring admin user...")
    for _ in range(5):
        existing = await repo.get_user_by_email(settings.admin_email, include_disabled=True)
        if existing:
            break
        try:
            await repo.create_user(email=settings.admin_email, password=settings.admin_password, name="Admin", is_admin=True, quota=-1)
            break
        except Exception:
            await asyncio.sleep(0.5)
    logger.info(f"Server starting on {settings.host}:{settings.port}")
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Token Proxy",
        version="2.3.0",
        lifespan=lifespan,
        docs_url="/docs",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.cors_origins == "*" else [o.strip() for o in settings.cors_origins.split(",")],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    app.include_router(router)
    app.include_router(v1_router, prefix="/v1")
    app.include_router(public_router, prefix="/api/public")
    app.include_router(auth_router, prefix="/api/auth")
    app.include_router(admin_router, prefix="/admin/api")

    return app
