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
    ai_recent_messages_limit: int = 30
    db_path: str = "AI_module/bot_talker_memory.db"
    default_personality: str = (
        "Отвечай коротко и непринужденно, используй разговорный русский язык. "
        "Шути, если это уместно, и не перетягивай разговор на себя."
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

def load_settings() -> Settings:
    return Settings()
