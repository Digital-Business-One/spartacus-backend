import asyncio
import functools
import inspect
import os
import time

import structlog

from app.logging.context import request_ctx

AUTO_MASK: frozenset[str] = frozenset(
    {"password", "token", "secret", "cpf", "authorization"}
)

logger = structlog.get_logger()


def _effective_level(override: str | None) -> str:
    return (override or os.getenv("LOG_LEVEL", "info")).lower()


def _get_ctx() -> dict:
    ctx = request_ctx.get()
    return {
        "request_id": ctx.get("request_id"),
        "user_id": ctx.get("user_id"),
    }


def _class_name(args: tuple) -> str | None:
    if args:
        cls = type(args[0])
        if cls.__module__ != "builtins":
            return cls.__name__
    return None


def _masked_params(
    func, args: tuple, kwargs: dict, extra_mask: list[str]
) -> dict:
    masked = AUTO_MASK | set(extra_mask)
    try:
        sig = inspect.signature(func)
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
    except TypeError:
        return {}
    return {
        name: "***" if name in masked else repr(value)
        for name, value in bound.arguments.items()
        if name != "self"
    }


def _elapsed_ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


def _log_return(ctx: dict, cls: str | None, method: str, ms: int) -> None:
    logger.info("return", **ctx, **{"class": cls, "method": method, "duration_ms": ms})


def _log_error(
    ctx: dict, cls: str | None, method: str, exc: Exception, ms: int
) -> None:
    logger.error(
        "error",
        **ctx,
        **{"class": cls, "method": method, "error": str(exc), "duration_ms": ms},
    )


def log(_func=None, *, level: str | None = None, mask: list[str] | None = None):
    """
    Decorator that logs entry, exit, and errors for Controller and Service methods.

    Usage:
        @log
        @log(level="debug")
        @log(mask=["password", "cpf"])
    """

    def decorator(func):
        method_name = func.__name__

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            effective = _effective_level(level)
            cls = _class_name(args)
            ctx = _get_ctx()

            entry = {**ctx, "class": cls, "method": method_name}
            if effective == "debug":
                entry["params"] = _masked_params(func, args, kwargs, mask or [])
            logger.info("call", **entry)

            start = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
                _log_return(ctx, cls, method_name, _elapsed_ms(start))
                return result
            except Exception as exc:
                _log_error(ctx, cls, method_name, exc, _elapsed_ms(start))
                raise

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            effective = _effective_level(level)
            cls = _class_name(args)
            ctx = _get_ctx()

            entry = {**ctx, "class": cls, "method": method_name}
            if effective == "debug":
                entry["params"] = _masked_params(func, args, kwargs, mask or [])
            logger.info("call", **entry)

            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                _log_return(ctx, cls, method_name, _elapsed_ms(start))
                return result
            except Exception as exc:
                _log_error(ctx, cls, method_name, exc, _elapsed_ms(start))
                raise

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    if _func is not None:
        return decorator(_func)
    return decorator
