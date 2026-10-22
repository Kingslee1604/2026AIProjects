"""OpenTelemetry tracing setup. Exporters are optional; trace IDs are always generated."""

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from agentforge import __version__
from agentforge.core.settings import Settings

_configured = False


def configure_tracing(settings: Settings) -> None:
    """Install a tracer provider once per process."""
    global _configured
    if _configured:
        return
    resource = Resource.create(
        {"service.name": settings.service_name, "service.version": __version__}
    )
    provider = TracerProvider(resource=resource)
    if settings.otel_exporter == "console":
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    _configured = True


def current_trace_id() -> str | None:
    """Hex trace ID of the active span, or None outside a span."""
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None
