from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    razorpay_key_id: str
    razorpay_key_secret: str
    razorpay_webhook_secret: str
    openai_api_key: str
    database_url: str = "sqlite+aiosqlite:///./revenue_recovery.db"

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
