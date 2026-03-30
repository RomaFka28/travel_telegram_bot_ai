from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row


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
    "weather_text": "TEXT",
    "weather_updated_at": "TEXT",
    "status": "TEXT NOT NULL DEFAULT 'active'",
    "created_by": "BIGINT",
    "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    "updated_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
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
    "weather_text",
    "weather_updated_at",
    "status",
}


class Database:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.is_postgres = dsn.startswith(("postgres://", "postgresql://"))

        if not self.is_postgres:
            db_path = self._normalize_sqlite_path(dsn)
            self.dsn = db_path
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _normalize_sqlite_path(dsn: str) -> str:
        if dsn.startswith("sqlite:///"):
            return dsn.removeprefix("sqlite:///")
        return dsn

    def _connect(self):
        if self.is_postgres:
            return psycopg.connect(self.dsn, row_factory=dict_row)

        connection = sqlite3.connect(self.dsn)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def init_db(self) -> None:
        if self.is_postgres:
            self._init_postgres()
            return
        self._init_sqlite()

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
                    reminders_enabled INTEGER NOT NULL DEFAULT 1
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
                        reminders_enabled BOOLEAN NOT NULL DEFAULT TRUE
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
                        "SELECT chat_id, reminders_enabled FROM chat_settings WHERE chat_id = %s",
                        (chat_id,),
                    )
                    row = cur.fetchone()
                    return self._row_to_dict(row) or {"chat_id": chat_id, "reminders_enabled": True}

        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO chat_settings(chat_id) VALUES (?)",
                (chat_id,),
            )
            row = conn.execute(
                "SELECT * FROM chat_settings WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            return self._row_to_dict(row) or {"chat_id": chat_id, "reminders_enabled": 1}

    def toggle_reminders(self, chat_id: int) -> dict[str, Any]:
        current = self.get_or_create_settings(chat_id)
        new_value = not bool(current["reminders_enabled"])

        if self.is_postgres:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE chat_settings SET reminders_enabled = %s WHERE chat_id = %s",
                        (new_value, chat_id),
                    )
                    cur.execute(
                        "SELECT chat_id, reminders_enabled FROM chat_settings WHERE chat_id = %s",
                        (chat_id,),
                    )
                    row = cur.fetchone()
                    return self._row_to_dict(row) or {"chat_id": chat_id, "reminders_enabled": new_value}

        with self._connect() as conn:
            conn.execute(
                "UPDATE chat_settings SET reminders_enabled = ? WHERE chat_id = ?",
                (1 if new_value else 0, chat_id),
            )
            row = conn.execute(
                "SELECT * FROM chat_settings WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            return self._row_to_dict(row) or {"chat_id": chat_id, "reminders_enabled": 1 if new_value else 0}

    def get_active_trip(self, chat_id: int) -> dict[str, Any] | None:
        query = """
            SELECT *
            FROM trips
            WHERE chat_id = {placeholder} AND status = 'active'
            ORDER BY id DESC
            LIMIT 1
        """
        params = (chat_id,)

        if self.is_postgres:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(query.format(placeholder="%s"), params)
                    return self._row_to_dict(cur.fetchone())

        with self._connect() as conn:
            row = conn.execute(query.format(placeholder="?"), params).fetchone()
            return self._row_to_dict(row)

    def get_trip_by_id(self, trip_id: int) -> dict[str, Any] | None:
        if self.is_postgres:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM trips WHERE id = %s", (trip_id,))
                    return self._row_to_dict(cur.fetchone())

        with self._connect() as conn:
            row = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
            return self._row_to_dict(row)

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
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM participants WHERE trip_id = %s ORDER BY LOWER(full_name) ASC",
                        (trip_id,),
                    )
                    return self._rows_to_dicts(cur.fetchall())

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM participants WHERE trip_id = ? ORDER BY full_name COLLATE NOCASE ASC",
                (trip_id,),
            ).fetchall()
            return self._rows_to_dicts(rows)

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
        if self.is_postgres:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM date_options WHERE id = %s", (option_id,))
                    return self._row_to_dict(cur.fetchone())

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM date_options WHERE id = ?",
                (option_id,),
            ).fetchone()
            return self._row_to_dict(row)

    def list_date_options(self, trip_id: int) -> list[dict[str, Any]]:
        query = """
            SELECT
                d.id,
                d.label,
                COUNT(v.id) AS votes
            FROM date_options d
            LEFT JOIN date_votes v ON v.option_id = d.id
            WHERE d.trip_id = {placeholder}
            GROUP BY d.id, d.label
            ORDER BY votes DESC, d.id ASC
        """

        if self.is_postgres:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(query.format(placeholder="%s"), (trip_id,))
                    return self._rows_to_dicts(cur.fetchall())

        with self._connect() as conn:
            rows = conn.execute(query.format(placeholder="?"), (trip_id,)).fetchall()
            return self._rows_to_dicts(rows)

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
