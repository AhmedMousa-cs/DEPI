"""Scrub known secrets from any text before it reaches logs, traces or the database."""
from app.config import settings


def _secrets() -> list[str]:
    values = [settings.groq_api_key, settings.anthropic_api_key, settings.langfuse_secret_key, settings.jwt_secret]
    return [v for v in values if v and len(v) >= 6]


def redact(text: str) -> str:
    out = str(text)
    for secret in _secrets():
        out = out.replace(secret, "[REDACTED]")
    return out
