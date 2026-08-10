"""
Observability: structured JSON logging and optional OpenTelemetry tracing.

Logging is always available and cheap. OpenTelemetry is enabled only when
``PRECISIONCALC_OTEL=1`` and the SDK is importable; otherwise the tracer is a
no-op so imports never fail in minimal environments.

Environment
-----------
* ``PRECISIONCALC_LOG_LEVEL``  (default ``INFO``)
* ``PRECISIONCALC_LOG_JSON``   (default ``1`` -> JSON lines; ``0`` -> plain)
* ``PRECISIONCALC_OTEL``       (``1`` to enable tracing if the SDK is present)
* ``OTEL_EXPORTER_OTLP_ENDPOINT`` (standard OTel var, used if exporting)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from typing import Any, Iterator

_LOGGER_NAME = "precisioncalc"


class _JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON for easy ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, val in getattr(record, "extra_fields", {}).items():
            payload[key] = val
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> logging.Logger:
    """Configure and return the package logger (idempotent)."""
    logger = logging.getLogger(_LOGGER_NAME)
    if getattr(logger, "_pc_configured", False):
        return logger
    level = os.getenv("PRECISIONCALC_LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler(sys.stderr)  # stderr keeps stdio transport clean
    if os.getenv("PRECISIONCALC_LOG_JSON", "1") == "1":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    logger._pc_configured = True  # type: ignore[attr-defined]
    return logger


LOGGER = configure_logging()


def log_event(msg: str, level: int = logging.INFO, **fields: Any) -> None:
    """Emit a structured log record with arbitrary extra fields."""
    LOGGER.log(level, msg, extra={"extra_fields": fields})


# ---------------------------------------------------------------------------
# Optional OpenTelemetry
# ---------------------------------------------------------------------------

_TRACER = None
if os.getenv("PRECISIONCALC_OTEL", "0") == "1":  # pragma: no cover - optional path
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": "precisioncalc-mcp"}))
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        except Exception:  # exporter optional
            pass
        trace.set_tracer_provider(provider)
        _TRACER = trace.get_tracer("precisioncalc-mcp")
        log_event("OpenTelemetry tracing enabled")
    except Exception as exc:  # SDK not fully installed
        log_event(f"OTel requested but unavailable: {exc}", level=logging.WARNING)


@contextmanager
def observe_tool(tool: str, **attrs: Any) -> Iterator[dict[str, Any]]:
    """Time + trace a tool invocation, logging a structured record on exit.

    Yields a mutable dict; set ``ctx['status']`` to influence the final log.
    """
    ctx: dict[str, Any] = {"status": "success"}
    start = time.perf_counter()
    span_cm = _TRACER.start_as_current_span(f"tool.{tool}") if _TRACER else None
    if span_cm:  # pragma: no cover
        span_cm.__enter__()
    try:
        yield ctx
    except Exception:
        ctx["status"] = "exception"
        raise
    finally:
        duration_ms = round((time.perf_counter() - start) * 1000, 3)
        log_event("tool_call", tool=tool, duration_ms=duration_ms,
                  status=ctx.get("status"), **attrs)
        if span_cm:  # pragma: no cover
            span_cm.__exit__(None, None, None)
