from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    database_url: str = "postgresql+psycopg://nexus:change-me@localhost:5432/nexus"

    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    access_token_expire_minutes: int = 60  # legacy alias kept for compatibility

    max_upload_bytes: int = 5 * 1024 * 1024

    # background jobs (blueprint section 8)
    job_max_retries: int = 2
    job_backoff_seconds: float = 5.0
    job_timeout_seconds: int = 600
    job_stale_seconds: int = 120
    job_recovery_interval_seconds: int = 30
    worker_poll_seconds: float = 2.0

    # LLM (provider-neutral; Groq through the OpenAI-compatible API)
    llm_provider: str = "groq"
    llm_base_url: str = "https://api.groq.com/openai/v1"
    groq_api_key: str = ""
    anthropic_api_key: str | None = None
    model_llm: str = "openai/gpt-oss-120b"
    model_judge_eval: str = "openai/gpt-oss-120b"
    llm_max_attempts: int = 3
    llm_max_output_tokens: int = 4096
    llm_timeout_seconds: int = 60

    # budgets, enforced in the backend (never trust the provider dashboard alone)
    daily_budget_usd: float = Field(
        default=5.0, validation_alias=AliasChoices("LLM_DAILY_BUDGET_USD", "DAILY_BUDGET_USD")
    )
    # Worst-case token reserve for ONE agent job. Agent 2's self-correction loop can make up to
    # MAX_PASSES * ATTEMPTS_PER_PASS calls, and the reserve is computed pessimistically
    # (2 chars/token), so this needs real headroom or planning jobs get rejected.
    max_tokens_per_job: int = Field(
        default=80000, validation_alias=AliasChoices("LLM_MAX_TOKENS_PER_JOB", "MAX_TOKENS_PER_JOB")
    )
    llm_pricing_json: str = "{}"  # {"model": {"input_per_1m": x, "output_per_1m": y}}
    llm_require_pricing: bool = False

    # Agent 4 (plan review) - the agent loop's guard rails
    agent4_summary_trigger_tokens: int = 60000  # one review reports ~8-12k tokens; a lower trigger summarises mid-run and restarts it
    agent4_summary_keep_messages: int = 12
    agent4_max_model_calls: int = 12
    agent4_max_tool_calls: int = 20
    agent4_max_graph_steps: int = 120  # one turn (middleware + model + tools) costs several graph steps; 40 cut real reviews off

    # Langfuse
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"


settings = Settings()
