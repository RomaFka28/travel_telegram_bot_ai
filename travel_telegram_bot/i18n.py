"""
Модуль интернационализации.

Загружает переводы из отдельных JSON-файлов по локалям (locales/ru.json, locales/en.json).
Поддерживает:
- Динамическое добавление новых языков без изменения кода
- Fallback на русский при отсутствии ключа
- Кэширование загруженных локалей
- Форматирование строк с kwargs
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Кэш загруженных локалей
_LOADED_LOCALES: dict[str, dict[str, str]] = {}
_FALLBACK_LANGUAGE = "ru"

# Для обратной совместимости — монолитный словарь (загружается лениво)
TRANSLATIONS: dict[str, dict[str, str]] = {}


def _locales_dir() -> Path:
    """Возвращает директорию с JSON-файлами локалей."""
    return Path(__file__).resolve().parent / "locales"


def load_locale(language_code: str) -> dict[str, str]:
    """
    Загружает локаль из JSON-файла с кэшированием.
    
    Args:
        language_code: Код языка (ru, en, etc.)
    
    Returns:
        Словарь переводов для указанной локали
    
    Raises:
        FileNotFoundError: Если файл локали не найден
        json.JSONDecodeError: Если файл содержит невалидный JSON
    """
    if language_code in _LOADED_LOCALES:
        return _LOADED_LOCALES[language_code]
    
    locale_file = _locales_dir() / f"{language_code}.json"
    if not locale_file.exists():
        raise FileNotFoundError(f"Locale file not found: {locale_file}")
    
    with open(locale_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    _LOADED_LOCALES[language_code] = data
    logger.debug("Loaded locale: %s (%d keys)", language_code, len(data))
    return data


def get_all_translations() -> dict[str, dict[str, str]]:
    """
    Возвращает все загрученные переводы в формате TRANSLATIONS.
    Для обратной совместимости с кодом, который ожидает TRANSLATIONS.
    """
    if not TRANSLATIONS:
        for locale_file in _locales_dir().glob("*.json"):
            language_code = locale_file.stem
            try:
                TRANSLATIONS[language_code] = load_locale(language_code)
            except (FileNotFoundError, json.JSONDecodeError) as e:
                logger.warning("Failed to load locale %s: %s", language_code, e)
    return TRANSLATIONS


def get_language(value: str | None) -> str:
    """Нормализует код языка."""
    return "en" if (value or "").lower() == "en" else "ru"


def tr(language_code: str | None, key: str, **kwargs) -> str:
    """
    Получить перевод для указанного языка.
    
    Args:
        language_code: Код языка
        key: Ключ перевода
        **kwargs: Параметры для форматирования строки
    
    Returns:
        Переведённая строка или ключ при отсутствии перевода
    """
    lang = get_language(language_code)
    
    try:
        locale = load_locale(lang)
    except (FileNotFoundError, json.JSONDecodeError):
        locale = {}
    
    value = locale.get(key)
    if not value and lang != _FALLBACK_LANGUAGE:
        try:
            fallback_locale = load_locale(_FALLBACK_LANGUAGE)
            value = fallback_locale.get(key)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
    
    if not value:
        value = key
    
    if kwargs:
        return value.format(**kwargs)
    return value


# Загрузить переводы при импорте для обратной совместимости
try:
    get_all_translations()
except Exception:
    import logging
    logging.getLogger(__name__).exception("Failed to load translations. Bot will use raw keys.")
