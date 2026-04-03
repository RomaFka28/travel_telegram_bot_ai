"""
Утилиты для безопасных HTTP-запросов.

Обеспечивает:
- Автоматические retry при временных ошибках
- Timeout для всех запросов
- Логирование ошибок
- Обработка исключений
"""
from __future__ import annotations

import logging
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


class HTTPRetryError(Exception):
    """Исчерпаны все попытки retry."""
    
    def __init__(self, message: str, attempts: int, last_exception: Exception | None = None):
        super().__init__(message)
        self.attempts = attempts
        self.last_exception = last_exception


def http_request_with_retry(
    url: str | urllib.request.Request,
    *,
    max_retries: int = 3,
    timeout: int = 30,
    backoff_factor: float = 1.0,
    retryable_status_codes: set[int] | None = None,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> bytes:
    """
    Выполняет HTTP-запрос с автоматическим retry при временных ошибках.
    
    Args:
        url: URL или Request объект
        max_retries: Максимальное количество попыток
        timeout: Timeout в секундах
        backoff_factor: Множитель задержки между попытками (1s, 2s, 4s...)
        retryable_status_codes: HTTP коды для retry (по умолчанию 429, 500, 502, 503, 504)
        data: POST данные
        headers: HTTP заголовки
    
    Returns:
        Ответ сервера в виде bytes
    
    Raises:
        HTTPRetryError: При исчерпании всех попыток
        urllib.error.HTTPError: При неустранимой HTTP ошибке
        urllib.error.URLError: При ошибке соединения
    """
    if retryable_status_codes is None:
        retryable_status_codes = {429, 500, 502, 503, 504}
    
    # Создать Request объект если передан просто URL
    if isinstance(url, str):
        if data is not None or headers is not None:
            request = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
        else:
            request = urllib.request.Request(url)
    else:
        request = url
    
    last_exception: Exception | None = None
    
    for attempt in range(1, max_retries + 1):
        try:
            logger.debug("HTTP request attempt %d/%d: %s", attempt, max_retries, request.full_url if hasattr(request, 'full_url') else url)
            
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        
        except urllib.error.HTTPError as e:
            last_exception = e
            
            # Не retry клиентские ошибки (кроме 429)
            if e.code not in retryable_status_codes:
                logger.warning(
                    "HTTP error %d (non-retryable): %s",
                    e.code,
                    e.reason,
                )
                raise
            
            # Логировать и продолжить retry
            wait_time = backoff_factor * (2 ** (attempt - 1))
            logger.warning(
                "HTTP error %d (attempt %d/%d), retrying in %.1fs: %s",
                e.code,
                attempt,
                max_retries,
                wait_time,
                e.reason,
            )
            
            if attempt < max_retries:
                time.sleep(wait_time)
            else:
                logger.error(
                    "HTTP error %d: all %d attempts exhausted",
                    e.code,
                    max_retries,
                )
                raise HTTPRetryError(
                    f"HTTP {e.code}: {e.reason}",
                    attempts=max_retries,
                    last_exception=e,
                ) from e
        
        except urllib.error.URLError as e:
            last_exception = e
            
            # Логировать ошибки соединения
            wait_time = backoff_factor * (2 ** (attempt - 1))
            logger.warning(
                "URL error (attempt %d/%d), retrying in %.1fs: %s",
                attempt,
                max_retries,
                wait_time,
                e.reason,
            )
            
            if attempt < max_retries:
                time.sleep(wait_time)
            else:
                logger.error(
                    "URL error: all %d attempts exhausted: %s",
                    max_retries,
                    e.reason,
                )
                raise HTTPRetryError(
                    f"Connection error: {e.reason}",
                    attempts=max_retries,
                    last_exception=e,
                ) from e
        
        except TimeoutError as e:
            last_exception = e
            
            wait_time = backoff_factor * (2 ** (attempt - 1))
            logger.warning(
                "Timeout (attempt %d/%d), retrying in %.1fs: %s",
                attempt,
                max_retries,
                wait_time,
                str(e),
            )
            
            if attempt < max_retries:
                time.sleep(wait_time)
            else:
                logger.error("Timeout: all %d attempts exhausted", max_retries)
                raise HTTPRetryError(
                    f"Request timeout: {e}",
                    attempts=max_retries,
                    last_exception=e,
                ) from e
    
    # Теоретически недостижимо, но для type checker
    raise HTTPRetryError(
        "Unexpected retry exhaustion",
        attempts=max_retries,
        last_exception=last_exception,
    )


def safe_http_get(
    url: str,
    *,
    params: dict[str, str] | None = None,
    max_retries: int = 3,
    timeout: int = 30,
    headers: dict[str, str] | None = None,
) -> bytes:
    """
    Безопасный HTTP GET с retry.
    
    Args:
        url: Базовый URL
        params: Query параметры
        max_retries: Количество попыток
        timeout: Timeout в секундах
        headers: Заголовки
    
    Returns:
        Ответ сервера
    """
    if params:
        import urllib.parse
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urllib.parse.urlencode(params)}"
    
    request_headers = {
        "User-Agent": "travel-telegram-bot/1.0",
        "Accept": "application/json",
    }
    if headers:
        request_headers.update(headers)
    
    request = urllib.request.Request(url, headers=request_headers, method="GET")
    return http_request_with_retry(
        request,
        max_retries=max_retries,
        timeout=timeout,
    )


def safe_http_post(
    url: str,
    *,
    data: bytes,
    content_type: str = "application/json",
    max_retries: int = 3,
    timeout: int = 30,
    headers: dict[str, str] | None = None,
) -> bytes:
    """
    Безопасный HTTP POST с retry.
    
    Args:
        url: URL
        data: POST данные
        content_type: Content-Type заголовок
        max_retries: Количество попыток
        timeout: Timeout в секундах
        headers: Дополнительные заголовки
    
    Returns:
        Ответ сервера
    """
    request_headers = {
        "Content-Type": content_type,
        "User-Agent": "travel-telegram-bot/1.0",
        "Accept": "application/json",
    }
    if headers:
        request_headers.update(headers)
    
    request = urllib.request.Request(url, data=data, headers=request_headers, method="POST")
    return http_request_with_retry(
        request,
        max_retries=max_retries,
        timeout=timeout,
    )
