from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    database_url: str = "postgresql+psycopg://nexus:change-me@localhost:5432/nexus"
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    max_upload_bytes: int = 5 * 1024 * 1024  # blueprint: 5 MB

    # background jobs (blueprint section 8)
    job_max_retries: int = 2  # 2 retries = 3 attempts in total
    job_backoff_seconds: float = 5.0  # delay = backoff * 2 ** (attempt - 1)
    job_timeout_seconds: int = 600
    job_stale_seconds: int = 120  # running job without heartbeat -> recovered
    job_recovery_interval_seconds: int = 30
    worker_poll_seconds: float = 2.0


settings = Settings()
