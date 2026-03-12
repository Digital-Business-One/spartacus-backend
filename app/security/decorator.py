import asyncio
import functools
import os
import re

from fastapi import HTTPException
from starlette.requests import Request

from app.security.context import auth_ctx

# Functions marked with @public — populated at import time.
_PUBLIC_FUNCTIONS: set = set()

# Compiled regex patterns for public routes (supports path parameters).
# Populated by register_public_routes().
_PUBLIC_PATTERNS: list[re.Pattern] = []


def public(func):
    """Mark a route handler as exempt from authentication."""
    func.__is_public__ = True
    _PUBLIC_FUNCTIONS.add(func)
    return func


def register_public_routes(routes) -> None:
    """
    Scan app routes and register path patterns for @public-marked handlers.
    Converts path templates (e.g. /{project_id}/classes) to regex patterns
    so parameterized routes are correctly matched at request time.
    Must be called once after all routes are registered in main.py.
    """
    for route in routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is not None and getattr(endpoint, "__is_public__", False):
            path = getattr(route, "path", "")
            if path:
                pattern = re.sub(r"\{[^}]+\}", "[^/]+", path)
                _PUBLIC_PATTERNS.append(re.compile(f"^{pattern}$"))


def is_public(request: Request) -> bool:
    path = request.url.path
    return any(p.match(path) for p in _PUBLIC_PATTERNS)


def require_roles(*roles: str):
    """
    Decorator that enforces role-based access control on a route handler.
    Reads AuthContext from ContextVar — no parameter needed.
    Returns 403 if the authenticated user does not hold at least one required role
    within the current project context (X-Project-Id).
    """
    def decorator(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            ctx = auth_ctx.get()
            if ctx is None or not any(r in ctx.roles for r in roles):
                raise HTTPException(status_code=403, detail="Permissão insuficiente")
            return await func(*args, **kwargs)

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            ctx = auth_ctx.get()
            if ctx is None or not any(r in ctx.roles for r in roles):
                raise HTTPException(status_code=403, detail="Permissão insuficiente")
            return func(*args, **kwargs)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def require_root(func):
    """
    Decorator that restricts a route handler to the ROOT project only.
    Returns 403 if X-Project-Id does not match ROOT_PROJECT_ID env var.
    Combine with @require_roles to also enforce role within the root project.
    """
    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        ctx = auth_ctx.get()
        root_project_id = os.getenv("ROOT_PROJECT_ID", "")
        if ctx is None or ctx.project_id != root_project_id:
            raise HTTPException(
                status_code=403, detail="Operação restrita ao projeto ROOT"
            )
        return await func(*args, **kwargs)

    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        ctx = auth_ctx.get()
        root_project_id = os.getenv("ROOT_PROJECT_ID", "")
        if ctx is None or ctx.project_id != root_project_id:
            raise HTTPException(
                status_code=403, detail="Operação restrita ao projeto ROOT"
            )
        return func(*args, **kwargs)

    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    return sync_wrapper
