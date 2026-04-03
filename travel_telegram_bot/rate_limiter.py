"""
Rate limiter для защиты от flood-атак.

Ограничивает количество запросов к боту от одного пользователя/чата.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from threading import Lock

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Простой rate limiter с sliding window.
    
    Пример:
        limiter = RateLimiter(max_calls=5, window_seconds=60)
        if limiter.is_allowed(user_id):
            # обработать запрос
        else:
            # отклонить
    """
    
    def __init__(self, max_calls: int, window_seconds: int) -> None:
        """
        Args:
            max_calls: Максимальное количество запросов в окне
            window_seconds: Размер окна в секундах
        """
        self._max_calls = max_calls
        self._window = window_seconds
        self._requests: dict[str | int, list[float]] = defaultdict(list)
        self._lock = Lock()
    
    def is_allowed(self, key: str | int) -> bool:
        """
        Проверяет, разрешён ли запрос.
        
        Returns:
            True если запрос разрешён, False если rate limit превышен
        """
        now = time.time()
        cutoff = now - self._window
        
        with self._lock:
            # Удалить старые записи
            self._requests[key] = [t for t in self._requests[key] if t > cutoff]
            
            if len(self._requests[key]) >= self._max_calls:
                logger.warning(
                    "Rate limit exceeded: key=%s calls=%d/%d window=%ds",
                    key, len(self._requests[key]), self._max_calls, self._window,
                )
                return False
            
            self._requests[key].append(now)
            return True
    
    def reset(self, key: str | int) -> None:
        """Сбрасывает счётчик для ключа."""
        with self._lock:
            self._requests.pop(key, None)
    
    def cleanup(self) -> int:
        """
        Удаляет все просроченные записи.
        Returns количество удалённых ключей.
        """
        now = time.time()
        cutoff = now - self._window
        removed = 0
        
        with self._lock:
            empty_keys = []
            for key, timestamps in self._requests.items():
                self._requests[key] = [t for t in timestamps if t > cutoff]
                if not self._requests[key]:
                    empty_keys.append(key)
            
            for key in empty_keys:
                del self._requests[key]
                removed += 1
        
        return removed


# Глобальные rate limiters
_llm_limiter = RateLimiter(max_calls=3, window_seconds=60)  # 3 запроса LLM в минуту
_command_limiter = RateLimiter(max_calls=20, window_seconds=60)  # 20 команд в минуту


def get_llm_limiter() -> RateLimiter:
    """Rate limiter для LLM-запросов (/plan, /newtrip)."""
    return _llm_limiter


def get_command_limiter() -> RateLimiter:
    """Rate limiter для обычных команд."""
    return _command_limiter
