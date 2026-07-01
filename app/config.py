import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://alemeno_user:alemeno_password@localhost:5432/alemeno_transactions"
    REDIS_URL: str = "redis://localhost:6379/0"
    GEMINI_API_KEY: str = ""

    # Allow reading from a local .env file
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
