"""
Система миграций базы данных.

Поддерживает:
- Версионированные миграции с номерами
- SQLite и PostgreSQL
- Прямые и обратные миграции (up/down)
- Таблицу отслеживания применённых миграций
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class Migration:
    """Описание одной миграции."""
    version: int
    name: str
    up_sqlite: str | list[str]
    up_postgres: str | list[str]
    down_sqlite: str | list[str] | None = None
    down_postgres: str | list[str] | None = None


@dataclass
class MigrationManager:
    """Менеджер миграций для SQLite и PostgreSQL."""
    migrations: list[Migration] = field(default_factory=list)
    
    def register(self, migration: Migration) -> None:
        """Регистрирует миграцию."""
        self.migrations.append(migration)
        self.migrations.sort(key=lambda m: m.version)
    
    def _get_applied_versions(self, conn, is_postgres: bool) -> set[int]:
        """Получает список уже применённых версий."""
        table_name = "bot_migrations"
        if is_postgres:
            # Проверить существует ли таблица
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    version BIGINT PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
        
        rows = conn.execute(f"SELECT version FROM {table_name} ORDER BY version").fetchall()
        # Handle both dict rows (PostgreSQL with dict_row) and tuple rows (SQLite)
        versions: set[int] = set()
        for row in rows:
            if isinstance(row, dict):
                versions.add(row["version"])
            else:
                versions.add(row[0])
        return versions
    
    def _record_migration(self, conn, version: int, name: str, is_postgres: bool) -> None:
        """Записывает информацию о применённой миграции."""
        table_name = "bot_migrations"
        if is_postgres:
            conn.execute(
                f"INSERT INTO {table_name} (version, name) VALUES (%s, %s) ON CONFLICT (version) DO NOTHING",
                (version, name),
            )
        else:
            conn.execute(
                f"INSERT OR IGNORE INTO {table_name} (version, name) VALUES (?, ?)",
                (version, name),
            )
    
    def _sqlite_column_exists(self, conn, table: str, column: str) -> bool:
        """Проверяет существует ли колонка в SQLite таблице."""
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row[1] == column for row in rows)
    
    def _execute_sql(self, conn, sql: str, is_postgres: bool) -> None:
        """Выполняет SQL-запрос."""
        # Для SQLite ALTER TABLE ADD COLUMN может упасть если колонка уже существует
        # Проверяем существование колонки перед добавлением
        if not is_postgres and sql.strip().upper().startswith("ALTER TABLE") and "ADD COLUMN" in sql.upper():
            # Извлекаем имя таблицы и колонки
            import re
            match = re.search(r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)", sql, re.IGNORECASE)
            if match:
                table_name = match.group(1)
                column_name = match.group(2)
                if self._sqlite_column_exists(conn, table_name, column_name):
                    return  # Колонка уже существует, пропускаем
        
        if is_postgres:
            # Для PostgreSQL использовать cursor
            with conn.cursor() as cur:
                cur.execute(sql)
        else:
            conn.execute(sql)
    
    def migrate(self, get_connection: Callable, is_postgres: bool, target_version: int | None = None) -> int:
        """
        Применяет все неприменённые миграции до target_version (или до последней).
        
        Args:
            get_connection: Функция, возвращающая соединение с БД
            is_postgres: True для PostgreSQL
            target_version: Целевая версия (None = последняя)
        
        Returns:
            Количество применённых миграций
        """
        if not self.migrations:
            logger.info("No migrations registered")
            return 0
        
        max_version = self.migrations[-1].version
        target = target_version or max_version
        
        with get_connection() as conn:
            applied = self._get_applied_versions(conn, is_postgres)
            pending = [m for m in self.migrations if m.version not in applied and m.version <= target]
            
            if not pending:
                logger.info("Database is up to date (version %d)", max(applied) if applied else 0)
                return 0
            
            logger.info("Applying %d migrations (from %d to %d)", len(pending), max(applied) if applied else 0, target)
            
            for migration in pending:
                logger.info("Applying migration #%d: %s", migration.version, migration.name)
                
                sql_statements = migration.up_postgres if is_postgres else migration.up_sqlite
                if isinstance(sql_statements, str):
                    sql_statements = [sql_statements]
                
                for sql in sql_statements:
                    try:
                        self._execute_sql(conn, sql, is_postgres)
                    except Exception as e:
                        logger.error("Failed to apply migration #%d '%s': %s", migration.version, migration.name, e)
                        conn.rollback()
                        raise
                
                # Для SQLite commit делается автоматически, для PostgreSQL — вручную
                if is_postgres:
                    conn.commit()
                
                self._record_migration(conn, migration.version, migration.name, is_postgres)
                if is_postgres:
                    conn.commit()
                
                logger.info("Applied migration #%d: %s", migration.version, migration.name)
        
        return len(pending)
    
    def rollback(self, get_connection, is_postgres: bool, steps: int = 1) -> int:
        """
        Отменяет последние применённые миграции.
        
        Args:
            get_connection: Функция, возвращающая соединение с БД
            is_postgres: True для PostgreSQL
            steps: Сколько миграций отменить
        
        Returns:
            Количество отменённых миграций
        """
        with get_connection() as conn:
            applied = self._get_applied_versions(conn, is_postgres)
            if not applied:
                logger.info("No migrations to rollback")
                return 0
            
            # Получить последние steps миграций
            to_rollback = sorted(
                [m for m in self.migrations if m.version in applied],
                key=lambda m: m.version,
                reverse=True,
            )[:steps]
            
            if not to_rollback:
                return 0
            
            logger.info("Rolling back %d migrations", len(to_rollback))
            
            for migration in to_rollback:
                down_sql = migration.down_postgres if is_postgres else migration.down_sqlite
                if not down_sql:
                    logger.warning("No down SQL for migration #%d '%s', skipping", migration.version, migration.name)
                    continue
                
                logger.info("Rolling back migration #%d: %s", migration.version, migration.name)
                
                sql_statements = down_sql if isinstance(down_sql, list) else [down_sql]
                for sql in sql_statements:
                    try:
                        self._execute_sql(conn, sql, is_postgres)
                    except Exception as e:
                        logger.error("Failed to rollback migration #%d '%s': %s", migration.version, migration.name, e)
                        conn.rollback()
                        raise
                
                if is_postgres:
                    conn.commit()
                
                # Удалить запись о миграции
                if is_postgres:
                    conn.execute("DELETE FROM bot_migrations WHERE version = %s", (migration.version,))
                    conn.commit()
                else:
                    conn.execute("DELETE FROM bot_migrations WHERE version = ?", (migration.version,))
                
                logger.info("Rolled back migration #%d: %s", migration.version, migration.name)
        
        return len(to_rollback)


def create_migration_manager() -> MigrationManager:
    """Создаёт менеджер миграций с зарегистрированными миграциями проекта."""
    manager = MigrationManager()
    
    # Миграция #1: Изначальная схема (для новых установок)
    # Для существующих БД эта миграция уже применена через init_db
    
    # Миграция #2: Добавление structured results колонок
    manager.register(Migration(
        version=2,
        name="add_structured_results",
        up_sqlite=[
            "ALTER TABLE trips ADD COLUMN flight_results TEXT",
            "ALTER TABLE trips ADD COLUMN housing_results TEXT",
            "ALTER TABLE trips ADD COLUMN activity_results TEXT",
            "ALTER TABLE trips ADD COLUMN transport_results TEXT",
            "ALTER TABLE trips ADD COLUMN rental_results TEXT",
            "ALTER TABLE trips ADD COLUMN detected_needs TEXT",
            "ALTER TABLE trips ADD COLUMN results_updated_at TEXT",
            "ALTER TABLE trips ADD COLUMN open_questions_text TEXT",
            "ALTER TABLE trips ADD COLUMN entry_requirements TEXT",
        ],
        up_postgres=[
            "ALTER TABLE trips ADD COLUMN IF NOT EXISTS flight_results TEXT",
            "ALTER TABLE trips ADD COLUMN IF NOT EXISTS housing_results TEXT",
            "ALTER TABLE trips ADD COLUMN IF NOT EXISTS activity_results TEXT",
            "ALTER TABLE trips ADD COLUMN IF NOT EXISTS transport_results TEXT",
            "ALTER TABLE trips ADD COLUMN IF NOT EXISTS rental_results TEXT",
            "ALTER TABLE trips ADD COLUMN IF NOT EXISTS detected_needs TEXT",
            "ALTER TABLE trips ADD COLUMN IF NOT EXISTS results_updated_at TEXT",
            "ALTER TABLE trips ADD COLUMN IF NOT EXISTS open_questions_text TEXT",
            "ALTER TABLE trips ADD COLUMN IF NOT EXISTS entry_requirements TEXT",
        ],
    ))
    
    # Миграция #3: reminders_sent для отслеживания отправленных напоминаний
    manager.register(Migration(
        version=3,
        name="add_reminders_sent",
        up_sqlite=["ALTER TABLE trips ADD COLUMN reminders_sent TEXT DEFAULT '[]'"],
        up_postgres=["ALTER TABLE trips ADD COLUMN IF NOT EXISTS reminders_sent TEXT DEFAULT '[]'"],
    ))

    return manager
