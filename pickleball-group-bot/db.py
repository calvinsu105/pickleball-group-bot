"""資料儲存：有設定 DATABASE_URL 就用 PostgreSQL，否則用本機 SQLite（僅供測試）。"""
import os
import random
import sqlite3
import time

PG_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS polls (
        id SERIAL PRIMARY KEY,
        chat_id TEXT NOT NULL,
        creator_id TEXT,
        creator_name TEXT,
        date TEXT, time TEXT, venue TEXT, price TEXT,
        capacity INTEGER,
        created_at DOUBLE PRECISION NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS votes (
        poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL,
        display_name TEXT,
        choice TEXT NOT NULL,
        guests INTEGER NOT NULL DEFAULT 0,
        voted_at DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (poll_id, user_id))""",
    # 簽到紀錄：guests 是實際到場的同伴人數
    """CREATE TABLE IF NOT EXISTS checkins (
        poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL,
        display_name TEXT,
        guests INTEGER NOT NULL DEFAULT 0,
        checked_at DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (poll_id, user_id))""",
    # 已處理過的 LINE 事件編號，用來忽略 LINE 重送的重複事件
    """CREATE TABLE IF NOT EXISTS processed_events (
        event_id TEXT PRIMARY KEY,
        created_at DOUBLE PRECISION NOT NULL)""",
]
SQLITE_SCHEMA = [
    s.replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
     .replace("DOUBLE PRECISION", "REAL")
    for s in PG_SCHEMA
]

# 按參加／不參加：同樣選擇保留原本的順位與攜伴；換選擇才重新排隊，攜伴歸零
UPSERT_VOTE = """
INSERT INTO votes (poll_id, user_id, display_name, choice, guests, voted_at)
VALUES (?, ?, ?, ?, 0, ?)
ON CONFLICT (poll_id, user_id) DO UPDATE SET
    display_name = excluded.display_name,
    choice = excluded.choice,
    guests = CASE WHEN votes.choice = excluded.choice THEN votes.guests ELSE 0 END,
    voted_at = CASE WHEN votes.choice = excluded.choice
                    THEN votes.voted_at ELSE excluded.voted_at END
"""

# 按攜伴按鈕：直接寫入計算好的狀態
SET_VOTE = """
INSERT INTO votes (poll_id, user_id, display_name, choice, guests, voted_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT (poll_id, user_id) DO UPDATE SET
    display_name = excluded.display_name,
    choice = excluded.choice,
    guests = excluded.guests,
    voted_at = excluded.voted_at
"""


class Database:
    def __init__(self, url=None):
        self.url = url if url is not None else os.environ.get("DATABASE_URL", "")
        self.is_pg = self.url.startswith(("postgres://", "postgresql://"))
        if not self.is_pg:
            self.sqlite_path = self.url or "polls.db"
        for stmt in PG_SCHEMA if self.is_pg else SQLITE_SCHEMA:
            self._execute(stmt)
        self._migrate()

    def _migrate(self):
        """舊版資料庫沒有 guests 欄位時自動補上（原有資料不受影響）。"""
        if self.is_pg:
            self._execute("ALTER TABLE votes ADD COLUMN IF NOT EXISTS guests INTEGER NOT NULL DEFAULT 0")
        else:
            cols = [r["name"] for r in self._execute("PRAGMA table_info(votes)", fetch="all")]
            if "guests" not in cols:
                self._execute("ALTER TABLE votes ADD COLUMN guests INTEGER NOT NULL DEFAULT 0")

    # ---- 底層 ----
    def _connect(self):
        if self.is_pg:
            import psycopg
            from psycopg.rows import dict_row
            return psycopg.connect(self.url, autocommit=True, row_factory=dict_row)
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _execute(self, sql, params=(), fetch=None):
        if self.is_pg:
            sql = sql.replace("?", "%s")
        conn = self._connect()
        try:
            cur = conn.execute(sql, params)
            rows = cur.fetchall() if fetch else None
            if not self.is_pg:
                conn.commit()
        finally:
            conn.close()
        if fetch == "one":
            return dict(rows[0]) if rows else None
        if fetch == "all":
            return [dict(r) for r in rows]
        return None

    # ---- 重複事件 ----
    def claim_event(self, event_id):
        """登記這個事件編號。第一次看到回傳 True；已處理過（LINE 重送）回傳 False。"""
        row = self._execute(
            "INSERT INTO processed_events (event_id, created_at) VALUES (?, ?)"
            " ON CONFLICT (event_id) DO NOTHING RETURNING event_id",
            (event_id, time.time()), fetch="one",
        )
        if random.random() < 0.02:  # 偶爾清掉 3 天前的紀錄，避免資料表一直變大
            self._execute("DELETE FROM processed_events WHERE created_at < ?", (time.time() - 3 * 86400,))
        return row is not None

    def release_event(self, event_id):
        """處理失敗時取消登記，讓 LINE 重送時可以再處理一次。"""
        self._execute("DELETE FROM processed_events WHERE event_id = ?", (event_id,))

    # ---- 揪團 ----
    def create_poll(self, chat_id, creator_id, creator_name, f):
        row = self._execute(
            "INSERT INTO polls (chat_id, creator_id, creator_name, date, time, venue, price,"
            " capacity, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (chat_id, creator_id, creator_name, f["date"], f["time"], f["venue"],
             f["price"], f["capacity"], time.time()),
            fetch="one",
        )
        return row["id"]

    def get_poll(self, poll_id):
        return self._execute("SELECT * FROM polls WHERE id = ?", (poll_id,), fetch="one")

    def list_polls(self, chat_id):
        """這個群組的所有揪團（最新的在前），每筆附上 votes（依投票先後排序）。"""
        polls = self._execute(
            "SELECT * FROM polls WHERE chat_id = ? ORDER BY created_at DESC", (chat_id,), fetch="all")
        votes = self._execute(
            "SELECT v.* FROM votes v JOIN polls p ON p.id = v.poll_id"
            " WHERE p.chat_id = ? ORDER BY v.voted_at, v.user_id", (chat_id,), fetch="all")
        by_poll = {}
        for v in votes:
            by_poll.setdefault(v["poll_id"], []).append(v)
        for p in polls:
            p["votes"] = by_poll.get(p["id"], [])
        return polls

    def delete_poll(self, poll_id):
        """刪除揪團與它的所有投票、簽到（同一個連線內完成）。"""
        conn = self._connect()
        q = "%s" if self.is_pg else "?"
        try:
            conn.execute(f"DELETE FROM checkins WHERE poll_id = {q}", (poll_id,))
            conn.execute(f"DELETE FROM votes WHERE poll_id = {q}", (poll_id,))
            conn.execute(f"DELETE FROM polls WHERE id = {q}", (poll_id,))
            if not self.is_pg:
                conn.commit()
        finally:
            conn.close()

    # ---- 投票 ----
    def vote(self, poll_id, user_id, display_name, choice):
        self._execute(UPSERT_VOTE, (poll_id, user_id, display_name, choice, time.time()))

    def get_vote(self, poll_id, user_id):
        return self._execute(
            "SELECT * FROM votes WHERE poll_id = ? AND user_id = ?", (poll_id, user_id), fetch="one")

    def set_vote(self, poll_id, user_id, display_name, choice, guests, keep_order):
        """寫入攜伴變更。keep_order=True 時保留原本的報名順位。"""
        current = self.get_vote(poll_id, user_id)
        voted_at = current["voted_at"] if keep_order and current else time.time()
        self._execute(SET_VOTE, (poll_id, user_id, display_name, choice, guests, voted_at))

    # ---- 簽到 ----
    def checkin(self, poll_id, user_id, display_name, guests):
        """簽到或更新實到同伴人數（重複簽到保留第一次的簽到時間）。"""
        self._execute(
            "INSERT INTO checkins (poll_id, user_id, display_name, guests, checked_at)"
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT (poll_id, user_id) DO UPDATE SET"
            " display_name = excluded.display_name, guests = excluded.guests",
            (poll_id, user_id, display_name, guests, time.time()),
        )

    def cancel_checkin(self, poll_id, user_id):
        self._execute("DELETE FROM checkins WHERE poll_id = ? AND user_id = ?", (poll_id, user_id))

    def get_checkin(self, poll_id, user_id):
        return self._execute(
            "SELECT * FROM checkins WHERE poll_id = ? AND user_id = ?", (poll_id, user_id), fetch="one")

    def get_checkins(self, poll_id):
        return self._execute(
            "SELECT * FROM checkins WHERE poll_id = ? ORDER BY checked_at, user_id", (poll_id,), fetch="all")

    def get_votes(self, poll_id):
        return self._execute(
            "SELECT * FROM votes WHERE poll_id = ? ORDER BY voted_at, user_id",
            (poll_id,), fetch="all",
        )
