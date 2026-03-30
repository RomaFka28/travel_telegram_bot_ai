from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


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
    "created_by": "INTEGER",
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
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def init_db(self) -> None:
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
            existing_columns = self._table_columns(conn, "trips")
            for column_name, definition in TRIP_COLUMNS.items():
                if column_name not in existing_columns:
                    conn.execute(f"ALTER TABLE trips ADD COLUMN {column_name} {definition}")

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {row[1] for row in rows}

    def get_or_create_settings(self, chat_id: int) -> sqlite3.Row:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO chat_settings(chat_id) VALUES (?)",
                (chat_id,),
            )
            return conn.execute(
                "SELECT * FROM chat_settings WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()

    def toggle_reminders(self, chat_id: int) -> sqlite3.Row:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO chat_settings(chat_id) VALUES (?)",
                (chat_id,),
            )
            current = conn.execute(
                "SELECT * FROM chat_settings WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            new_value = 0 if int(current["reminders_enabled"]) else 1
            conn.execute(
                "UPDATE chat_settings SET reminders_enabled = ? WHERE chat_id = ?",
                (new_value, chat_id),
            )
            return conn.execute(
                "SELECT * FROM chat_settings WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()

    def get_active_trip(self, chat_id: int) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT *
                FROM trips
                WHERE chat_id = ? AND status = 'active'
                ORDER BY id DESC
                LIMIT 1
                """,
                (chat_id,),
            ).fetchone()

    def get_trip_by_id(self, trip_id: int) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM trips WHERE id = ?",
                (trip_id,),
            ).fetchone()

    def create_trip(self, chat_id: int, created_by: int | None, payload: dict[str, Any]) -> int:
        values = {key: value for key, value in payload.items() if key in EDITABLE_TRIP_FIELDS and key != "status"}
        values.setdefault("status", "active")

        with self._connect() as conn:
            conn.execute(
                "UPDATE trips SET status = 'archived', updated_at = CURRENT_TIMESTAMP WHERE chat_id = ? AND status = 'active'",
                (chat_id,),
            )
            fields = ["chat_id", "created_by", *values.keys()]
            placeholders = ", ".join(["?"] * len(fields))
            sql = f"INSERT INTO trips({', '.join(fields)}) VALUES ({placeholders})"
            params = [chat_id, created_by, *values.values()]
            cursor = conn.execute(sql, params)
            return int(cursor.lastrowid)

    def archive_active_trip(self, chat_id: int) -> bool:
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
        assignments = ", ".join(f"{field} = ?" for field in safe_updates)
        params = [self._coerce_value(value) for value in safe_updates.values()]
        params.append(trip_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE trips SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                params,
            )

    @staticmethod
    def _coerce_value(value: Any) -> Any:
        if isinstance(value, bool):
            return int(value)
        return value

    def upsert_participant(
        self,
        trip_id: int,
        user_id: int,
        username: str | None,
        full_name: str,
        status: str,
    ) -> None:
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
                (trip_id, user_id, username or "", full_name, status),
            )

    def list_participants(self, trip_id: int) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM participants WHERE trip_id = ? ORDER BY full_name COLLATE NOCASE ASC",
                (trip_id,),
            ).fetchall()

    def add_date_option(self, trip_id: int, label: str, created_by: int) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO date_options(trip_id, label, created_by) VALUES (?, ?, ?)",
                (trip_id, label.strip(), created_by),
            )
            return int(cursor.lastrowid)

    def get_date_option(self, option_id: int) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM date_options WHERE id = ?",
                (option_id,),
            ).fetchone()

    def list_date_options(self, trip_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
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
            ).fetchall()
            return [dict(row) for row in rows]

    def toggle_date_vote(self, option_id: int, user_id: int) -> tuple[bool, int]:
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
