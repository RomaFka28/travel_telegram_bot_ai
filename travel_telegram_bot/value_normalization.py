from __future__ import annotations


PLACEHOLDER_VALUES = {
    "",
    "-",
    "—",
    "не указано",
    "не указаны",
    "неизвестно",
    "неизвестны",
    "уточняется",
    "уточняются",
    "n/a",
}


def normalize_optional_text(value: str | None) -> str:
    return (value or "").strip()


def is_placeholder_value(value: str | None) -> bool:
    normalized = normalize_optional_text(value).lower()
    return normalized in PLACEHOLDER_VALUES


def normalized_search_value(value: str | None) -> str | None:
    normalized = normalize_optional_text(value)
    if is_placeholder_value(normalized):
        return None
    return normalized
