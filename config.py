from typing import Literal

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
    ai_summary_model: str = "deepseek-v4-flash"
    ai_thinking_enabled: bool = False
    ai_observer_enabled: bool = True
    ai_recent_messages_limit: int = 30
    ai_summary_enabled: bool = True
    ai_summary_batch_size: int = Field(default=8, gt=0, le=100)
    ai_summary_max_batch_messages: int = Field(default=60, gt=0, le=200)
    ai_summary_max_chars: int = Field(default=2000, ge=500, le=10000)
    ai_summary_retry_cooldown_seconds: int = Field(default=900, ge=60, le=86_400)
    ai_brief_max_tokens: int = Field(default=160, ge=64, le=500)
    ai_normal_max_tokens: int = Field(default=500, ge=128, le=1200)
    ai_detailed_max_tokens: int = Field(default=1000, ge=256, le=2000)
    ai_research_enabled: bool = True
    research_cache_ttl_seconds: int = Field(default=21_600, gt=0)
    research_timeout_seconds: float = Field(default=8.0, gt=0)
    research_max_facts: int = Field(default=6, gt=0, le=12)
    research_web_base_url: str | None = None
    telegram_drop_pending_updates: bool = True
    telegram_admin_id: int = Field(default=0, ge=0)
    telegram_api_mode: Literal["cloud", "local"] = "cloud"
    telegram_api_base_url: str = ""
    telegram_api_request_timeout_seconds: int = Field(default=300, gt=0)
    telegram_upload_chunk_size: int = Field(
        default=1_048_576,
        ge=65_536,
        le=4_194_304,
    )
    media_temp_dir: str = "data/media"
    media_max_bytes: int = Field(default=48_000_000, gt=0)
    media_max_duration_seconds: int = Field(default=600, gt=0)
    media_max_concurrent_downloads: int = Field(default=2, gt=0)
    media_proxy_url: SecretStr | None = None
    media_cookies_file: str = ""
    local_media_max_bytes: int = Field(default=1_900_000_000, gt=0)
    local_media_max_duration_seconds: int = Field(default=14_400, gt=0)
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

    @property
    def effective_telegram_api_base_url(self) -> str:
        if self.telegram_api_mode == "cloud":
            return ""
        return self.telegram_api_base_url or "http://telegram-bot-api:8081"

    @property
    def effective_media_max_bytes(self) -> int:
        return (
            self.local_media_max_bytes
            if self.telegram_api_mode == "local"
            else self.media_max_bytes
        )

    @property
    def effective_media_max_duration_seconds(self) -> int:
        return (
            self.local_media_max_duration_seconds
            if self.telegram_api_mode == "local"
            else self.media_max_duration_seconds
        )


def load_settings() -> Settings:
    return Settings()
