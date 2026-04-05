from __future__ import annotations

import logging
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from llm_travel_planner import LLMTravelPlanner
from openrouter_client import OpenRouterError
from travel_planner import TripRequest, TravelPlanner
from value_normalization import truncate_source_prompt

logger = logging.getLogger(__name__)

RouteType = Literal["one_way", "round_trip", "unknown"]

_NEED_KEYWORDS: dict[str, tuple[str, ...]] = {
    "tickets": (
        "билет",
        "билеты",
        "авиабилет",
        "авиабилеты",
        "перелет",
        "перелёт",
        "рейс",
        "вылет",
        "one way",
        "round trip",
        "без обратного билета",
        "в одну сторону",
        "туда-обратно",
    ),
    "housing": (
        "жилье",
        "жильё",
        "отель",
        "гостиниц",
        "гостиница",
        "апартаменты",
        "апарт",
        "квартира",
        "хостел",
    ),
    "activities": (
        "экскурси",
        "активност",
        "музей",
        "куда сходить",
        "что посмотреть",
        "маршрут",
    ),
}

_INTEREST_HINTS: dict[str, tuple[str, ...]] = {
    "прогулки": ("прогулк", "гулять", "пеш", "набережн"),
    "еда": ("еда", "гастро", "ресторан", "кафе", "бар", "кофе", "кухн"),
    "море": ("море", "пляж", "побереж"),
    "природа": ("природ", "горы", "гора", "лес", "поход", "закат"),
    "история": ("истори", "музей", "архитект", "старый город", "крепост"),
    "ночная жизнь": ("ночн", "клуб", "бар", "вечерин"),
    "шопинг": ("шоп", "магазин", "рынок", "бутик"),
}


class TripRequestExtraction(BaseModel):
    """Normalized trip-request interpretation before plan generation."""

    model_config = ConfigDict(str_strip_whitespace=True)

    destination: str | None = None
    origin: str | None = None
    dates_text: str | None = None
    days_count: int | None = None
    group_size: int | None = None
    budget_text: str | None = None
    interests: list[str] = Field(default_factory=list)
    needs: list[str] = Field(default_factory=list)
    route_type: RouteType = "unknown"
    notes: str = ""
    language_code: str = "ru"
    missing_fields: list[str] = Field(default_factory=list)
    is_actionable: bool = False

    def to_trip_request(self, planner: TravelPlanner, *, source_prompt: str) -> TripRequest:
        """Convert actionable extraction result to TripRequest."""
        if not self.destination:
            raise ValueError("TripRequestExtraction is not actionable.")

        notes_parts = [self.notes.strip()]
        if self.route_type == "one_way":
            notes_parts.append("Билет нужен в одну сторону.")
        elif self.route_type == "round_trip":
            notes_parts.append("Нужен билет туда-обратно.")
        notes = "\n".join(part for part in notes_parts if part).strip()

        return planner.build_request_from_fields(
            title="",
            destination=self.destination,
            origin=self.origin or "не указано",
            dates_text=self.dates_text or "не указаны",
            days_count=int(self.days_count or 3),
            group_size=int(self.group_size or 1),
            budget_text=self.budget_text or "Бизнес",
            interests_text=", ".join(self.interests) if self.interests else "не указаны",
            notes=notes,
            source_prompt=truncate_source_prompt(source_prompt),
            language_code=self.language_code or "ru",
        )


class TripRequestExtractor:
    """Structured request extractor with optional LLM path and deterministic fallback."""

    def __init__(self, planner: TravelPlanner | None = None) -> None:
        self._planner = planner or TravelPlanner()

    async def extract_async(
        self,
        text: str,
        *,
        language_code: str | None = None,
        planner: TravelPlanner | None = None,
        allow_llm: bool = True,
    ) -> TripRequestExtraction:
        effective_planner = planner or self._planner
        effective_language = language_code or "ru"
        if allow_llm and isinstance(effective_planner, LLMTravelPlanner):
            try:
                payload = await effective_planner.extract_trip_request_async(
                    text,
                    language_code=effective_language,
                )
                return self._normalize_payload(payload, text=text, language_code=effective_language)
            except OpenRouterError as exc:
                logger.warning("LLM trip extraction failed, using heuristic fallback: %s", exc)
            except Exception:
                logger.exception("Unexpected LLM trip extraction failure, using heuristic fallback")
        return self.extract(
            text,
            language_code=effective_language,
            planner=effective_planner,
            allow_llm=False,
        )

    def extract(
        self,
        text: str,
        *,
        language_code: str | None = None,
        planner: TravelPlanner | None = None,
        allow_llm: bool = False,
    ) -> TripRequestExtraction:
        effective_planner = planner or self._planner
        effective_language = language_code or "ru"
        if allow_llm and isinstance(effective_planner, LLMTravelPlanner):
            try:
                payload = effective_planner.extract_trip_request(
                    text,
                    language_code=effective_language,
                )
                return self._normalize_payload(payload, text=text, language_code=effective_language)
            except OpenRouterError as exc:
                logger.warning("Sync LLM trip extraction failed, using heuristic fallback: %s", exc)
            except Exception:
                logger.exception("Unexpected sync LLM extraction failure, using heuristic fallback")
        return self._fallback_extract(
            text,
            language_code=effective_language,
            planner=effective_planner,
        )

    def _normalize_payload(
        self,
        payload: dict[str, object],
        *,
        text: str,
        language_code: str,
    ) -> TripRequestExtraction:
        raw_interests = payload.get("interests")
        raw_needs = payload.get("needs")
        extraction = TripRequestExtraction.model_validate(
            {
                **payload,
                "language_code": language_code,
                "interests": self._merge_interest_lists(
                    self._coerce_string_list(raw_interests),
                    self._extract_explicit_interests(text),
                ),
                "needs": self._normalize_needs(self._coerce_string_list(raw_needs), text),
            }
        )
        extraction.missing_fields = self._normalize_missing_fields(extraction, text)
        extraction.is_actionable = bool(extraction.destination and not extraction.missing_fields)
        return extraction

    def _fallback_extract(
        self,
        text: str,
        *,
        language_code: str,
        planner: TravelPlanner,
    ) -> TripRequestExtraction:
        stripped = (text or "").strip()
        destination = self._safe_extract(lambda: planner._extract_destination(stripped))
        origin = self._safe_extract(lambda: planner._extract_origin(stripped))
        dates_text = self._safe_extract(lambda: planner._extract_dates(stripped))
        days_count = self._safe_extract(lambda: planner._extract_days_count(stripped))
        group_size = self._safe_extract(lambda: planner._extract_group_size(stripped))
        budget_text = self._safe_extract(lambda: planner._extract_budget(stripped))
        planner_interests = self._safe_extract(lambda: planner._extract_interests(stripped)) or []

        interests = self._merge_interest_lists(planner_interests, self._extract_explicit_interests(stripped))
        needs = self._normalize_needs([], stripped)
        route_type = self._detect_route_type(stripped)

        extraction = TripRequestExtraction(
            destination=self._normalize_unknown_text(destination),
            origin=self._normalize_unknown_text(origin),
            dates_text=self._normalize_unknown_text(dates_text),
            days_count=self._normalize_default_days(days_count, stripped),
            group_size=self._normalize_default_group_size(group_size, stripped),
            budget_text=self._normalize_default_budget(budget_text, stripped),
            interests=interests,
            needs=needs,
            route_type=route_type,
            notes="",
            language_code=language_code,
        )
        extraction.missing_fields = self._normalize_missing_fields(extraction, stripped)
        extraction.is_actionable = bool(extraction.destination and not extraction.missing_fields)
        return extraction

    @staticmethod
    def _safe_extract(func):
        try:
            return func()
        except Exception:
            return None

    @staticmethod
    def _normalize_unknown_text(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if not normalized or normalized.lower() in {"не указано", "не указаны"}:
            return None
        return normalized

    @staticmethod
    def _normalize_default_days(value: object, text: str) -> int | None:
        if isinstance(value, int):
            if value == 3 and not re.search(r"\b\d{1,2}\s*(?:дн|дня|дней|сут|ноч)\b", text, flags=re.IGNORECASE):
                return None
            return value
        return None

    @staticmethod
    def _normalize_default_group_size(value: object, text: str) -> int | None:
        if isinstance(value, int):
            explicit = re.search(
                r"\b(?:нас|мы)\s+\d{1,2}\b|\b\d{1,2}\s*(?:чел|человека|человек)\b|\bвдвоем\b|\bвдвоём\b|\bвтроем\b|\bвтроём\b|\bодин\b|\bодна\b",
                text,
                flags=re.IGNORECASE,
            )
            return value if explicit else None
        return None

    @staticmethod
    def _normalize_default_budget(value: object, text: str) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if not normalized:
            return None
        explicit = any(
            token in text.lower()
            for token in (
                "бюджет",
                "эконом",
                "бизнес",
                "первый класс",
                "комфорт",
                "премиум",
                "дешев",
                "недорого",
            )
        )
        return normalized if explicit else None

    @staticmethod
    def _detect_route_type(text: str) -> RouteType:
        lowered = text.lower()
        if any(token in lowered for token in ("в одну сторону", "без обратного билета", "one way", "только туда")):
            return "one_way"
        if any(token in lowered for token in ("туда-обратно", "round trip", "обратно")):
            return "round_trip"
        return "unknown"

    def _normalize_needs(self, llm_needs: list[str], text: str) -> list[str]:
        result: list[str] = []
        for value in llm_needs:
            normalized = str(value).strip().lower()
            if normalized in _NEED_KEYWORDS and normalized not in result:
                result.append(normalized)
        lowered = text.lower()
        for key, keywords in _NEED_KEYWORDS.items():
            if any(keyword in lowered for keyword in keywords) and key not in result:
                result.append(key)
        return result

    def _extract_explicit_interests(self, text: str) -> list[str]:
        lowered = text.lower()
        result: list[str] = []
        for normalized, keywords in _INTEREST_HINTS.items():
            if any(keyword in lowered for keyword in keywords) and normalized not in result:
                result.append(normalized)

        interest_blocks = re.findall(
            r"(?:интересуют|люблю|нравятся|хочется)\s+([^.!?\n]+)",
            lowered,
            flags=re.IGNORECASE,
        )
        for block in interest_blocks:
            for chunk in re.split(r",| и | / ", block):
                cleaned = re.sub(r"[^a-zа-яё0-9\- ]+", "", chunk, flags=re.IGNORECASE).strip()
                if not cleaned:
                    continue
                for normalized, keywords in _INTEREST_HINTS.items():
                    if any(keyword in cleaned for keyword in keywords):
                        if normalized not in result:
                            result.append(normalized)
                        break
        return result

    @staticmethod
    def _merge_interest_lists(*lists: list[str]) -> list[str]:
        result: list[str] = []
        for items in lists:
            for item in items:
                normalized = str(item).strip()
                if normalized and normalized not in result:
                    result.append(normalized)
        return result

    @staticmethod
    def _coerce_string_list(value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    def _normalize_missing_fields(self, extraction: TripRequestExtraction, text: str) -> list[str]:
        missing: list[str] = []
        if not extraction.destination:
            missing.append("destination")
        if "tickets" in extraction.needs:
            if not extraction.origin:
                missing.append("origin")
            if not extraction.dates_text:
                missing.append("dates_text")
            if extraction.route_type == "unknown":
                missing.append("route_type")
        return missing
