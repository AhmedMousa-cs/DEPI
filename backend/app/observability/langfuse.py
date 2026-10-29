"""Langfuse (v3 SDK) wrapper. PostgreSQL stays the source of truth; every call here is best-effort."""
import logging
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, Iterator

from app.config import settings
from app.observability.redact import redact

log = logging.getLogger("nexus.langfuse")

ALLOWED_METADATA = ("project_id", "job_id", "agent", "user_role", "prompt_version", "model")


def safe_metadata(meta: dict) -> dict:
    """Whitelist + redact. Anything not listed (keys, tokens, passwords...) can never leave."""
    return {k: redact(str(meta[k])) for k in ALLOWED_METADATA if k in meta}


class GenerationHandle:
    def __init__(self) -> None:
        self.trace_id: str | None = None
        self._obs: Any = None

    def finish(self, *, usage=None, cost: Decimal | None = None, error: str | None = None) -> None:
        if self._obs is None:
            return
        try:
            kwargs: dict = {}
            if usage is not None:
                kwargs["usage_details"] = {"input": usage.input_tokens, "output": usage.output_tokens}
            if cost is not None:
                kwargs["cost_details"] = {"total": float(cost)}
            if error:
                kwargs["level"] = "ERROR"
                kwargs["status_message"] = redact(error)[:300]
            self._obs.update(**kwargs)
        except Exception as exc:  # noqa: BLE001  (observability must never break the business flow)
            log.warning("langfuse update failed: %s", type(exc).__name__)


class Tracer:
    def __init__(self, client: Any = None) -> None:
        self._client = client
        self._disabled = False

    def _lf(self):
        if self._client is not None or self._disabled:
            return self._client
        if not (settings.langfuse_public_key and settings.langfuse_secret_key):
            self._disabled = True
            return None
        try:
            from langfuse import Langfuse

            self._client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("langfuse init failed: %s", type(exc).__name__)
            self._disabled = True
        return self._client

    @contextmanager
    def generation(self, *, name: str, model: str, metadata: dict, session_id: Any = None) -> Iterator[GenerationHandle]:
        handle = GenerationHandle()
        cm = None
        try:
            lf = self._lf()
            if lf is not None:
                cm = lf.start_as_current_generation(name=name, model=model, metadata=safe_metadata(metadata))
                handle._obs = cm.__enter__()
                handle.trace_id = lf.get_current_trace_id()
                try:
                    handle._obs.update_trace(session_id=None if session_id is None else str(session_id),
                                             metadata=safe_metadata(metadata))
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            log.warning("langfuse start failed: %s", type(exc).__name__)
            cm, handle._obs = None, None
        try:
            yield handle
        finally:
            if cm is not None:
                try:
                    cm.__exit__(None, None, None)
                except Exception as exc:  # noqa: BLE001
                    log.warning("langfuse close failed: %s", type(exc).__name__)

    def flush(self) -> None:
        try:
            if self._client is not None:
                self._client.flush()
        except Exception as exc:  # noqa: BLE001
            log.warning("langfuse flush failed: %s", type(exc).__name__)


_tracer: Tracer | None = None


def get_tracer() -> Tracer:
    global _tracer
    if _tracer is None:
        _tracer = Tracer()
    return _tracer
