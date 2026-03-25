import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from firebase_admin import initialize_app

from app.logging.config import configure_logging
from app.logging.middleware import LoggingMiddleware
from app.routers import auth, classes, internal, members, projects
from app.security.context import auth_ctx
from app.security.decorator import public, register_public_routes
from app.security.middleware import AuthMiddleware

load_dotenv()
configure_logging()

app = FastAPI(title="Spartacus API", version="0.1.0")

# CORS — origins configurable via env var (comma-separated).
# In development (APP_ENV=development), allow all origins when CORS_ORIGINS is not set.
_cors_raw = os.getenv("CORS_ORIGINS", "")
_cors_origins = [o.strip() for o in _cors_raw.split(",") if o.strip()]
_is_dev = os.getenv("APP_ENV") == "development"

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins if _cors_origins else (["*"] if _is_dev else []),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Starlette applies middlewares in reverse add order.
# AuthMiddleware executes first: sets auth_ctx and propagates user_id to request_ctx.
app.add_middleware(LoggingMiddleware)
app.add_middleware(AuthMiddleware)

app.include_router(internal.router)
app.include_router(projects.router)
app.include_router(members.router)
app.include_router(classes.router)
app.include_router(auth.router)

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


# Resolve @public paths after all routes are registered.
register_public_routes(app.routes)
