import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .auth import get_current_user
from .database import engine
from .routers import auth, admin, integrations
from .schemas import UserOut
from .seed import seed_bootstrap_admin

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await seed_bootstrap_admin()
    yield
    await engine.dispose()


app = FastAPI(title="Dobby Dashboard", version="1.0.0", lifespan=lifespan)

# Session middleware is required by authlib's starlette OAuth client
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ["SECRET_KEY"],
    https_only=os.environ.get("ENV", "development") == "production",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("DASHBOARD_URL", "http://localhost:3000")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])
app.include_router(integrations.router, prefix="/integrations", tags=["integrations"])


@app.get("/health", tags=["meta"])
async def health():
    return {"ok": True}


@app.get("/me", response_model=UserOut, tags=["meta"])
async def me(current_user=Depends(get_current_user)):
    return UserOut.model_validate(current_user)
