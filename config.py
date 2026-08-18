from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr


class Settings(BaseSettings):
    bot_token: SecretStr
    AI_api_token: SecretStr
    ai_base_url: str = "https://api.deepseek.com"
    ai_model: str = "deepseek-v4-flash"
    ai_participation_model: str = "deepseek-v4-flash"
    ai_research_model: str = "deepseek-v4-flash"
    ai_observer_model: str = "deepseek-v4-flash"
    ai_thinking_enabled: bool = False
    ai_observer_enabled: bool = True
    ai_recent_messages_limit: int = 30
    ai_research_enabled: bool = True
    research_cache_ttl_seconds: int = Field(default=21_600, gt=0)
    research_timeout_seconds: float = Field(default=8.0, gt=0)
    research_max_facts: int = Field(default=6, gt=0, le=12)
    research_web_base_url: str | None = None
    telegram_drop_pending_updates: bool = True
    bot_aliases: str = ""
    media_temp_dir: str = "data/media"
    media_max_bytes: int = Field(default=48_000_000, gt=0)
    media_max_duration_seconds: int = Field(default=600, gt=0)
    media_max_concurrent_downloads: int = Field(default=2, gt=0)
    db_path: str = "AI_module/bot_talker_memory.db"
    default_personality: str = (
        "Говори коротко, живо и по-человечески, обычным разговорным русским. "
        "Отвечай сразу по делу, обычно одной-двумя фразами. Шути, если это "
        "уместно, и не перетягивай разговор на себя. Можешь уместно материться, "
        "подкалывать и высказываться резко. Не называй себя ботом или ИИ."
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


def load_settings() -> Settings:
    return Settings()
