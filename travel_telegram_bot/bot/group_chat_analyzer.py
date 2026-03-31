from __future__ import annotations

from dataclasses import dataclass, field
import re

from travel_links import CATEGORY_KEYWORDS, detect_link_needs
from travel_planner import TravelPlanner

TRAVEL_TRIGGERS = [
    "поедем", "поехали", "съездим", "путешестви", "отдохнуть",
    "отпуск", "выходные", "куда поедем", "едем в", "летим в", "маршрут",
]

KNOWN_NON_NAMES = {"не", "да", "ну", "ой", "эй", "все", "там", "тут", "так"}


@dataclass
class ChatSignal:
    has_travel_intent: bool
    destination: str | None
    origin: str | None
    dates_text: str | None
    participants_mentioned: list[str] = field(default_factory=list)
    budget_hint: str | None = None
    interests: list[str] = field(default_factory=list)
    detected_needs: list[str] = field(default_factory=list)
    need_strengths: dict[str, int] = field(default_factory=dict)
    raw_text: str = ""


class GroupChatAnalyzer:
    def __init__(self) -> None:
        self._planner = TravelPlanner()
        self._city_names: set[str] = self._collect_city_names()

    def _collect_city_names(self) -> set[str]:
        from travel_planner import DESTINATIONS

        names: set[str] = set()
        for profile in DESTINATIONS:
            names.add(profile.key.lower())
            names.add(profile.display_name.lower())
            for alias in profile.aliases:
                names.add(alias.lower())
        return names

    def analyze(self, text: str) -> ChatSignal:
        normalized = text.lower().strip()
        has_intent = any(trigger in normalized for trigger in TRAVEL_TRIGGERS)

        destination: str | None = None
        origin: str | None = None
        dates_text: str | None = None
        budget_hint: str | None = None
        interests: list[str] = []
        detected_needs = sorted(detect_link_needs(text))
        need_strengths = self._score_needs(normalized)

        if has_intent:
            try:
                destination = self._planner._extract_destination(text)
            except Exception:
                pass
            try:
                origin = self._planner._extract_origin(text)
            except Exception:
                pass
            raw_dates = self._planner._extract_dates(text)
            dates_text = raw_dates if raw_dates != "не указаны" else None
            raw_budget = self._planner._extract_budget(text)
            budget_hint = raw_budget if raw_budget != "средний" else None
            interests = self._planner._extract_interests(text)

        participants = self._extract_names(text)

        return ChatSignal(
            has_travel_intent=has_intent,
            destination=destination,
            origin=origin,
            dates_text=dates_text,
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
        return self.analyze(combined)

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
