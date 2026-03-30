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
    database_path: str
    openrouter_api_key: str = ""
    openrouter_model: str = "stepfun/step-3.5-flash:free"
    log_level: str = "INFO"


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
    database_path = os.getenv("DATABASE_PATH", "data/travel_bot.db").strip()
    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    openrouter_model = os.getenv("OPENROUTER_MODEL", "stepfun/step-3.5-flash:free").strip()

    if not telegram_token:
        raise ValueError(
            "Переменная окружения TELEGRAM_BOT_TOKEN не задана. Создайте бота через @BotFather и добавьте токен в .env"
        )

    return Settings(
        telegram_token=telegram_token,
        database_path=_resolve_database_path(database_path),
        openrouter_api_key=openrouter_api_key,
        openrouter_model=openrouter_model or "stepfun/step-3.5-flash:free",
        log_level=log_level or "INFO",
    )
