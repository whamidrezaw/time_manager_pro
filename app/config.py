from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    app_name: str = Field(default="TimeManager Pro", alias="APP_NAME")
    app_env: Literal["development", "staging", "production"] = Field(
        default="development",
        alias="APP_ENV",
    )
    app_debug: bool = Field(default=False, alias="APP_DEBUG")

    app_host: str = Field(default="127.0.0.1", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    webapp_base_url: str = Field(default="http://127.0.0.1:8000", alias="WEBAPP_BASE_URL")

    bot_token: str = Field(..., alias="BOT_TOKEN")
    telegram_initdata_max_age: int = Field(default=900, alias="TELEGRAM_INITDATA_MAX_AGE")
    telegram_initdata_future_skew: int = Field(default=60, alias="TELEGRAM_INITDATA_FUTURE_SKEW")

    @field_validator("bot_token", mode="before")
    @classmethod
    def strip_bot_token(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    telegram_webhook_secret: str = Field(..., alias="TELEGRAM_WEBHOOK_SECRET")
    telegram_bot_username: str = Field(default="Timemanager2026_bot", alias="TELEGRAM_BOT_USERNAME")
    telegram_mini_app_short_name: str = Field(default="app", alias="TELEGRAM_MINI_APP_SHORT_NAME")

    mongo_uri: str = Field(..., alias="MONGO_URI")
    mongo_db_name: str = Field(default="time_manager_pro", alias="MONGO_DB_NAME")

    max_title_len: int = Field(default=200, alias="MAX_TITLE_LEN")
    max_note_len: int = Field(default=2000, alias="MAX_NOTE_LEN")
    max_events_per_user: int = Field(default=500, alias="MAX_EVENTS_PER_USER")
    rate_limit_count: int = Field(default=30, alias="RATE_LIMIT_COUNT")

    reminder_batch_size: int = Field(default=50, alias="REMINDER_BATCH_SIZE")
    stale_processing_secs: int = Field(default=300, alias="STALE_PROCESSING_SECS")
    reminder_poll_interval_secs: int = Field(default=30, alias="REMINDER_POLL_INTERVAL_SECS")
    default_reminder_hour: int = Field(default=9, alias="DEFAULT_REMINDER_HOUR")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @computed_field
    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"

    @computed_field
    @property
    def is_staging(self) -> bool:
        return self.app_env == "staging"

    @computed_field
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def validate_critical(self) -> None:
        if not self.bot_token.strip():
            raise ValueError("BOT_TOKEN must not be empty")

        if not self.mongo_uri.strip():
            raise ValueError("MONGO_URI must not be empty")

        if not self.telegram_webhook_secret.strip():
            raise ValueError("TELEGRAM_WEBHOOK_SECRET must not be empty")

        if self.max_title_len < 10:
            raise ValueError("MAX_TITLE_LEN is unrealistically low")

        if self.max_note_len < 100:
            raise ValueError("MAX_NOTE_LEN is unrealistically low")

        if self.rate_limit_count < 1:
            raise ValueError("RATE_LIMIT_COUNT must be >= 1")

        if self.reminder_batch_size < 1:
            raise ValueError("REMINDER_BATCH_SIZE must be >= 1")

        if not (0 <= self.default_reminder_hour <= 23):
            raise ValueError("DEFAULT_REMINDER_HOUR must be between 0 and 23")

        if self.telegram_initdata_future_skew < 0:
            raise ValueError("TELEGRAM_INITDATA_FUTURE_SKEW must be >= 0")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_critical()
    return settings
