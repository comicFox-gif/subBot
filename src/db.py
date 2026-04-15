"""
Subscriber database — SQLite backed
-------------------------------------
Tables:
  subscribers(chat_id, username, first_name, subscribed_at, active,
              subscription_expiry)
  pending_payments(id, chat_id, username, method, requested_at, status)

A subscriber receives signals when:
  • FREE_ACCESS=true  → active=1  (no payment needed)
  • FREE_ACCESS=false → active=1  AND subscription_expiry > utcnow
"""

import sqlite3
import os
from datetime import datetime, timezone, timedelta

DB_PATH    = os.getenv("DB_PATH", "subscribers.db")
FREE_ACCESS = os.getenv("FREE_ACCESS", "false").lower() == "true"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subscribers (
                chat_id              INTEGER PRIMARY KEY,
                username             TEXT    DEFAULT '',
                first_name           TEXT    DEFAULT '',
                subscribed_at        TEXT    NOT NULL,
                active               INTEGER NOT NULL DEFAULT 1,
                subscription_expiry  TEXT    DEFAULT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_payments (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id       INTEGER NOT NULL,
                username      TEXT    DEFAULT '',
                method        TEXT    NOT NULL,
                requested_at  TEXT    NOT NULL,
                status        TEXT    NOT NULL DEFAULT 'pending'
            )
        """)
        # Migrate existing DBs that don't have the columns yet
        for col_sql in [
            "ALTER TABLE subscribers ADD COLUMN subscription_expiry TEXT DEFAULT NULL",
            "ALTER TABLE subscribers ADD COLUMN has_join_request INTEGER DEFAULT 0",
            "ALTER TABLE subscribers ADD COLUMN trial_expiry TEXT DEFAULT NULL",
            "ALTER TABLE subscribers ADD COLUMN trial_used INTEGER DEFAULT 0",
            "ALTER TABLE subscribers ADD COLUMN trial_kicked INTEGER DEFAULT 0",
        ]:
            try:
                conn.execute(col_sql)
            except Exception:
                pass
        conn.commit()


# ------------------------------------------------------------------
# Subscribe / unsubscribe
# ------------------------------------------------------------------

def subscribe(chat_id: int, username: str, first_name: str):
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO subscribers (chat_id, username, first_name, subscribed_at, active)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(chat_id) DO UPDATE SET
                username   = excluded.username,
                first_name = excluded.first_name,
                active     = 1
            """,
            (chat_id, username or "", first_name or "", now),
        )
        conn.commit()


def unsubscribe(chat_id: int):
    with _conn() as conn:
        conn.execute(
            "UPDATE subscribers SET active = 0 WHERE chat_id = ?",
            (chat_id,),
        )
        conn.commit()


# ------------------------------------------------------------------
# Payment / subscription
# ------------------------------------------------------------------

def set_paid(chat_id: int, days: int = 30):
    """Extend (or set) subscription by *days* days from now."""
    now = datetime.now(timezone.utc)

    with _conn() as conn:
        row = conn.execute(
            "SELECT subscription_expiry FROM subscribers WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()

        if row and row["subscription_expiry"]:
            try:
                current_expiry = datetime.fromisoformat(row["subscription_expiry"])
                # If still in the future, extend from the current expiry
                if current_expiry > now:
                    new_expiry = current_expiry + timedelta(days=days)
                else:
                    new_expiry = now + timedelta(days=days)
            except ValueError:
                new_expiry = now + timedelta(days=days)
        else:
            new_expiry = now + timedelta(days=days)

        conn.execute(
            "UPDATE subscribers SET active = 1, subscription_expiry = ? WHERE chat_id = ?",
            (new_expiry.isoformat(), chat_id),
        )
        conn.commit()
        return new_expiry


def get_expiry(chat_id: int) -> datetime | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT subscription_expiry FROM subscribers WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        if row and row["subscription_expiry"]:
            try:
                return datetime.fromisoformat(row["subscription_expiry"])
            except ValueError:
                return None
        return None


def is_paid(chat_id: int) -> bool:
    expiry = get_expiry(chat_id)
    if expiry is None:
        return False
    return expiry > datetime.now(timezone.utc)


# ------------------------------------------------------------------
# Status checks
# ------------------------------------------------------------------

def is_subscribed(chat_id: int) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT active FROM subscribers WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return bool(row and row["active"])


def get_broadcast_targets() -> list[int]:
    """
    Returns chat_ids to broadcast to.
    FREE_ACCESS=true  → all active subscribers
    FREE_ACCESS=false → only active subscribers with a valid paid subscription
    """
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        if FREE_ACCESS:
            rows = conn.execute(
                "SELECT chat_id FROM subscribers WHERE active = 1"
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT chat_id FROM subscribers
                WHERE active = 1
                  AND subscription_expiry IS NOT NULL
                  AND subscription_expiry > ?
                """,
                (now,),
            ).fetchall()
        return [r["chat_id"] for r in rows]


# ------------------------------------------------------------------
# Counts
# ------------------------------------------------------------------

def count_active() -> int:
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM subscribers WHERE active = 1"
        ).fetchone()
        return row["cnt"]


def count_paid() -> int:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt FROM subscribers
            WHERE active = 1
              AND subscription_expiry IS NOT NULL
              AND subscription_expiry > ?
            """,
            (now,),
        ).fetchone()
        return row["cnt"]


def count_total() -> int:
    with _conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM subscribers").fetchone()
        return row["cnt"]


# ------------------------------------------------------------------
# Pending payments
# ------------------------------------------------------------------

def add_pending(chat_id: int, username: str, method: str):
    """Insert or replace a pending payment request for chat_id."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        # Remove any existing pending entry for this user first
        conn.execute(
            "DELETE FROM pending_payments WHERE chat_id = ? AND status = 'pending'",
            (chat_id,),
        )
        conn.execute(
            "INSERT INTO pending_payments (chat_id, username, method, requested_at, status) "
            "VALUES (?, ?, ?, ?, 'pending')",
            (chat_id, username or "", method, now),
        )
        conn.commit()


def get_pending_all() -> list[dict]:
    """Return all pending (unresolved) payment requests."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM pending_payments WHERE status = 'pending' ORDER BY requested_at"
        ).fetchall()
        return [dict(r) for r in rows]


def get_pending_for(chat_id: int) -> dict | None:
    """Return the latest pending request for a specific chat_id."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM pending_payments WHERE chat_id = ? AND status = 'pending' "
            "ORDER BY requested_at DESC LIMIT 1",
            (chat_id,),
        ).fetchone()
        return dict(row) if row else None


def resolve_pending(chat_id: int, status: str):
    """Mark pending payments for chat_id as approved/denied."""
    with _conn() as conn:
        conn.execute(
            "UPDATE pending_payments SET status = ? WHERE chat_id = ? AND status = 'pending'",
            (status, chat_id),
        )
        conn.commit()


# ------------------------------------------------------------------
# Channel join requests
# ------------------------------------------------------------------

def set_join_request(chat_id: int, value: bool):
    """Record that this user has a pending channel join request."""
    with _conn() as conn:
        conn.execute(
            "UPDATE subscribers SET has_join_request = ? WHERE chat_id = ?",
            (1 if value else 0, chat_id),
        )
        conn.commit()


def has_join_request(chat_id: int) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT has_join_request FROM subscribers WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        return bool(row and row["has_join_request"])


# ------------------------------------------------------------------
# Free trial
# ------------------------------------------------------------------

def set_trial(chat_id: int, days: int = 7):
    """Mark the trial as used and set its expiry."""
    expiry = datetime.now(timezone.utc) + timedelta(days=days)
    with _conn() as conn:
        conn.execute(
            """
            UPDATE subscribers
            SET trial_expiry = ?, trial_used = 1, trial_kicked = 0
            WHERE chat_id = ?
            """,
            (expiry.isoformat(), chat_id),
        )
        conn.commit()
    return expiry


def has_used_trial(chat_id: int) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT trial_used FROM subscribers WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return bool(row and row["trial_used"])


def get_trial_expiry(chat_id: int) -> datetime | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT trial_expiry FROM subscribers WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        if row and row["trial_expiry"]:
            try:
                return datetime.fromisoformat(row["trial_expiry"])
            except ValueError:
                return None
        return None


def is_trial_active(chat_id: int) -> bool:
    expiry = get_trial_expiry(chat_id)
    if expiry is None:
        return False
    return expiry > datetime.now(timezone.utc)


def get_expired_trial_users() -> list[int]:
    """
    Users whose free trial has expired, haven't paid, and haven't been kicked yet.
    """
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT chat_id FROM subscribers
            WHERE trial_expiry IS NOT NULL
              AND trial_expiry < ?
              AND trial_kicked = 0
              AND (
                subscription_expiry IS NULL
                OR subscription_expiry < ?
              )
            """,
            (now, now),
        ).fetchall()
        return [r["chat_id"] for r in rows]


def set_trial_kicked(chat_id: int):
    with _conn() as conn:
        conn.execute(
            "UPDATE subscribers SET trial_kicked = 1 WHERE chat_id = ?", (chat_id,)
        )
        conn.commit()
