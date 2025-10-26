import logging
import json
import contextvars
from typing import Any, Dict


request_id_ctx = contextvars.ContextVar("request_id", default="-")
chat_id_ctx = contextvars.ContextVar("chat_id", default="-")


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_ctx.get(),
            "chat_id": chat_id_ctx.get(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_json_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    # Clear existing handlers in case of reloads
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root.addHandler(handler)


# OTEL tracer stub — instrumentation ready
_tracer = None


def get_tracer():
    """Return a tracer-like object.

    TODO: Replace with opentelemetry.trace.get_tracer(APP_NAME) when OTEL added.
    """
    global _tracer
    if _tracer is None:
        class _NoopTracer:
            def start_as_current_span(self, name: str):
                class _Noop:
                    def __enter__(self_inner):
                        return None

                    def __exit__(self_inner, exc_type, exc, tb):
                        return False
                return _Noop()
        _tracer = _NoopTracer()
    return _tracer


