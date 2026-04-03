"""
Модуль логирования для Telegram-бота путешествий.

Обеспечивает:
- Консольное логирование с форматированием
- Файловое логирование с ротацией
- Отслеживание ошибок
- Структурированные логи для ключевых операций
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from logging import Logger
from pathlib import Path


class RichFormatter(logging.Formatter):
    """Расширенный форматтер для детализированных логов."""
    
    FORMATS = {
        logging.DEBUG: "[{asctime}] {levelname:<7} {name} {funcName}:{lineno} — {message}",
        logging.INFO: "[{asctime}] {levelname:<7} {name} — {message}",
        logging.WARNING: "[{asctime}] {levelname:<7} {name} ⚠️ {message}",
        logging.ERROR: "[{asctime}] {levelname:<7} {name} ❌ {message}",
        logging.CRITICAL: "[{asctime}] {levelname:<7} {name} 🚨 {message}",
    }
    
    def format(self, record: logging.LogRecord) -> str:
        log_fmt = self.FORMATS.get(record.levelno, self.FORMATS[logging.INFO])
        formatter = logging.Formatter(
            log_fmt,
            datefmt="%Y-%m-%d %H:%M:%S",
            style="{",
        )
        return formatter.format(record)


class CompactFormatter(logging.Formatter):
    """Компактный форматтер для продакшена."""
    
    def format(self, record: logging.LogRecord) -> str:
        fmt = "[{asctime}] {levelname:<7} {name} — {message}"
        formatter = logging.Formatter(
            fmt,
            datefmt="%Y-%m-%d %H:%M:%S",
            style="{",
        )
        return formatter.format(record)


class ErrorReportFormatter(logging.Formatter):
    """Форматтер для отчётов об ошибках (полный traceback)."""
    
    def format(self, record: logging.LogRecord) -> str:
        base_fmt = "[{asctime}] {levelname:<7} {name} — {message}"
        if record.exc_text:
            return f"{base_fmt}\n{{exc_text}}"
        return base_fmt
        
        formatter = logging.Formatter(
            base_fmt,
            datefmt="%Y-%m-%d %H:%M:%S",
            style="{",
        )
        return formatter.format(record)


def setup_logging(
    level: str = "INFO",
    log_file: str | None = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
    enable_console: bool = True,
) -> None:
    """
    Настраивает систему логирования.
    
    Args:
        level: Уровень логирования (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Путь к файлу логов (None = только консоль)
        max_bytes: Максимальный размер файла до ротации
        backup_count: Количество файлов ротации
        enable_console: Выводить ли в консоль
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Очистить существующие обработчики
    root_logger.handlers.clear()
    
    # Консольный обработчик
    if enable_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        
        # В продакшене компактный формат, в разработке — подробный
        is_production = os.getenv("RENDER", "false").lower() == "true"
        console_handler.setFormatter(CompactFormatter() if is_production else RichFormatter())
        root_logger.addHandler(console_handler)
    
    # Файловый обработчик с ротацией
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        file_handler = logging.handlers.RotatingFileHandler(
            filename=log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(CompactFormatter())
        root_logger.addHandler(file_handler)
    
    # Отдельный логгер для ошибок (всегда пишет в файл)
    error_log_path = Path(log_file).parent / "error.log" if log_file else None
    if error_log_path and error_log_path.parent.exists():
        error_handler = logging.handlers.RotatingFileHandler(
            filename=error_log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(ErrorReportFormatter())
        root_logger.addHandler(error_handler)
    
    # Отключить слишком шумные логгеры третьих сторон
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("psycopg").setLevel(logging.WARNING)
    
    # Логгер для telegram-bot
    telegram_logger = logging.getLogger("telegram")
    telegram_logger.setLevel(logging.WARNING)


def get_logger(name: str) -> Logger:
    """
    Получить именованный логгер.
    
    Args:
        name: Имя логгера (обычно __name__)
    
    Returns:
        Настроенный логгер
    """
    return logging.getLogger(name)


def log_exception_safe(logger: Logger, message: str, exc: Exception | None = None) -> None:
    """
    Безопасно залогировать исключение с контекстом.
    
    Args:
        logger: Логгер
        message: Сообщение об ошибке
        exc: Исключение (опционально)
    """
    if exc:
        logger.exception(f"{message}: {exc.__class__.__name__}: {exc}")
    else:
        logger.error(message)


def log_operation(logger: Logger, operation: str, success: bool, details: dict | None = None) -> None:
    """
    Структурированно залогировать операцию.
    
    Args:
        logger: Логгер
        operation: Название операции
        success: Успешно ли выполнена
        details: Дополнительные данные
    """
    if details:
        details_str = ", ".join(f"{k}={v}" for k, v in details.items())
        msg = f"{operation} — {'OK' if success else 'FAIL'} [{details_str}]"
    else:
        msg = f"{operation} — {'OK' if success else 'FAIL'}"
    
    if success:
        logger.info(msg)
    else:
        logger.error(msg)


def silence_noisy_loggers() -> None:
    """Отключить слишком шумные логгеры библиотек."""
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("psycopg").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
