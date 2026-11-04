import json
import logging
import time
from contextvars import ContextVar
from uuid import UUID, uuid4


_request_id = ContextVar("request_id", default=None)
_STRUCTURED_FIELDS = (
    "event_id",
    "user_id",
    "attempt",
    "status_code",
    "duration_ms",
    "outcome",
)


class StructuredJsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "timestamp": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": _request_id.get(),
        }
        for field in _STRUCTURED_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


class RequestCorrelationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.logger = logging.getLogger("fakeaccount.access")

    def __call__(self, request):
        supplied_id = request.headers.get("X-Request-ID", "")
        try:
            request_id = str(UUID(supplied_id))
        except (ValueError, AttributeError):
            request_id = str(uuid4())

        token = _request_id.set(request_id)
        started_at = time.perf_counter()
        try:
            response = self.get_response(request)
            response["X-Request-ID"] = request_id
            self.logger.info(
                "http.request.complete",
                extra={
                    "request_id": request_id,
                    "status_code": response.status_code,
                    "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
                },
            )
            return response
        finally:
            _request_id.reset(token)
