import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from .auth import get_current_user
from .database import engine, get_db
from .routers import auth, admin, integrations
from .schemas import MeUpdate, UserOut
from .seed import seed_bootstrap_admin
from .login_config import auth_mode, client_host, dashboard_urls, is_local_request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if auth_mode() != "local":
        await seed_bootstrap_admin()
    yield
    await engine.dispose()


app = FastAPI(title="Dobby Dashboard", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def local_network_only(request, call_next):
    if auth_mode() == "local" and not is_local_request(request):
        logger.warning("local_access_denied client=%s path=%s — add its range to LOCAL_NETWORKS",
                       client_host(request), request.url.path)
        return JSONResponse({"detail": "Local network access only"}, status_code=403)
    return await call_next(request)

# Session middleware is required by authlib's starlette OAuth client
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ["SECRET_KEY"],
    https_only=os.environ.get("ENV", "development") == "production",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=dashboard_urls(),
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


@app.patch("/me", response_model=UserOut, tags=["meta"])
async def update_me(
    body: MeUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Let a user set (or clear) the address Dobby invites them with."""
    update_data = body.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Nothing to update")
    try:
        # Authentication already opened the session's transaction.
        for field, value in update_data.items():
            setattr(current_user, field, value)
        db.add(current_user)
        await db.commit()
    except Exception:
        logger.exception("Failed to update profile for user %s", current_user.id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")
    return UserOut.model_validate(current_user)
