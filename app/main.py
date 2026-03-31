import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from firebase_admin import initialize_app

from app.logging.config import configure_logging
from app.logging.middleware import LoggingMiddleware
from app.models.project import MyProjectOut
from app.routers import (
    accounts,
    auth,
    checkin,
    classes,
    donations,
    internal,
    medical_history,
    members,
    modalities,
    profile,
    projects,
)
from app.security.context import auth_ctx
from app.security.decorator import public, register_public_routes
from app.security.middleware import AuthMiddleware
from app.services.project_service import ProjectService

load_dotenv()
configure_logging()

app = FastAPI(title="Spartacus API", version="0.1.0")

# CORS — configured via CORS_ORIGINS env var (comma-separated).
# Use "*" in CORS_ORIGINS to allow all origins in development.
# When CORS_ORIGINS is not set, defaults to no origins allowed.
_cors_raw = os.getenv("CORS_ORIGINS", "")
_cors_origins = [o.strip() for o in _cors_raw.split(",") if o.strip()]
print(f"[CORS] allow_origins={_cors_origins}")

# Starlette applies middlewares in REVERSE add order (last added = outermost).
# Order of execution: CORS → Logging → Auth → route handler.
app.add_middleware(AuthMiddleware)
app.add_middleware(LoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(internal.router)
app.include_router(accounts.router)
app.include_router(projects.router)
app.include_router(members.router)
app.include_router(checkin.router)
app.include_router(classes.router)
app.include_router(donations.router)
app.include_router(modalities.router)
app.include_router(auth.router)
app.include_router(medical_history.router)
app.include_router(profile.router)

# Firebase Admin SDK — uses Application Default Credentials on Cloud Run.
# In local dev, uses FIREBASE_AUTH_EMULATOR_HOST if set.
# ValueError is raised when the app is already initialized (e.g. integration tests).
try:
    initialize_app()
except ValueError:
    pass


@public
@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/me")
def me():
    """Returns the authenticated user's data."""
    ctx = auth_ctx.get()
    return {"uid": ctx.user_id, "email": ctx.user_email, "roles": ctx.roles}


@app.get("/me/projects")
def my_projects() -> list[MyProjectOut]:
    """Returns all projects the authenticated user belongs to."""
    ctx = auth_ctx.get()
    return ProjectService().list_by_user(ctx.user_id)


# Resolve @public paths after all routes are registered.
register_public_routes(app.routes)
