from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://nexus:change-me@localhost:5432/nexus"
    jwt_secret: str = "change-me"


settings = Settings()