"""
Система метрик и мониторинга.

Отслеживает:
- Время ответа LLM
- Процент fallback на эвристику
- Частоту использования команд
- Ошибки и их частоту
- Время выполнения операций с БД
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MetricPoint:
    """Одна точка метрики."""
    name: str
    value: float
    tags: dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class MetricsCollector:
    """
    Коллектор метрик с агрегацией.
    
    Поддерживает:
    - Счётчики (counters)
    - Таймеры (timers)
    - Гистограммы (histograms)
    """
    
    def __init__(self, flush_interval: int = 300) -> None:
        """
        Args:
            flush_interval: Интервал сброса метрик в секундах (по умолчанию 5 мин)
        """
        self._counters: dict[str, int] = defaultdict(int)
        self._timers: dict[str, list[float]] = defaultdict(list)
        self._flush_interval = flush_interval
        self._last_flush = time.time()
    
    def increment(self, name: str, value: int = 1, tags: dict[str, str] | None = None) -> None:
        """Увеличивает счётчик."""
        key = self._make_key(name, tags)
        self._counters[key] += value
    
    def record_time(self, name: str, duration: float, tags: dict[str, str] | None = None) -> None:
        """Записывает время выполнения."""
        key = self._make_key(name, tags)
        self._timers[key].append(duration)
    
    def timer(self, name: str, tags: dict[str, str] | None = None) -> TimerContext:
        """Контекстный менеджер для замера времени."""
        return TimerContext(self, name, tags)
    
    def get_counter(self, name: str, tags: dict[str, str] | None = None) -> int:
        """Получает значение счётчика."""
        key = self._make_key(name, tags)
        return self._counters.get(key, 0)
    
    def get_timer_stats(self, name: str, tags: dict[str, str] | None = None) -> dict[str, float]:
        """Получает статистику таймера."""
        key = self._make_key(name, tags)
        values = self._timers.get(key, [])
        if not values:
            return {"count": 0, "min": 0, "max": 0, "avg": 0, "p50": 0, "p95": 0}
        
        sorted_values = sorted(values)
        count = len(sorted_values)
        return {
            "count": count,
            "min": sorted_values[0],
            "max": sorted_values[-1],
            "avg": sum(sorted_values) / count,
            "p50": sorted_values[int(count * 0.5)],
            "p95": sorted_values[int(count * 0.95)] if count > 1 else sorted_values[0],
        }
    
    def get_report(self) -> dict[str, Any]:
        """Получает полный отчёт по всем метрикам."""
        report: dict[str, Any] = {
            "counters": dict(self._counters),
            "timers": {},
        }
        
        for key in self._timers:
            values = self._timers[key]
            if values:
                sorted_values = sorted(values)
                count = len(sorted_values)
                report["timers"][key] = {
                    "count": count,
                    "min": round(sorted_values[0], 3),
                    "max": round(sorted_values[-1], 3),
                    "avg": round(sum(sorted_values) / count, 3),
                    "p50": round(sorted_values[int(count * 0.5)], 3),
                    "p95": round(sorted_values[int(count * 0.95)], 3) if count > 1 else round(sorted_values[0], 3),
                }
        
        return report
    
    def log_report(self) -> None:
        """Логирует отчёт по метрикам."""
        report = self.get_report()
        
        logger.info("=== Metrics Report ===")
        logger.info("Counters:")
        for key, value in report["counters"].items():
            logger.info("  %s: %d", key, value)
        
        logger.info("Timers:")
        for key, stats in report["timers"].items():
            logger.info(
                "  %s: count=%d avg=%.3fs p50=%.3fs p95=%.3fs",
                key,
                stats["count"],
                stats["avg"],
                stats["p50"],
                stats["p95"],
            )
        logger.info("=== End Report ===")
    
    def flush(self) -> None:
        """Сбрасывает все метрики."""
        self._counters.clear()
        self._timers.clear()
        self._last_flush = time.time()
        logger.info("Metrics flushed")
    
    def _make_key(self, name: str, tags: dict[str, str] | None = None) -> str:
        """Создаёт ключ метрики из имени и тегов."""
        if not tags:
            return name
        tags_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}[{tags_str}]"


class TimerContext:
    """Контекстный менеджер для замера времени."""
    
    def __init__(self, collector: MetricsCollector, name: str, tags: dict[str, str] | None = None) -> None:
        self._collector = collector
        self._name = name
        self._tags = tags
        self._start = 0.0
    
    def __enter__(self) -> "TimerContext":
        self._start = time.perf_counter()
        return self
    
    def __exit__(self, *args: Any) -> None:
        duration = time.perf_counter() - self._start
        self._collector.record_time(self._name, duration, self._tags)


# Глобальный коллектор метрик
_metrics: MetricsCollector | None = None


def get_metrics() -> MetricsCollector:
    """Получает глобальный коллектор метрик."""
    global _metrics
    if _metrics is None:
        _metrics = MetricsCollector()
    return _metrics


def reset_metrics() -> None:
    """Сбрасывает глобальный коллектор метрик (для тестов)."""
    global _metrics
    _metrics = None


# Convenience функции для быстрого доступа
def increment(name: str, value: int = 1, tags: dict[str, str] | None = None) -> None:
    get_metrics().increment(name, value, tags)


def record_time(name: str, duration: float, tags: dict[str, str] | None = None) -> None:
    get_metrics().record_time(name, duration, tags)


def timer(name: str, tags: dict[str, str] | None = None) -> TimerContext:
    return get_metrics().timer(name, tags)
