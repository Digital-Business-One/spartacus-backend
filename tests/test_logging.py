import pytest
from structlog.testing import capture_logs

from app.logging.context import request_ctx
from app.logging.decorator import AUTO_MASK, log

# ── helpers ──────────────────────────────────────────────────────────────────

def _set_ctx(request_id: str = "req-123", user_id: str = "user-abc") -> None:
    request_ctx.set(
        {"request_id": request_id, "user_ip": "127.0.0.1", "user_id": user_id}
    )


def _find(logs: list, event: str) -> dict:
    return next(entry for entry in logs if entry["event"] == event)


def _filter(logs: list, event: str) -> list:
    return [entry for entry in logs if entry["event"] == event]


# ── sync function ─────────────────────────────────────────────────────────────

def test_log_sync_success_logs_call_and_return():
    @log
    def add(x, y):
        return x + y

    _set_ctx()
    with capture_logs() as logs:
        result = add(1, 2)

    assert result == 3
    events = [entry["event"] for entry in logs]
    assert "call" in events
    assert "return" in events


def test_log_sync_propagates_exception_and_logs_error():
    @log
    def boom():
        raise ValueError("explodiu")

    _set_ctx()
    with capture_logs() as logs:
        with pytest.raises(ValueError, match="explodiu"):
            boom()

    error_logs = _filter(logs, "error")
    assert len(error_logs) == 1
    assert error_logs[0]["error"] == "explodiu"
    assert "duration_ms" in error_logs[0]


# ── async function ────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_log_async_success_logs_call_and_return():
    @log
    async def fetch(url: str):
        return f"response:{url}"

    _set_ctx()
    with capture_logs() as logs:
        result = await fetch("http://example.com")

    assert result == "response:http://example.com"
    events = [entry["event"] for entry in logs]
    assert "call" in events
    assert "return" in events


@pytest.mark.anyio
async def test_log_async_propagates_exception_and_logs_error():
    @log
    async def fail():
        raise RuntimeError("async boom")

    _set_ctx()
    with capture_logs() as logs:
        with pytest.raises(RuntimeError):
            await fail()

    error_logs = _filter(logs, "error")
    assert len(error_logs) == 1
    assert "async boom" in error_logs[0]["error"]


# ── context propagation ───────────────────────────────────────────────────────

def test_log_includes_request_id_and_user_id():
    @log
    def noop():
        pass

    _set_ctx(request_id="req-xyz", user_id="user-999")
    with capture_logs() as logs:
        noop()

    call_log = _find(logs, "call")
    assert call_log["request_id"] == "req-xyz"
    assert call_log["user_id"] == "user-999"


# ── AUTO_MASK ─────────────────────────────────────────────────────────────────

def test_auto_mask_fields_hidden_in_debug(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "debug")

    @log
    def create(username: str, password: str, token: str):
        pass

    _set_ctx()
    with capture_logs() as logs:
        create("alice", "s3cr3t", "tok123")

    params = _find(logs, "call")["params"]
    assert params["username"] == repr("alice")
    assert params["password"] == "***"
    assert params["token"] == "***"


def test_explicit_mask_hides_field_in_debug(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "debug")

    @log(mask=["cpf"])
    def register(name: str, cpf: str):
        pass

    _set_ctx()
    with capture_logs() as logs:
        register("João", "123.456.789-00")

    params = _find(logs, "call")["params"]
    assert params["name"] == repr("João")
    assert params["cpf"] == "***"


def test_no_params_in_info_level(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "info")

    @log
    def process(data: str):
        pass

    _set_ctx()
    with capture_logs() as logs:
        process("sensitive_data")

    assert "params" not in _find(logs, "call")


# ── @log(level="debug") ───────────────────────────────────────────────────────

def test_log_with_explicit_debug_level_includes_params(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "info")  # global is info

    @log(level="debug")
    def process(value: str):
        pass

    _set_ctx()
    with capture_logs() as logs:
        process("hello")

    assert "params" in _find(logs, "call")


# ── AUTO_MASK contents ────────────────────────────────────────────────────────

def test_auto_mask_contains_expected_fields():
    assert AUTO_MASK >= {"password", "token", "secret", "cpf", "authorization"}
