from datetime import UTC, datetime, timedelta

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    razorpay_key_id: str
    razorpay_key_secret: str
    razorpay_webhook_secret: str
    openai_api_key: str
    database_url: str = "sqlite+aiosqlite:///./revenue_recovery.db"
    # 1 "logical hour" in the Guardrail bounds table = this many real seconds.
    # Default 3600 = real-time (production). Lower it for a live demo so multi-hour
    # cooldowns compress into a session instead of requiring real wall-clock waits.
    time_scale_seconds_per_hour: float = 3600.0

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # .env takes priority over ambient OS env vars, so a stray global
        # var (e.g. OPENAI_API_KEY set for local Ollama tooling) can't
        # shadow this project's own configuration.
        return (init_settings, dotenv_settings, env_settings, file_secret_settings)


settings = Settings()


def logical_delta(hours: float) -> timedelta:
    """Convert a "logical hours" duration (Guardrail cooldowns, the abandoned-checkout
    detection threshold, etc.) into a real timedelta, scaled per settings so a multi-day
    batch can replay inside one live demo session."""
    return timedelta(seconds=hours * settings.time_scale_seconds_per_hour)


def ensure_utc(value: datetime) -> datetime:
    """SQLite has no native timezone-aware datetime type - values written as tz-aware
    (everything in this codebase uses datetime.now(UTC)) round-trip back from the DB as
    naive datetimes, which raises TypeError against a fresh datetime.now(UTC) subtraction.
    Call this on any DB-loaded datetime before arithmetic against datetime.now(UTC)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
