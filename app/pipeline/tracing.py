"""Wires pipecat's built-in OTel tracing (STT/LLM/TTS latency, turn spans)
to Langfuse. No-ops if LANGFUSE_PUBLIC_KEY/SECRET_KEY aren't set.
"""

from __future__ import annotations

import base64

from loguru import logger
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from pipecat.utils.tracing.setup import setup_tracing

from app.config.restaurants import ACTIVE_RESTAURANT
from app.config.settings import settings


def setup_call_tracing() -> bool:
    """Returns whether tracing is actually enabled — pass straight into
    PipelineWorker(enable_tracing=...)."""
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        logger.info("Call tracing off: LANGFUSE_PUBLIC_KEY/SECRET_KEY not set")
        return False

    auth = base64.b64encode(
        f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}".encode()
    ).decode()
    # OTLPSpanExporter only auto-appends "/v1/traces" when it falls back to
    # its own default endpoint — an explicit `endpoint=` (like this one) is
    # posted to verbatim. Langfuse's OTel ingestion route lives at
    # /api/public/otel/v1/traces, not /api/public/otel, so leaving the
    # suffix off 404s on every single span batch (silent otherwise — this
    # only surfaces as a "Failed to export span batch code: 404" warning
    # from the OTel SDK itself, not from anything in this codebase).
    exporter = OTLPSpanExporter(
        endpoint=f"{settings.langfuse_host}/api/public/otel/v1/traces",
        headers={"Authorization": f"Basic {auth}", "x-langfuse-ingestion-version": "4"},
    )
    ok = setup_tracing(service_name=f"{ACTIVE_RESTAURANT.name} voice agent", exporter=exporter)
    logger.info("Call tracing {}", "enabled" if ok else "failed to initialize")
    return ok
