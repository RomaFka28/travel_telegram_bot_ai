from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


@dataclass(slots=True)
class Settings:
    telegram_token: str
    database_dsn: str
    openrouter_api_key: str = ""
    openrouter_model: str = "qwen/qwen3.6-plus:free"
    openrouter_web_search: bool = True
    gemini_api_key: str = ""
    groq_api_key: str = ""
    travelpayouts_api_key: str = ""
    travelpayouts_marker: int | None = None
    travelpayouts_trs: int | None = None
    log_level: str = "INFO"
    playwright_enabled: bool = False
    playwright_timeout_ms: int = 12000


def _resolve_database_path(database_path: str) -> str:
    requested_path = Path(database_path).expanduser()

    try:
        requested_path.parent.mkdir(parents=True, exist_ok=True)
        return str(requested_path)
    except PermissionError:
        fallback_path = Path(__file__).resolve().parent / "data" / requested_path.name
        fallback_path.parent.mkdir(parents=True, exist_ok=True)
        warnings.warn(
            f"DATABASE_PATH={database_path!r} is not writable. Falling back to {fallback_path}.",
            RuntimeWarning,
            stacklevel=2,
        )
        return str(fallback_path)


def load_settings() -> Settings:
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    database_url = os.getenv("DATABASE_URL", "").strip()
    database_path = os.getenv("DATABASE_PATH", "data/travel_bot.db").strip()
    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    openrouter_model = os.getenv("OPENROUTER_MODEL", "qwen/qwen3.6-plus:free").strip()
    openrouter_web_search = os.getenv("OPENROUTER_WEB_SEARCH", "true").strip().lower() not in {"0", "false", "no", "off"}
    gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
    groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
    travelpayouts_api_key = os.getenv("TRAVELPAYOUTS_API_KEY", "").strip()
    travelpayouts_marker_raw = os.getenv("TRAVELPAYOUTS_MARKER", "").strip()
    travelpayouts_trs_raw = os.getenv("TRAVELPAYOUTS_TRS", "").strip()
    playwright_enabled = os.getenv("PLAYWRIGHT_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    playwright_timeout_raw = os.getenv("PLAYWRIGHT_TIMEOUT_MS", "12000").strip()

    if not telegram_token:
        raise ValueError(
            "Переменная окружения TELEGRAM_BOT_TOKEN не задана. Создайте бота через @BotFather и добавьте токен в .env"
        )

    return Settings(
        telegram_token=telegram_token,
        database_dsn=database_url or _resolve_database_path(database_path),
        openrouter_api_key=openrouter_api_key,
        openrouter_model=openrouter_model or "qwen/qwen3.6-plus:free",
        openrouter_web_search=openrouter_web_search,
        gemini_api_key=gemini_api_key,
        groq_api_key=groq_api_key,
        travelpayouts_api_key=travelpayouts_api_key,
        travelpayouts_marker=int(travelpayouts_marker_raw) if travelpayouts_marker_raw.isdigit() else None,
        travelpayouts_trs=int(travelpayouts_trs_raw) if travelpayouts_trs_raw.isdigit() else None,
        log_level=log_level or "INFO",
        playwright_enabled=playwright_enabled,
        playwright_timeout_ms=int(playwright_timeout_raw or "12000"),
    )
