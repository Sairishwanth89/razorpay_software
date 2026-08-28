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
