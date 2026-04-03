from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from queue import Queue
from threading import Lock
from typing import Any, Generator

import psycopg
from psycopg.rows import dict_row

from migrations import create_migration_manager


TRIP_COLUMNS: dict[str, str] = {
    "title": "TEXT NOT NULL",
    "destination": "TEXT",
    "origin": "TEXT",
    "dates_text": "TEXT",
    "days_count": "INTEGER NOT NULL DEFAULT 3",
    "group_size": "INTEGER NOT NULL DEFAULT 2",
    "budget_text": "TEXT",
    "interests_text": "TEXT",
    "notes": "TEXT",
    "source_prompt": "TEXT",
    "context_text": "TEXT",
    "itinerary_text": "TEXT",
    "logistics_text": "TEXT",
    "stay_text": "TEXT",
    "alternatives_text": "TEXT",
    "budget_breakdown_text": "TEXT",
    "budget_total_text": "TEXT",
    "tickets_text": "TEXT",
    "links_text": "TEXT",
    "weather_text": "TEXT",
    "weather_updated_at": "TEXT",
    "summary_short_text": "TEXT",
    "flight_results": "TEXT",
    "housing_results": "TEXT",
    "activity_results": "TEXT",
    "transport_results": "TEXT",
    "rental_results": "TEXT",
    "detected_needs": "TEXT",
    "results_updated_at": "TEXT",
    "open_questions_text": "TEXT",
    "entry_requirements_text": "TEXT",
    "status": "TEXT NOT NULL DEFAULT 'active'",
    "created_by": "BIGINT",
    "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    "updated_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
}

CHAT_SETTINGS_COLUMNS: dict[str, str] = {
    "reminders_enabled": "BOOLEAN NOT NULL DEFAULT TRUE",
    "autodraft_enabled": "BOOLEAN NOT NULL DEFAULT TRUE",
    "selected_trip_id": "BIGINT",
    "language_code": "TEXT NOT NULL DEFAULT 'ru'",
    "language_selected": "BOOLEAN NOT NULL DEFAULT FALSE",
}

EDITABLE_TRIP_FIELDS = {
    "title",
    "destination",
    "origin",
    "dates_text",
    "days_count",
    "group_size",
    "budget_text",
    "interests_text",
    "notes",
    "source_prompt",
    "context_text",
    "itinerary_text",
    "logistics_text",
    "stay_text",
    "alternatives_text",
    "budget_breakdown_text",
    "budget_total_text",
    "tickets_text",
    "links_text",
    "weather_text",
    "weather_updated_at",
    "summary_short_text",
    "flight_results",
    "housing_results",
    "activity_results",
    "transport_results",
    "rental_results",
    "detected_needs",
    "results_updated_at",
    "open_questions_text",
    "entry_requirements_text",
    "status",
}


class Database:
    def __init__(self, dsn: str, *, pool_size: int = 5) -> None:
        self.dsn = dsn
        self.is_postgres = dsn.startswith(("postgres://", "postgresql://"))
        self._pool_size = pool_size
        
        # SQLite connection pool
        self._sqlite_pool: Queue[sqlite3.Connection] | None = None
        self._sqlite_pool_lock = Lock()
        
        # Migration manager
        self._migrations = create_migration_manager()
        
        if not self.is_postgres:
            db_path = self._normalize_sqlite_path(dsn)
            self.dsn = db_path
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._init_sqlite_pool()

    @staticmethod
    def _normalize_sqlite_path(dsn: str) -> str:
        if dsn.startswith("sqlite:///"):
            return dsn.removeprefix("sqlite:///")
        return dsn

    def _init_sqlite_pool(self) -> None:
        """Инициализирует пул соединений SQLite."""
        self._sqlite_pool = Queue(maxsize=self._pool_size)
        # Предварительно создаём соединения
        for _ in range(self._pool_size):
            conn = self._create_sqlite_connection()
            self._sqlite_pool.put(conn)
    
    def _create_sqlite_connection(self) -> sqlite3.Connection:
        """Создаёт новое SQLite соединение."""
        connection = sqlite3.connect(self.dsn, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
    
    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection | psycopg.Connection, None, None]:
        """
        Получает соединение из пула (SQLite) или создаёт новое (PostgreSQL).
        
        Для SQLite: использует пул соединений с автоматическим возвратом и коммитом.
        Для PostgreSQL: создаёт новое соединение (psycopg сам управляет пулом на сервере).
        """
        if self.is_postgres:
            conn = psycopg.connect(self.dsn, row_factory=dict_row)
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        else:
            conn = self._sqlite_pool.get() if self._sqlite_pool else self._create_sqlite_connection()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                if self._sqlite_pool:
                    self._sqlite_pool.put(conn)
                else:
                    conn.close()

    def init_db(self) -> None:
        if self.is_postgres:
            self._init_postgres()
        else:
            self._init_sqlite()
        
        # Применить миграции после инициализации схемы
        self.run_migrations()
    
    def run_migrations(self, target_version: int | None = None) -> int:
        """
        Применяет все неприменённые миграции.
        
        Returns:
            Количество применённых миграций
        """
        return self._migrations.migrate(
            get_connection=self._connect,
            is_postgres=self.is_postgres,
            target_version=target_version,
        )

    def _init_sqlite(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS trips (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    destination TEXT,
                    dates_text TEXT,
                    budget_text TEXT,
                    notes TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_by INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trip_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    full_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(trip_id, user_id),
                    FOREIGN KEY (trip_id) REFERENCES trips(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS chat_settings (
                    chat_id INTEGER PRIMARY KEY,
                    reminders_enabled INTEGER NOT NULL DEFAULT 1,
                    autodraft_enabled INTEGER NOT NULL DEFAULT 1,
                    selected_trip_id INTEGER
                );

                CREATE TABLE IF NOT EXISTS chat_members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    full_name TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(chat_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS date_options (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trip_id INTEGER NOT NULL,
                    label TEXT NOT NULL,
                    created_by INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (trip_id) REFERENCES trips(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS date_votes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    option_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(option_id, user_id),
                    FOREIGN KEY (option_id) REFERENCES date_options(id) ON DELETE CASCADE
                );
                """
            )
            existing_columns = self._sqlite_table_columns(conn, "trips")
            for column_name, definition in TRIP_COLUMNS.items():
                if column_name not in existing_columns:
                    conn.execute(f"ALTER TABLE trips ADD COLUMN {column_name} {definition}")
            chat_settings_columns = self._sqlite_table_columns(conn, "chat_settings")
            for column_name, definition in CHAT_SETTINGS_COLUMNS.items():
                if column_name not in chat_settings_columns:
                    conn.execute(f"ALTER TABLE chat_settings ADD COLUMN {column_name} {definition}")

    def _init_postgres(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS trips (
                        id BIGSERIAL PRIMARY KEY,
                        chat_id BIGINT NOT NULL,
                        title TEXT NOT NULL,
                        destination TEXT,
                        dates_text TEXT,
                        budget_text TEXT,
                        notes TEXT,
                        status TEXT NOT NULL DEFAULT 'active',
                        created_by BIGINT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS participants (
                        id BIGSERIAL PRIMARY KEY,
                        trip_id BIGINT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
                        user_id BIGINT NOT NULL,
                        username TEXT,
                        full_name TEXT NOT NULL,
                        status TEXT NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (trip_id, user_id)
                    )
                    """
                )
                cur.execute(
                    """
                CREATE TABLE IF NOT EXISTS chat_settings (
                    chat_id BIGINT PRIMARY KEY,
                    reminders_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    autodraft_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    selected_trip_id BIGINT
                )
                """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chat_members (
                        id BIGSERIAL PRIMARY KEY,
                        chat_id BIGINT NOT NULL,
                        user_id BIGINT NOT NULL,
                        username TEXT,
                        full_name TEXT NOT NULL,
                        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (chat_id, user_id)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS date_options (
                        id BIGSERIAL PRIMARY KEY,
                        trip_id BIGINT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
                        label TEXT NOT NULL,
                        created_by BIGINT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS date_votes (
                        id BIGSERIAL PRIMARY KEY,
                        option_id BIGINT NOT NULL REFERENCES date_options(id) ON DELETE CASCADE,
                        user_id BIGINT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (option_id, user_id)
                    )
                    """
                )
                for column_name, definition in CHAT_SETTINGS_COLUMNS.items():
                    cur.execute(
                        f"ALTER TABLE chat_settings ADD COLUMN IF NOT EXISTS {column_name} {definition}"
                    )
                for column_name, definition in TRIP_COLUMNS.items():
                    cur.execute(
                        f"ALTER TABLE trips ADD COLUMN IF NOT EXISTS {column_name} {definition}"
                    )

    @staticmethod
    def _sqlite_table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {row[1] for row in rows}

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        return dict(row)

    @staticmethod
    def _rows_to_dicts(rows: list[Any]) -> list[dict[str, Any]]:
        return [dict(row) for row in rows]

    @staticmethod
    def _coerce_value(value: Any) -> Any:
        if isinstance(value, bool):
            return int(value)
        return value

    def _q(self, sql: str, params: tuple = ()) -> Any:
        """Execute sql written with ? placeholders on both backends."""
        if self.is_postgres:
            sql = sql.replace("?", "%s")
        with self._connect() as conn:
            if self.is_postgres:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    try:
                        return self._rows_to_dicts(cur.fetchall())
                    except Exception:
                        return None
            cur = conn.execute(sql, params)
            try:
                return self._rows_to_dicts(cur.fetchall())
            except Exception:
                return None

    def get_or_create_settings(self, chat_id: int) -> dict[str, Any]:
        if self.is_postgres:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO chat_settings(chat_id)
                        VALUES (%s)
                        ON CONFLICT (chat_id) DO NOTHING
                        """,
                        (chat_id,),
                    )
                    cur.execute(
                        "SELECT chat_id, reminders_enabled, autodraft_enabled, selected_trip_id, language_code, language_selected FROM chat_settings WHERE chat_id = %s",
                        (chat_id,),
                    )
                    row = cur.fetchone()
                    return self._row_to_dict(row) or {
                        "chat_id": chat_id,
                        "reminders_enabled": True,
                        "autodraft_enabled": True,
                        "selected_trip_id": None,
                        "language_code": "ru",
                        "language_selected": False,
                    }

        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO chat_settings(chat_id) VALUES (?)",
                (chat_id,),
            )
            row = conn.execute(
                "SELECT * FROM chat_settings WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            return self._row_to_dict(row) or {
                "chat_id": chat_id,
                "reminders_enabled": 1,
                "autodraft_enabled": 1,
                "selected_trip_id": None,
                "language_code": "ru",
                "language_selected": 0,
            }

    def toggle_reminders(self, chat_id: int) -> dict[str, Any]:
        return self._toggle_chat_setting(chat_id, "reminders_enabled")

    def toggle_autodraft(self, chat_id: int) -> dict[str, Any]:
        return self._toggle_chat_setting(chat_id, "autodraft_enabled")

    def _toggle_chat_setting(self, chat_id: int, field_name: str) -> dict[str, Any]:
        current = self.get_or_create_settings(chat_id)
        new_value = not bool(current[field_name])

        if self.is_postgres:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE chat_settings SET {field_name} = %s WHERE chat_id = %s",
                        (new_value, chat_id),
                    )
                    cur.execute(
                        "SELECT chat_id, reminders_enabled, autodraft_enabled, selected_trip_id, language_code, language_selected FROM chat_settings WHERE chat_id = %s",
                        (chat_id,),
                    )
                    row = cur.fetchone()
                    if row:
                        return self._row_to_dict(row) or {}
                    fallback = current.copy()
                    fallback[field_name] = new_value
                    return fallback

        with self._connect() as conn:
            conn.execute(
                f"UPDATE chat_settings SET {field_name} = ? WHERE chat_id = ?",
                (1 if new_value else 0, chat_id),
            )
            row = conn.execute(
                "SELECT * FROM chat_settings WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            if row:
                return self._row_to_dict(row) or {}
            fallback = current.copy()
            fallback[field_name] = 1 if new_value else 0
            return fallback

    def set_selected_trip(self, chat_id: int, trip_id: int | None) -> None:
        self.get_or_create_settings(chat_id)

        if self.is_postgres:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE chat_settings SET selected_trip_id = %s WHERE chat_id = %s",
                        (trip_id, chat_id),
                    )
            return

        with self._connect() as conn:
            conn.execute(
                "UPDATE chat_settings SET selected_trip_id = ? WHERE chat_id = ?",
                (trip_id, chat_id),
            )

    def set_chat_language(self, chat_id: int, language_code: str) -> dict[str, Any]:
        self.get_or_create_settings(chat_id)
        normalized = "en" if (language_code or "").lower() == "en" else "ru"

        if self.is_postgres:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE chat_settings SET language_code = %s, language_selected = %s WHERE chat_id = %s",
                        (normalized, True, chat_id),
                    )
                    cur.execute(
                        "SELECT chat_id, reminders_enabled, autodraft_enabled, selected_trip_id, language_code, language_selected FROM chat_settings WHERE chat_id = %s",
                        (chat_id,),
                    )
                    row = cur.fetchone()
                    return self._row_to_dict(row) or {}

        with self._connect() as conn:
            conn.execute(
                "UPDATE chat_settings SET language_code = ?, language_selected = ? WHERE chat_id = ?",
                (normalized, 1, chat_id),
            )
            row = conn.execute(
                "SELECT * FROM chat_settings WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            return self._row_to_dict(row) or {}

    def get_chat_language(self, chat_id: int) -> str:
        settings = self.get_or_create_settings(chat_id)
        return "en" if settings.get("language_code") == "en" else "ru"

    def get_selected_trip(self, chat_id: int) -> dict[str, Any] | None:
        settings = self.get_or_create_settings(chat_id)
        selected_trip_id = settings.get("selected_trip_id")
        if not selected_trip_id:
            return None
        return self.get_trip_by_id(int(selected_trip_id))

    def get_active_trip(self, chat_id: int) -> dict[str, Any] | None:
        rows = self._q(
            """
            SELECT *
            FROM trips
            WHERE chat_id = ? AND status = 'active'
            ORDER BY id DESC
            LIMIT 1
            """,
            (chat_id,),
        )
        return rows[0] if rows else None

    def get_trip_by_id(self, trip_id: int) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM trips WHERE id = ?", (trip_id,))
        return rows[0] if rows else None

    def create_trip(self, chat_id: int, created_by: int | None, payload: dict[str, Any]) -> int:
        values = {key: value for key, value in payload.items() if key in EDITABLE_TRIP_FIELDS and key != "status"}
        values.setdefault("status", "active")
        fields = ["chat_id", "created_by", *values.keys()]
        params = [chat_id, created_by, *values.values()]

        if self.is_postgres:
            placeholders = ", ".join(["%s"] * len(fields))
            sql = f"INSERT INTO trips({', '.join(fields)}) VALUES ({placeholders}) RETURNING id"
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE trips SET status = 'archived', updated_at = CURRENT_TIMESTAMP WHERE chat_id = %s AND status = 'active'",
                        (chat_id,),
                    )
                    cur.execute(sql, params)
                    return int(cur.fetchone()["id"])

        placeholders = ", ".join(["?"] * len(fields))
        sql = f"INSERT INTO trips({', '.join(fields)}) VALUES ({placeholders})"
        with self._connect() as conn:
            conn.execute(
                "UPDATE trips SET status = 'archived', updated_at = CURRENT_TIMESTAMP WHERE chat_id = ? AND status = 'active'",
                (chat_id,),
            )
            cursor = conn.execute(sql, params)
            return int(cursor.lastrowid)

    def archive_active_trip(self, chat_id: int) -> bool:
        if self.is_postgres:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE trips SET status = 'archived', updated_at = CURRENT_TIMESTAMP WHERE chat_id = %s AND status = 'active'",
                        (chat_id,),
                    )
                    return cur.rowcount > 0

        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE trips SET status = 'archived', updated_at = CURRENT_TIMESTAMP WHERE chat_id = ? AND status = 'active'",
                (chat_id,),
            )
            return cursor.rowcount > 0

    def list_trips(self, chat_id: int, status: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM trips WHERE chat_id = ?"
        params: tuple[Any, ...] = (chat_id,)
        if status:
            sql += " AND status = ?"
            params += (status,)
        sql += " ORDER BY updated_at DESC, id DESC"
        rows = self._q(sql, params)
        return rows or []

    def activate_trip(self, chat_id: int, trip_id: int) -> bool:
        trip = self.get_trip_by_id(trip_id)
        if not trip or int(trip["chat_id"]) != chat_id:
            return False

        if self.is_postgres:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE trips SET status = 'archived', updated_at = CURRENT_TIMESTAMP WHERE chat_id = %s AND status = 'active' AND id <> %s",
                        (chat_id, trip_id),
                    )
                    cur.execute(
                        "UPDATE trips SET status = 'active', updated_at = CURRENT_TIMESTAMP WHERE id = %s AND chat_id = %s",
                        (trip_id, chat_id),
                    )
                    activated = cur.rowcount > 0
            if activated:
                self.set_selected_trip(chat_id, trip_id)
            return activated

        with self._connect() as conn:
            conn.execute(
                "UPDATE trips SET status = 'archived', updated_at = CURRENT_TIMESTAMP WHERE chat_id = ? AND status = 'active' AND id <> ?",
                (chat_id, trip_id),
            )
            cursor = conn.execute(
                "UPDATE trips SET status = 'active', updated_at = CURRENT_TIMESTAMP WHERE id = ? AND chat_id = ?",
                (trip_id, chat_id),
            )
            activated = cursor.rowcount > 0
        if activated:
            self.set_selected_trip(chat_id, trip_id)
        return activated

    def delete_trip(self, chat_id: int, trip_id: int) -> bool:
        trip = self.get_trip_by_id(trip_id)
        if not trip or int(trip["chat_id"]) != chat_id:
            return False
        selected_trip = self.get_selected_trip(chat_id)
        should_clear_selected = bool(selected_trip and int(selected_trip["id"]) == trip_id)

        if self.is_postgres:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM trips WHERE id = %s AND chat_id = %s",
                        (trip_id, chat_id),
                    )
                    deleted = cur.rowcount > 0
            if deleted:
                if should_clear_selected:
                    self.set_selected_trip(chat_id, None)
            return deleted

        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM trips WHERE id = ? AND chat_id = ?",
                (trip_id, chat_id),
            )
            deleted = cursor.rowcount > 0
        if deleted:
            if should_clear_selected:
                self.set_selected_trip(chat_id, None)
        return deleted

    def update_trip_fields(self, trip_id: int, updates: dict[str, Any]) -> None:
        safe_updates = {key: value for key, value in updates.items() if key in EDITABLE_TRIP_FIELDS}
        if not safe_updates:
            return

        params = [self._coerce_value(value) for value in safe_updates.values()]

        if self.is_postgres:
            assignments = ", ".join(f"{field} = %s" for field in safe_updates)
            params.append(trip_id)
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE trips SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                        params,
                    )
            return

        assignments = ", ".join(f"{field} = ?" for field in safe_updates)
        params.append(trip_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE trips SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                params,
            )

    def upsert_participant(
        self,
        trip_id: int,
        user_id: int,
        username: str | None,
        full_name: str,
        status: str,
    ) -> None:
        params = (trip_id, user_id, username or "", full_name, status)

        if self.is_postgres:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO participants(trip_id, user_id, username, full_name, status)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT(trip_id, user_id)
                        DO UPDATE SET
                            username = EXCLUDED.username,
                            full_name = EXCLUDED.full_name,
                            status = EXCLUDED.status,
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        params,
                    )
            return

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO participants(trip_id, user_id, username, full_name, status)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(trip_id, user_id)
                DO UPDATE SET
                    username = excluded.username,
                    full_name = excluded.full_name,
                    status = excluded.status,
                    updated_at = CURRENT_TIMESTAMP
                """,
                params,
            )

    def list_participants(self, trip_id: int) -> list[dict[str, Any]]:
        if self.is_postgres:
            rows = self._q(
                "SELECT * FROM participants WHERE trip_id = ? ORDER BY LOWER(full_name) ASC",
                (trip_id,),
            )
        else:
            rows = self._q(
                "SELECT * FROM participants WHERE trip_id = ? ORDER BY full_name COLLATE NOCASE ASC",
                (trip_id,),
            )
        return rows or []

    def upsert_chat_member(
        self,
        chat_id: int,
        user_id: int,
        username: str | None,
        full_name: str,
    ) -> None:
        params = (chat_id, user_id, username or "", full_name)
        if self.is_postgres:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO chat_members(chat_id, user_id, username, full_name)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT(chat_id, user_id)
                        DO UPDATE SET
                            username = EXCLUDED.username,
                            full_name = EXCLUDED.full_name,
                            last_seen_at = CURRENT_TIMESTAMP
                        """,
                        params,
                    )
            return

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_members(chat_id, user_id, username, full_name)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id)
                DO UPDATE SET
                    username = excluded.username,
                    full_name = excluded.full_name,
                    last_seen_at = CURRENT_TIMESTAMP
                """,
                params,
            )

    def count_chat_members(self, chat_id: int) -> int:
        rows = self._q("SELECT COUNT(*) AS total FROM chat_members WHERE chat_id = ?", (chat_id,))
        return int(rows[0]["total"]) if rows else 0

    def add_date_option(self, trip_id: int, label: str, created_by: int) -> int:
        if self.is_postgres:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO date_options(trip_id, label, created_by) VALUES (%s, %s, %s) RETURNING id",
                        (trip_id, label.strip(), created_by),
                    )
                    return int(cur.fetchone()["id"])

        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO date_options(trip_id, label, created_by) VALUES (?, ?, ?)",
                (trip_id, label.strip(), created_by),
            )
            return int(cursor.lastrowid)

    def get_date_option(self, option_id: int) -> dict[str, Any] | None:
        rows = self._q(
            "SELECT * FROM date_options WHERE id = ?",
            (option_id,),
        )
        return rows[0] if rows else None

    def list_date_options(self, trip_id: int) -> list[dict[str, Any]]:
        rows = self._q(
            """
            SELECT
                d.id,
                d.label,
                COUNT(v.id) AS votes
            FROM date_options d
            LEFT JOIN date_votes v ON v.option_id = d.id
            WHERE d.trip_id = ?
            GROUP BY d.id, d.label
            ORDER BY votes DESC, d.id ASC
            """,
            (trip_id,),
        )
        return rows or []

    def toggle_date_vote(self, option_id: int, user_id: int) -> tuple[bool, int]:
        if self.is_postgres:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM date_votes WHERE option_id = %s AND user_id = %s",
                        (option_id, user_id),
                    )
                    existing = cur.fetchone()
                    if existing:
                        cur.execute(
                            "DELETE FROM date_votes WHERE option_id = %s AND user_id = %s",
                            (option_id, user_id),
                        )
                        added = False
                    else:
                        cur.execute(
                            "INSERT INTO date_votes(option_id, user_id) VALUES (%s, %s)",
                            (option_id, user_id),
                        )
                        added = True

                    cur.execute(
                        "SELECT COUNT(*) AS cnt FROM date_votes WHERE option_id = %s",
                        (option_id,),
                    )
                    total_votes = cur.fetchone()["cnt"]
                    return added, int(total_votes)

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM date_votes WHERE option_id = ? AND user_id = ?",
                (option_id, user_id),
            ).fetchone()
            if existing:
                conn.execute(
                    "DELETE FROM date_votes WHERE option_id = ? AND user_id = ?",
                    (option_id, user_id),
                )
                added = False
            else:
                conn.execute(
                    "INSERT INTO date_votes(option_id, user_id) VALUES (?, ?)",
                    (option_id, user_id),
                )
                added = True

            total_votes = conn.execute(
                "SELECT COUNT(*) AS cnt FROM date_votes WHERE option_id = ?",
                (option_id,),
            ).fetchone()["cnt"]
            return added, int(total_votes)
