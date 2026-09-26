"""Structured logs, request ids and RED lines (Batch 23, ADR 0011).

Every log line is one JSON object in Render's own log stream. Every request
gets an id, taken from a valid X-Request-ID or made here, which is on every
line logged while it runs and on its response, so one user's problem can be
followed from start to end. Each request writes one line with its route, status
and duration, and each reminder run one summary line: rate, errors and duration
are read from those, with no metrics server to keep.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
request_logger = logging.getLogger("tm_pro.request")

_VALID_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}


class _RequestId(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: time, level, logger, message, request id, and
    every field passed as `extra` (an `event` names the kind of line)."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", request_id_var.get()),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD and key not in entry and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_logging(settings) -> None:
    """JSON lines by default; LOG_FORMAT=text keeps them readable on a laptop."""
    handler = logging.StreamHandler()
    handler.addFilter(_RequestId())
    if str(getattr(settings, "log_format", "json")).lower() == "text":
        text = "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"
        handler.setFormatter(logging.Formatter(text))
    else:
        handler.setFormatter(JsonFormatter())
    # Only a handler this function added earlier is replaced: another one on the
    # root logger (a test's log capture, a platform's agent) is not ours to drop.
    handler._tm_pro = True
    root = logging.getLogger()
    root.handlers[:] = [h for h in root.handlers if not getattr(h, "_tm_pro", False)] + [handler]
    root.setLevel(getattr(logging, str(settings.log_level).upper(), logging.INFO))
    # The RED line below replaces uvicorn's access log; both would say the same.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    # httpx logs every URL it calls at INFO, and the Telegram API's carry the
    # bot token. Quiet in every environment, not only when APP_ENV says so.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def log_reminder_run(logger: logging.Logger, source: str, processed: int, recovered: int,
                     stats: dict, started: float) -> None:
    """The one summary line of a reminder run, from the cron endpoint or the Action."""
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    logger.info("reminder run source=%s processed=%s recovered=%s overdue=%s", source, processed, recovered,
                stats.get("overdue"), extra={
                    "event": "reminder_run", "source": source, "processed": processed, "recovered": recovered,
                    "overdue": stats.get("overdue"), "worst_late_minutes": stats.get("worst_late_minutes"),
                    "duration_ms": duration_ms})


def _log_request(scope: dict, status: int, started: float, exc_info: bool = False) -> None:
    if scope.get("path", "").startswith("/static/"):
        return
    route = getattr(scope.get("route"), "path", None) or "unmatched"  # the template, never a token
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    request_logger.log(logging.ERROR if status >= 500 else logging.INFO, "%s %s %s %sms",
                       scope.get("method"), route, status, duration_ms, exc_info=exc_info, extra={
                           "event": "request", "method": scope.get("method"), "route": route,
                           "status": status, "duration_ms": duration_ms})


class RequestContextMiddleware:
    """A pure ASGI middleware, so streamed responses pass through untouched."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _VALID_ID.fullmatch(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started, status = time.perf_counter(), 500

        async def send_with_id(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [*message.get("headers", []), (b"x-request-id", request_id.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            _log_request(scope, 500, started, exc_info=True)
            raise
        else:
            _log_request(scope, status, started)
        finally:
            request_id_var.reset(token)
