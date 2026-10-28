from pydantic_settings import BaseSettings
from functools import lru_cache
import os


class Settings(BaseSettings):
    # Supabase
    SUPABASE_URL: str = ""
    SUPABASE_JWT_SECRET: str = ""
    SUPABASE_DB_URL: str = ""  # postgresql+asyncpg://...

    # AI
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-1.5-flash"

    # Email — Resend
    RESEND_API_KEY: str = ""
    RESEND_WEBHOOK_SECRET: str = ""
    RESEND_FROM_EMAIL: str = "noreply@example.com"
    RESEND_REPLY_TO_DOMAIN: str = ""  # e.g. yourdomain.com

    # App
    APP_NAME: str = "AI Payment Recovery Agent"
    FRONTEND_URL: str = "http://localhost:3000"
    DEMO_MODE: bool = True  # Simulates sends until credentials are configured

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
