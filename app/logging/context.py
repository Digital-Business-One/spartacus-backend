from contextvars import ContextVar

# Populated once per request by LoggingMiddleware.
# Keys: request_id (str), user_ip (str), user_id (str | None)
request_ctx: ContextVar[dict] = ContextVar("request_ctx", default={})
