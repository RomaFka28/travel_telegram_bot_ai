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

# Максимальный размер source_prompt для защиты от неограниченного роста
SOURCE_PROMPT_MAX_LENGTH = 4000


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


def truncate_source_prompt(value: str, max_length: int = SOURCE_PROMPT_MAX_LENGTH) -> str:
    """
    Обрезает source_prompt до безопасного размера.
    Сохраняет последние max_length символов, чтобы контекст оставался актуальным.
    Корректно обрабатывает multi-byte Unicode-символы.
    """
    text = (value or "").strip()
    if len(text) <= max_length:
        return text
    # Берём начало — оригинальный запрос важнее накопленных правок
    return text[:max_length]
