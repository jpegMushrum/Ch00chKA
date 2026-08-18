from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr


class Settings(BaseSettings):
    bot_token: SecretStr
    AI_api_token: SecretStr
    ai_base_url: str = "https://api.deepseek.com"
    ai_model: str = "deepseek-v4-flash"
    ai_participation_model: str = "deepseek-v4-flash"
    ai_observer_model: str = "deepseek-v4-flash"
    ai_thinking_enabled: bool = False
    ai_observer_enabled: bool = True
    ai_alias_generation_enabled: bool = True
    ai_recent_messages_limit: int = 30
    telegram_drop_pending_updates: bool = True
    bot_aliases: str = ""
    db_path: str = "AI_module/bot_talker_memory.db"
    default_personality: str = (
        "Говори коротко, живо и по-человечески, обычным разговорным русским. "
        "Отвечай сразу по делу, обычно одной-двумя фразами. Шути, если это "
        "уместно, и не перетягивай разговор на себя. Не называй себя ботом или ИИ."
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

def load_settings() -> Settings:
    return Settings()
