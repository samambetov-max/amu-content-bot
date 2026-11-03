"""SQLite memory of the client bot: chats, messages, learned playbook.

Client conversations are private: the database lives outside git (CLIENT_BOT_DATA, default ./clientdata).
"""
import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS connections (
    id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, can_reply INTEGER NOT NULL, enabled INTEGER NOT NULL,
    updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS chats (
    chat_id INTEGER PRIMARY KEY, connection_id TEXT, name TEXT, username TEXT,
    notes TEXT NOT NULL DEFAULT '', paused_until REAL NOT NULL DEFAULT 0, due REAL,
    last_client REAL NOT NULL DEFAULT 0, last_bot REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, tg_id INTEGER, role TEXT NOT NULL,
    text TEXT NOT NULL, ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id, id);
CREATE TABLE IF NOT EXISTS playbook (version INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, reason TEXT, ts REAL NOT NULL);
"""

# Roles of stored messages
CLIENT, OWNER, BOT = "client", "owner", "bot"


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    def _all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def _one(self, sql: str, args: tuple = ()) -> dict | None:
        rows = self._all(sql, args)
        return rows[0] if rows else None

    # --- key/value -------------------------------------------------------
    def get(self, key: str, default=None):
        row = self._one("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set(self, key: str, value) -> None:
        self._exec("INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, json.dumps(value, ensure_ascii=False)))

    # --- business connections --------------------------------------------
    def save_connection(self, conn_id: str, owner_id: int, can_reply: bool, enabled: bool) -> None:
        self._exec("INSERT INTO connections VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET owner_id=excluded.owner_id,"
                   " can_reply=excluded.can_reply, enabled=excluded.enabled, updated=excluded.updated",
                   (conn_id, owner_id, int(can_reply), int(enabled), time.time()))

    def connection(self, conn_id: str) -> dict | None:
        return self._one("SELECT * FROM connections WHERE id=?", (conn_id,))

    def connections(self) -> list[dict]:
        return self._all("SELECT * FROM connections ORDER BY updated DESC")

    # --- chats -------------------------------------------------------------
    def touch_chat(self, chat_id: int, connection_id: str, name: str, username: str) -> None:
        self._exec("INSERT INTO chats(chat_id, connection_id, name, username) VALUES(?,?,?,?) ON CONFLICT(chat_id) DO UPDATE"
                   " SET connection_id=excluded.connection_id, name=excluded.name, username=excluded.username",
                   (chat_id, connection_id, name, username))

    def chat(self, chat_id: int) -> dict | None:
        return self._one("SELECT * FROM chats WHERE chat_id=?", (chat_id,))

    def update_chat(self, chat_id: int, **fields) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE chats SET {cols} WHERE chat_id=?", (*fields.values(), chat_id))

    def resume_all(self) -> None:
        self._exec("UPDATE chats SET paused_until=0")

    def due_chats(self, now: float) -> list[dict]:
        return self._all("SELECT * FROM chats WHERE due IS NOT NULL AND due<=? ORDER BY due", (now,))

    def next_due(self) -> float | None:
        row = self._one("SELECT MIN(due) AS d FROM chats WHERE due IS NOT NULL")
        return row["d"] if row else None

    def chat_stats(self, now: float) -> dict:
        row = self._one("SELECT COUNT(*) AS chats, SUM(paused_until>?) AS paused FROM chats", (now,))
        return {"chats": row["chats"] or 0, "paused": row["paused"] or 0}

    # --- messages ----------------------------------------------------------
    def add_message(self, chat_id: int, role: str, text: str, tg_id: int | None = None, ts: float | None = None) -> int:
        cur = self._exec("INSERT INTO messages(chat_id, tg_id, role, text, ts) VALUES(?,?,?,?,?)",
                         (chat_id, tg_id, role, text, ts or time.time()))
        return cur.lastrowid

    def edit_message(self, chat_id: int, tg_id: int, text: str) -> None:
        self._exec("UPDATE messages SET text=? WHERE chat_id=? AND tg_id=?", (text, chat_id, tg_id))

    def has_message(self, chat_id: int, tg_id: int) -> bool:
        return self._one("SELECT 1 FROM messages WHERE chat_id=? AND tg_id=?", (chat_id, tg_id)) is not None

    def history(self, chat_id: int, limit: int) -> list[dict]:
        rows = self._all("SELECT * FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?", (chat_id, limit))
        return rows[::-1]

    def last_message_id(self, chat_id: int) -> int:
        row = self._one("SELECT MAX(id) AS m FROM messages WHERE chat_id=?", (chat_id,))
        return (row and row["m"]) or 0

    def messages_since(self, msg_id: int, roles: tuple[str, ...]) -> list[dict]:
        marks = ",".join("?" * len(roles))
        return self._all(f"SELECT * FROM messages WHERE id>? AND chat_id>0 AND role IN ({marks}) ORDER BY id", (msg_id, *roles))

    def owner_examples(self, limit: int = 400) -> list[dict]:
        """Owner's own replies with the client messages right before them: how the owner really answers."""
        replies = self._all("SELECT * FROM messages WHERE role=? AND chat_id>0 ORDER BY id DESC LIMIT ?", (OWNER, limit))
        out = []
        for r in replies:
            before = self._all("SELECT role, text FROM messages WHERE chat_id=? AND id<? ORDER BY id DESC LIMIT 4",
                               (r["chat_id"], r["id"]))
            client = [b["text"] for b in before[::-1] if b["role"] == CLIENT]
            if client:
                out.append({"client": "\n".join(client[-3:]), "owner": r["text"], "id": r["id"]})
        return out

    def count(self, role: str) -> int:
        return self._one("SELECT COUNT(*) AS n FROM messages WHERE role=?", (role,))["n"]

    def max_id(self) -> int:
        return self._one("SELECT COALESCE(MAX(id), 0) AS m FROM messages")["m"]

    # --- learned playbook -------------------------------------------------
    def playbook(self) -> str:
        row = self._one("SELECT text FROM playbook ORDER BY version DESC LIMIT 1")
        return row["text"] if row else ""

    def playbook_version(self) -> int:
        row = self._one("SELECT MAX(version) AS v FROM playbook")
        return (row and row["v"]) or 0

    def save_playbook(self, text: str, reason: str) -> None:
        self._exec("INSERT INTO playbook(text, reason, ts) VALUES(?,?,?)", (text, reason, time.time()))
