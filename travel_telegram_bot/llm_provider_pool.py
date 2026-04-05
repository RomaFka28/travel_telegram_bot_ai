from __future__ import annotations

import asyncio
import itertools
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LLMProvider:
    name: str
    daily_limit: int
    api_key: str
    base_url: str
    model: str
    use_web_search: bool = False


def build_provider_list(
    openrouter_api_key: str,
    openrouter_model: str,
    openrouter_web_search: bool,
    gemini_api_key: str,
    groq_api_key: str,
) -> list[LLMProvider]:
    """
    Build list of available providers ordered by daily_limit descending.
    Only includes providers where api_key is set.
    Order: Groq (14400) -> Gemini (1500) -> OpenRouter (500)
    """
    providers: list[LLMProvider] = []

    if groq_api_key:
        providers.append(
            LLMProvider(
                name="Groq",
                daily_limit=14400,
                api_key=groq_api_key,
                base_url="https://api.groq.com/openai/v1/chat/completions",
                model="llama-3.3-70b-versatile",
                use_web_search=False,
            )
        )

    if gemini_api_key:
        providers.append(
            LLMProvider(
                name="Gemini",
                daily_limit=1500,
                api_key=gemini_api_key,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                model="gemini-2.0-flash",
                use_web_search=True,
            )
        )

    if openrouter_api_key:
        providers.append(
            LLMProvider(
                name="OpenRouter",
                daily_limit=500,
                api_key=openrouter_api_key,
                base_url="https://openrouter.ai/api/v1/chat/completions",
                model=openrouter_model or "google/gemini-2.0-flash-exp:free",
                use_web_search=openrouter_web_search,
            )
        )

    providers.sort(key=lambda provider: provider.daily_limit, reverse=True)
    return providers


class LLMProviderPool:
    """
    Thread-safe round-robin pool.
    Each call to get_next() returns the next provider in rotation.
    """

    def __init__(self, providers: list[LLMProvider]) -> None:
        if not providers:
            raise ValueError("No LLM providers configured. Set at least one API key.")
        self._providers = list(providers)
        self._cycle = itertools.cycle(range(len(self._providers)))
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return bool(self._providers)

    async def get_next(self) -> LLMProvider:
        async with self._lock:
            index = next(self._cycle)
        return self._providers[index]

    def all_providers(self) -> list[LLMProvider]:
        return list(self._providers)
