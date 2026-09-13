"""
database/queries.py

Every SQL statement in the application lives here. Services call these
typed functions instead of writing raw SQL, and `app.py` / `pages/` never
touch SQLite directly - this keeps the "clean architecture" boundary easy
to enforce and easy to audit for injection risk (everything is parameterized).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.database import get_connection
from utils.security import hash_password


def _row_to_dict(row) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

def insert_item(
    type_: str,
    title: str,
    description: str,
    category: str,
    brand: str,
    color: str,
    location: str,
    event_date: str,
    event_time: str,
    image_path: Optional[str],
    user_id: Optional[int] = None,
) -> int:
    """Insert a lost/found report and return its new item id."""
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO items
               (user_id, type, title, description, category, brand, primary_color,
                location, event_date, event_time, image_path)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (user_id or 1, type_, title, description, category, brand, color, location,
             event_date, event_time, image_path),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_item(item_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return _row_to_dict(row)


def get_items_by_type(
    type_: str,
    status: Optional[str] = None,
    limit: Optional[int] = None,
    user_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    query = "SELECT * FROM items WHERE type=?"
    params: List[Any] = [type_]
    if status:
        query += " AND status=?"
        params.append(status)
    if user_id is not None:
        query += " AND user_id=?"
        params.append(user_id)
    query += " ORDER BY id DESC"
    if limit:
        query += " LIMIT ?"
        params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_recent_items(limit: int = 20) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM items ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def seed_default_users() -> None:
    """Create application users for testing and role-based access control."""
    default_users = [
        ("admin", "Admin@123", "System Administrator", "Administrator", "admin@findback.ai", "+1-555-0101"),
        ("staff", "Staff@123", "Operations Staff", "Staff", "staff@findback.ai", "+1-555-0102"),
        ("user1", "User@123", "Regular User 1", "Regular User", "user1@findback.ai", "+1-555-0103"),
        ("user2", "User@123", "Regular User 2", "Regular User", "user2@findback.ai", "+1-555-0104"),
        ("user3", "User@123", "Regular User 3", "Regular User", "user3@findback.ai", "+1-555-0105"),
    ]
    with get_connection() as conn:
        for username, password, full_name, role, email, phone_number in default_users:
            existing = conn.execute(
                "SELECT id FROM users WHERE username=?", (username,)
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO users (username, password_hash, full_name, email, phone_number, role) VALUES (?,?,?,?,?,?)",
                    (username, hash_password(password), full_name, email, phone_number, role),
                )
            else:
                conn.execute(
                    "UPDATE users SET full_name=?, email=?, phone_number=?, role=? WHERE username=?",
                    (full_name, email, phone_number, role, username),
                )
        conn.commit()


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username=? AND is_active=1",
            (username,),
        ).fetchone()
        return _row_to_dict(row)


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id=? AND is_active=1",
            (user_id,),
        ).fetchone()
        return _row_to_dict(row)


def get_all_users() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]


def update_item_status(item_id: int, status: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE items SET status=? WHERE id=?", (status, item_id))
        conn.commit()


def get_stats() -> Dict[str, int]:
    """Home-page dashboard counters."""
    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        lost = conn.execute("SELECT COUNT(*) FROM items WHERE type='lost'").fetchone()[0]
        found = conn.execute("SELECT COUNT(*) FROM items WHERE type='found'").fetchone()[0]
        matches = conn.execute("SELECT COUNT(*) FROM claims WHERE status='potential'").fetchone()[0]
    return {"total": total, "lost": lost, "found": found, "matches": matches}


# ---------------------------------------------------------------------------
# Item AI features
# ---------------------------------------------------------------------------

def insert_item_features(
    item_id: int,
    ai_category: Optional[str],
    ai_brand_json: str,
    ai_colors_json: str,
    ai_features_json: str,
    ai_confidence_json: str,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO item_features
               (item_id, ai_category, ai_brand, ai_colors, ai_features, ai_confidence)
               VALUES (?,?,?,?,?,?)""",
            (item_id, ai_category, ai_brand_json, ai_colors_json,
             ai_features_json, ai_confidence_json),
        )
        conn.commit()


def get_item_features(item_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM item_features WHERE item_id=?", (item_id,)
        ).fetchone()
        return _row_to_dict(row)


# ---------------------------------------------------------------------------
# Matches
# ---------------------------------------------------------------------------

def insert_match(
    lost_item_id: int,
    found_item_id: int,
    scores: Dict[str, float],
    final_score: float,
    explanation: Optional[str] = None,
) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO matches
               (lost_item_id, found_item_id, text_score, image_score, category_score,
                feature_score, location_score, time_score, brand_color_score,
                final_score, explanation)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                lost_item_id, found_item_id,
                scores.get("text", 0.0), scores.get("image", 0.0),
                scores.get("category", 0.0), scores.get("features", 0.0),
                scores.get("location", 0.0), scores.get("time", 0.0),
                scores.get("brand_color", 0.0), final_score, explanation,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_match_status(match_id: int, status: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE matches SET status=? WHERE id=?", (status, match_id))
        conn.commit()


def create_claim(
    lost_item_id: int,
    found_item_id: int,
    requested_by_user_id: int,
    notes: Optional[str] = None,
) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO claims
               (lost_item_id, found_item_id, requested_by_user_id, status, notes)
               VALUES (?, ?, ?, 'potential', ?)""",
            (lost_item_id, found_item_id, requested_by_user_id, notes),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_claim(claim_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
        return _row_to_dict(row)


def get_claim_by_pair(lost_item_id: int, found_item_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM claims WHERE lost_item_id=? AND found_item_id=? ORDER BY id DESC LIMIT 1",
            (lost_item_id, found_item_id),
        ).fetchone()
        return _row_to_dict(row)


def list_claims(
    status: Optional[str] = None,
    user_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    query = "SELECT * FROM claims"
    params: List[Any] = []
    if status:
        query += " WHERE status=?"
        params.append(status)
    if user_id is not None:
        if status:
            query += " AND requested_by_user_id=?"
        else:
            query += " WHERE requested_by_user_id=?"
        params.append(user_id)
    query += " ORDER BY created_at DESC"
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def update_claim(
    claim_id: int,
    *,
    status: Optional[str] = None,
    verification_status: Optional[str] = None,
    handover_status: Optional[str] = None,
    notes: Optional[str] = None,
    reviewed_by_user_id: Optional[int] = None,
) -> None:
    fields = ["updated_at = CURRENT_TIMESTAMP"]
    params: List[Any] = []
    if status is not None:
        fields.append("status = ?")
        params.append(status)
    if verification_status is not None:
        fields.append("verification_status = ?")
        params.append(verification_status)
    if handover_status is not None:
        fields.append("handover_status = ?")
        params.append(handover_status)
    if notes is not None:
        fields.append("notes = ?")
        params.append(notes)
    if reviewed_by_user_id is not None:
        fields.append("reviewed_by_user_id = ?")
        params.append(reviewed_by_user_id)
    query = f"UPDATE claims SET {', '.join(fields)} WHERE id=?"
    params.append(claim_id)
    with get_connection() as conn:
        conn.execute(query, tuple(params))
        conn.commit()


def resolve_claim(claim_id: int, *, verification_status: str, handover_status: str, reviewed_by_user_id: int, notes: Optional[str] = None) -> None:
    update_claim(
        claim_id,
        status="resolved",
        verification_status=verification_status,
        handover_status=handover_status,
        notes=notes,
        reviewed_by_user_id=reviewed_by_user_id,
    )
    with get_connection() as conn:
        conn.execute(
            "UPDATE claims SET resolved_at = CURRENT_TIMESTAMP WHERE id=?",
            (claim_id,),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def create_notification(
    user_id: int,
    title: str,
    message: str,
    notif_type: str = "match_confirmed",
    item_id: Optional[int] = None,
    claim_id: Optional[int] = None,
) -> int:
    """Insert a notification for a single user and return its new id."""
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO notifications (user_id, item_id, claim_id, type, title, message)
               VALUES (?,?,?,?,?,?)""",
            (user_id, item_id, claim_id, notif_type, title, message),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_notifications(user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_unread_notification_count(user_id: int) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0",
            (user_id,),
        ).fetchone()
        return int(row[0]) if row else 0


def mark_notification_read(notification_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE notifications SET is_read=1 WHERE id=?", (notification_id,))
        conn.commit()


def mark_all_notifications_read(user_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE notifications SET is_read=1 WHERE user_id=? AND is_read=0", (user_id,))
        conn.commit()


# ---------------------------------------------------------------------------
# AI usage / observability
# ---------------------------------------------------------------------------

def log_ai_usage(
    provider: str,
    model: str,
    request_type: str,
    success: bool,
    error_type: Optional[str] = None,
    latency_ms: int = 0,
) -> None:
    """Best-effort usage logging. Never raises - observability must not
    be able to break the main request path."""
    try:
        with get_connection() as conn:
            conn.execute(
                """INSERT INTO ai_usage(provider, model, request_type, success, error_type, latency_ms)
                   VALUES (?,?,?,?,?,?)""",
                (provider, model, request_type, 1 if success else 0, error_type, latency_ms),
            )
            conn.commit()
    except Exception:
        pass
def reset_application_data() -> bool:
    """Reset all lost/found transaction data while preserving user accounts."""
    with get_connection() as conn:
        # Delete dependent/transactional data first
        conn.execute("DELETE FROM notifications")
        conn.execute("DELETE FROM claims")
        conn.execute("DELETE FROM matches")
        conn.execute("DELETE FROM item_features")
        conn.execute("DELETE FROM ai_usage")

        # Delete all lost/found items
        conn.execute("DELETE FROM items")

        # Reset SQLite ID counters for these tables
        conn.execute("""
            DELETE FROM sqlite_sequence
            WHERE name IN (
                'items',
                'item_features',
                'matches',
                'claims',
                'notifications',
                'ai_usage'
            )
        """)

        conn.commit()

    return True
