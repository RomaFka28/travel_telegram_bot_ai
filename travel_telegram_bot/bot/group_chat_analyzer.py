from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from threading import Lock

from travel_links import CATEGORY_KEYWORDS, detect_link_needs
from travel_planner import TravelPlanner

TRAVEL_TRIGGERS = [
    "поедем", "поехали", "съездим", "путешестви", "отдохнуть",
    "отпуск", "выходные", "куда поедем", "едем в", "летим в", "маршрут",
]

KNOWN_NON_NAMES = {"не", "да", "ну", "ой", "эй", "все", "там", "тут", "так"}


# Глобальный кэш для city_names (инициализируется лениво)
_CITY_NAMES_CACHE: set[str] | None = None
_CITY_NAMES_LOCK = Lock()


def _get_or_build_city_names() -> set[str]:
    """Лениво строит и кэширует названия городов (thread-safe)."""
    global _CITY_NAMES_CACHE
    
    if _CITY_NAMES_CACHE is not None:
        return _CITY_NAMES_CACHE
    
    with _CITY_NAMES_LOCK:
        if _CITY_NAMES_CACHE is not None:
            return _CITY_NAMES_CACHE
        
        from travel_planner import DESTINATIONS
        
        names: set[str] = set()
        for profile in DESTINATIONS:
            names.add(profile.key.lower())
            names.add(profile.display_name.lower())
            for alias in profile.aliases:
                names.add(alias.lower())
        
        _CITY_NAMES_CACHE = names
        return names


@dataclass
class ChatSignal:
    has_travel_intent: bool
    destination: str | None
    origin: str | None
    dates_text: str | None
    days_count: int | None = None
    group_size: int | None = None
    participants_mentioned: list[str] = field(default_factory=list)
    budget_hint: str | None = None
    interests: list[str] = field(default_factory=list)
    detected_needs: list[str] = field(default_factory=list)
    need_strengths: dict[str, int] = field(default_factory=dict)
    destination_votes: list[tuple[str, int]] = field(default_factory=list)
    consensus_ready: bool = False
    raw_text: str = ""


class GroupChatAnalyzer:
    """
    Анализатор групповых сообщений.
    
    Использует общий кэш названий городов для всех экземпляров.
    Создавать можно без опаски — city_names не пересобирается.
    """
    
    def __init__(self) -> None:
        # Используем общий кэш вместо создания нового
        self._city_names: set[str] = _get_or_build_city_names()
        # TravelPlanner нужен только для извлечения сущностей — переиспользуем общий
        self._planner = TravelPlanner()

    def analyze(self, text: str) -> ChatSignal:
        normalized = text.lower().strip()

        destination: str | None = None
        origin: str | None = None
        dates_text: str | None = None
        days_count: int | None = None
        group_size: int | None = None
        budget_hint: str | None = None
        interests: list[str] = []
        detected_needs = sorted(detect_link_needs(text))
        need_strengths = self._score_needs(normalized)

        try:
            destination = self._planner._extract_destination(text)
        except Exception:
            pass
        try:
            origin = self._planner._extract_origin(text)
        except Exception:
            pass
        try:
            raw_dates = self._planner._extract_dates(text)
            dates_text = raw_dates if raw_dates != "не указаны" else None
        except Exception:
            pass
        try:
            parsed_days = self._planner._extract_days_count(text)
            days_count = parsed_days if parsed_days != 3 or self._has_explicit_days(text) else None
        except Exception:
            pass
        try:
            parsed_group_size = self._planner._extract_group_size(text)
            group_size = parsed_group_size if self._has_explicit_group_size(text) else None
        except Exception:
            pass
        try:
            raw_budget = self._planner._extract_budget(text)
            budget_hint = raw_budget if raw_budget != "Бизнес" or self._has_explicit_budget(text) else None
        except Exception:
            pass
        try:
            interests = self._planner._extract_interests(text)
        except Exception:
            pass

        has_intent = self._looks_like_trip_request(
            normalized,
            destination=destination,
            origin=origin,
            dates_text=dates_text,
            days_count=days_count,
            group_size=group_size,
            budget_hint=budget_hint,
        )

        participants = self._extract_names(text)

        return ChatSignal(
            has_travel_intent=has_intent,
            destination=destination,
            origin=origin,
            dates_text=dates_text,
            days_count=days_count,
            group_size=group_size,
            participants_mentioned=participants,
            budget_hint=budget_hint,
            interests=interests,
            detected_needs=detected_needs,
            need_strengths=need_strengths,
            raw_text=text,
        )

    def analyze_messages(self, messages: list[str]) -> ChatSignal:
        cleaned = [message.strip() for message in messages if (message or "").strip()]
        combined = "\n".join(cleaned[-8:])
        signal = self.analyze(combined)

        destination_counts: dict[str, int] = {}
        for message in cleaned[-8:]:
            try:
                destination = self._planner._extract_destination(message)
            except Exception:
                destination = None
            if not destination:
                continue
            display_name = self._planner._display_destination(destination)
            destination_counts[display_name] = destination_counts.get(display_name, 0) + 1

        ranked_destinations = sorted(destination_counts.items(), key=lambda item: (-item[1], item[0]))
        signal.destination_votes = ranked_destinations
        if not ranked_destinations:
            return signal

        leader_name, leader_votes = ranked_destinations[0]
        second_votes = ranked_destinations[1][1] if len(ranked_destinations) > 1 else 0
        total_votes = sum(count for _, count in ranked_destinations)
        signal.consensus_ready = len(ranked_destinations) == 1 or (
            leader_votes >= max(2, math.ceil(total_votes / 2))
            and leader_votes > second_votes
        )
        if signal.consensus_ready:
            signal.destination = leader_name
        elif len(ranked_destinations) > 1:
            signal.destination = None
        return signal

    def _extract_names(self, text: str) -> list[str]:
        tokens = re.findall(r"\b[А-ЯЁ][а-яё]{2,}\b", text)
        result: list[str] = []
        for token in tokens:
            lower = token.lower()
            if lower in self._city_names:
                continue
            if lower in KNOWN_NON_NAMES:
                continue
            if token not in result:
                result.append(token)
        return result

    def _score_needs(self, normalized_text: str) -> dict[str, int]:
        scores: dict[str, int] = {}
        for category, keywords in CATEGORY_KEYWORDS.items():
            score = sum(normalized_text.count(keyword) for keyword in keywords)
            if score > 0:
                scores[category] = score
        return scores

    def _looks_like_trip_request(
        self,
        normalized_text: str,
        *,
        destination: str | None,
        origin: str | None,
        dates_text: str | None,
        days_count: int | None,
        group_size: int | None,
        budget_hint: str | None,
    ) -> bool:
        if any(trigger in normalized_text for trigger in TRAVEL_TRIGGERS):
            return True
        explicit_signals = sum(1 for value in (destination, origin, dates_text, budget_hint) if value)
        if days_count is not None:
            explicit_signals += 1
        if group_size is not None:
            explicit_signals += 1
        if destination and explicit_signals >= 3:
            return True
        if destination and origin and (dates_text or days_count is not None):
            return True
        if re.search(r"\bиз\s+[A-Za-zА-Яа-яЁё\- ]+\b.*\bв\s+[A-Za-zА-Яа-яЁё\- ]+", normalized_text, flags=re.IGNORECASE):
            return True
        return False

    @staticmethod
    def _has_explicit_days(text: str) -> bool:
        return bool(
            re.search(
                r"\b\d{1,2}\s*(?:дн(?:я|ей)?|сут(?:ок)?|ноч(?:ь|и|ей)?)\b",
                text,
                flags=re.IGNORECASE,
            )
        )

    @staticmethod
    def _has_explicit_group_size(text: str) -> bool:
        lowered = text.lower()
        if any(token in lowered for token in ("вдвоем", "вдвоём", "втроем", "втроём", "нас двое", "нас трое")):
            return True
        return bool(
            re.search(
                r"\b(?:нас|мы)\s+\d{1,2}\b|\b\d{1,2}\s*(?:чел(?:овек)?|человека|человек)\b|\bкомпан(?:ия|ией)\s+из\s+\d{1,2}\b",
                text,
                flags=re.IGNORECASE,
            )
        )

    @staticmethod
    def _has_explicit_budget(text: str) -> bool:
        lowered = text.lower()
        return any(
            token in lowered
            for token in (
                "бюджет",
                "эконом",
                "бизнес",
                "первый класс",
                "до ",
                "на ",
                "от ",
                "не ограничен",
                "без ограничений",
                "подешевле",
                "дешевле",
            )
        )
