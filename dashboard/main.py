import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .auth import get_current_user
from .database import engine
from .routers import auth, admin, integrations, local_auth
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

_dashboard_url = os.environ.get("DASHBOARD_URL", "http://localhost:3000")
_allowed_origins = list({
    _dashboard_url,
    "http://localhost:3000",
    "http://localhost:8000",
    "http://127.0.0.1:3000",
})

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(local_auth.router, prefix="/auth", tags=["auth"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])
app.include_router(integrations.router, prefix="/integrations", tags=["integrations"])


@app.get("/health", tags=["meta"])
async def health():
    return {"ok": True}


@app.get("/me", response_model=UserOut, tags=["meta"])
async def me(current_user=Depends(get_current_user)):
    return UserOut.model_validate(current_user)
