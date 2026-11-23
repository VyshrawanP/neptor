import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
import app.config as config


class DatabaseError(Exception):
    """Base exception for database errors."""
    pass


class DraftNotFoundError(DatabaseError):
    pass


class DraftOwnershipError(DatabaseError):
    pass


class DraftAlreadyApprovedError(DatabaseError):
    pass


class DraftAlreadyRejectedError(DatabaseError):
    pass


class DraftStaleError(DatabaseError):
    pass


class DraftNotPendingError(DatabaseError):
    pass


class VersionMismatchError(DatabaseError):
    pass


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Creates a configured SQLite connection with row factories and pragmas."""
    target_path = db_path or config.DATABASE_PATH
    conn = sqlite3.connect(target_path, timeout=10.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn


def ensure_db_initialized(db_path: Optional[str] = None) -> None:
    """Ensures database tables exist."""
    init_db(db_path)


def init_db(db_path: Optional[str] = None) -> None:
    """Initializes tables for published sites, versions, and drafts."""
    conn = get_connection(db_path)
    with conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS published_websites (
                user_id TEXT PRIMARY KEY,
                current_version INTEGER NOT NULL DEFAULT 1,
                html TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS website_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                html TEXT NOT NULL,
                instruction TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, version)
            );

            CREATE TABLE IF NOT EXISTS pending_drafts (
                draft_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                instruction TEXT NOT NULL,
                original_html TEXT NOT NULL,
                proposed_html TEXT NOT NULL,
                base_version INTEGER NOT NULL,
                validation_results TEXT NOT NULL,
                change_summary TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            );

            CREATE TABLE IF NOT EXISTS admin_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS admin_sessions (
                session_id TEXT PRIMARY KEY,
                admin_email TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS admin_conversations (
                id TEXT PRIMARY KEY,
                admin_email TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS admin_chat_messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                sender TEXT NOT NULL,
                message TEXT NOT NULL,
                draft_id TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_drafts_user_status ON pending_drafts(user_id, status);
            CREATE INDEX IF NOT EXISTS idx_versions_user ON website_versions(user_id, version);
            CREATE INDEX IF NOT EXISTS idx_chat_conv ON admin_chat_messages(conversation_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_conv_admin ON admin_conversations(admin_email, updated_at);
            """
        )
    conn.close()


def load_starter_html() -> str:
    """Loads baseline starter HTML from file."""
    path = Path(config.STARTER_SITE_PATH)
    if not path.is_file():
        # Fallback starter HTML if file is missing
        return (
            "<!DOCTYPE html><html><head><title>Default Site</title></head>"
            "<body><div id='booking-modal'><form id='booking-form'></form>"
            "<button id='open-booking-btn'></button><button id='close-booking-btn'></button>"
            "<div id='booking-confirmation'></div></div>"
            "<script id='booking-runtime-script'></script></body></html>"
        )
    return path.read_text(encoding="utf-8")


def get_or_create_user_site(user_id: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Retrieves published website for user, or initializes version 1 if user is new."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT user_id, current_version, html, updated_at FROM published_websites WHERE user_id = ?",
                (user_id,)
            )
            row = cur.fetchone()
            if row:
                return dict(row)

            # Initialize version 1 from starter_site.html
            initial_html = load_starter_html()
            now_iso = datetime.now(timezone.utc).isoformat()
            cur.execute(
                "INSERT INTO published_websites (user_id, current_version, html, updated_at) VALUES (?, ?, ?, ?)",
                (user_id, 1, initial_html, now_iso)
            )
            cur.execute(
                "INSERT INTO website_versions (user_id, version, html, instruction, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, 1, initial_html, "Initial baseline version", now_iso)
            )
            return {
                "user_id": user_id,
                "current_version": 1,
                "html": initial_html,
                "updated_at": now_iso
            }
    finally:
        conn.close()


def get_published_site(user_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieves current published site without auto-initializing."""
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id, current_version, html, updated_at FROM published_websites WHERE user_id = ?",
            (user_id,)
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_pending_draft(
    user_id: str,
    instruction: str,
    original_html: str,
    proposed_html: str,
    base_version: int,
    validation_results: Dict[str, Any],
    change_summary: str,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Creates a new draft for user.
    Explicitly marks any existing pending draft for this user as 'stale'
    before inserting the new draft.
    """
    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.cursor()
            # Explicitly mark existing pending drafts as stale (never silent overwrite)
            cur.execute(
                "UPDATE pending_drafts SET status = 'stale' WHERE user_id = ? AND status = 'pending'",
                (user_id,)
            )

            draft_id = f"draft_{uuid.uuid4().hex[:12]}"
            now_iso = datetime.now(timezone.utc).isoformat()
            validation_json = json.dumps(validation_results)

            cur.execute(
                """
                INSERT INTO pending_drafts (
                    draft_id, user_id, instruction, original_html, proposed_html,
                    base_version, validation_results, change_summary, created_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    draft_id, user_id, instruction, original_html, proposed_html,
                    base_version, validation_json, change_summary, now_iso
                )
            )

            return {
                "draft_id": draft_id,
                "user_id": user_id,
                "instruction": instruction,
                "original_html": original_html,
                "proposed_html": proposed_html,
                "base_version": base_version,
                "validation_results": validation_results,
                "change_summary": change_summary,
                "created_at": now_iso,
                "status": "pending"
            }
    finally:
        conn.close()


def get_pending_draft(user_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieves the active pending draft for a user."""
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT draft_id, user_id, instruction, original_html, proposed_html,
                   base_version, validation_results, change_summary, created_at, status
            FROM pending_drafts
            WHERE user_id = ? AND status = 'pending'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id,)
        )
        row = cur.fetchone()
        if not row:
            return None
        res = dict(row)
        try:
            res["validation_results"] = json.loads(res["validation_results"])
        except Exception:
            pass
        return res
    finally:
        conn.close()


def get_draft_by_id(draft_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieves a draft by its ID regardless of status."""
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT draft_id, user_id, instruction, original_html, proposed_html,
                   base_version, validation_results, change_summary, created_at, status
            FROM pending_drafts
            WHERE draft_id = ?
            """,
            (draft_id,)
        )
        row = cur.fetchone()
        if not row:
            return None
        res = dict(row)
        try:
            res["validation_results"] = json.loads(res["validation_results"])
        except Exception:
            pass
        return res
    finally:
        conn.close()


def approve_draft(user_id: str, draft_id: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Approves and publishes a pending draft atomically.
    Validates ownership, pending status, and base version matching current published version.
    """
    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.cursor()
            # Fetch draft inside transaction
            cur.execute(
                """
                SELECT draft_id, user_id, instruction, original_html, proposed_html,
                       base_version, validation_results, change_summary, created_at, status
                FROM pending_drafts
                WHERE draft_id = ?
                """,
                (draft_id,)
            )
            draft_row = cur.fetchone()
            if not draft_row:
                raise DraftNotFoundError(f"Draft with ID '{draft_id}' was not found.")

            draft = dict(draft_row)

            # Check ownership
            if draft["user_id"] != user_id:
                raise DraftOwnershipError("Draft does not belong to the specified user.")

            # Check status
            if draft["status"] == "approved":
                raise DraftAlreadyApprovedError(f"Draft '{draft_id}' has already been approved.")
            elif draft["status"] == "rejected":
                raise DraftAlreadyRejectedError(f"Draft '{draft_id}' has already been rejected.")
            elif draft["status"] == "stale":
                raise DraftStaleError(f"Draft '{draft_id}' is stale and cannot be approved.")
            elif draft["status"] != "pending":
                raise DraftNotPendingError(f"Draft '{draft_id}' is not in pending status (status={draft['status']}).")

            # Fetch current live site
            cur.execute(
                "SELECT user_id, current_version, html, updated_at FROM published_websites WHERE user_id = ?",
                (user_id,)
            )
            pub_row = cur.fetchone()
            if not pub_row:
                raise DatabaseError(f"No published website found for user '{user_id}'.")

            current_version = pub_row["current_version"]

            # Stale check: Current version must match draft's base_version
            if current_version != draft["base_version"]:
                # Mark draft as stale
                cur.execute(
                    "UPDATE pending_drafts SET status = 'stale' WHERE draft_id = ?",
                    (draft_id,)
                )
                raise VersionMismatchError(
                    f"Draft base version ({draft['base_version']}) does not match current published version ({current_version}). Draft is stale."
                )

            new_version = current_version + 1
            now_iso = datetime.now(timezone.utc).isoformat()

            # Atomically publish new HTML
            cur.execute(
                """
                UPDATE published_websites
                SET current_version = ?, html = ?, updated_at = ?
                WHERE user_id = ? AND current_version = ?
                """,
                (new_version, draft["proposed_html"], now_iso, user_id, current_version)
            )
            if cur.rowcount == 0:
                raise VersionMismatchError("Concurrent modification detected during publishing.")

            # Record version history
            cur.execute(
                """
                INSERT INTO website_versions (user_id, version, html, instruction, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, new_version, draft["proposed_html"], draft["instruction"], now_iso)
            )

            # Mark draft as approved
            cur.execute(
                "UPDATE pending_drafts SET status = 'approved' WHERE draft_id = ?",
                (draft_id,)
            )

            return {
                "status": "approved",
                "draft_id": draft_id,
                "user_id": user_id,
                "version": new_version,
                "message": f"Draft '{draft_id}' successfully approved and published as version {new_version}."
            }
    finally:
        conn.close()


def reject_draft(user_id: str, draft_id: Optional[str] = None, db_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Rejects a pending draft without modifying the live website.
    """
    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.cursor()
            if draft_id:
                cur.execute(
                    """
                    SELECT draft_id, user_id, instruction, original_html, proposed_html,
                           base_version, validation_results, change_summary, created_at, status
                    FROM pending_drafts
                    WHERE draft_id = ?
                    """,
                    (draft_id,)
                )
                row = cur.fetchone()
                if not row:
                    raise DraftNotFoundError(f"Draft with ID '{draft_id}' was not found.")
                draft = dict(row)
                if draft["user_id"] != user_id:
                    raise DraftOwnershipError("Draft does not belong to the specified user.")
            else:
                # Target active pending draft for user
                cur.execute(
                    """
                    SELECT draft_id, user_id, instruction, original_html, proposed_html,
                           base_version, validation_results, change_summary, created_at, status
                    FROM pending_drafts
                    WHERE user_id = ? AND status = 'pending'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (user_id,)
                )
                row = cur.fetchone()
                if not row:
                    raise DraftNotFoundError("No pending draft found for this user.")
                draft = dict(row)

            target_id = draft["draft_id"]
            if draft["status"] == "approved":
                raise DraftAlreadyApprovedError(f"Draft '{target_id}' has already been approved.")
            elif draft["status"] == "rejected":
                raise DraftAlreadyRejectedError(f"Draft '{target_id}' has already been rejected.")
            elif draft["status"] != "pending":
                raise DraftNotPendingError(f"Draft '{target_id}' is not in pending status (status={draft['status']}).")

            # Mark draft as rejected
            cur.execute(
                "UPDATE pending_drafts SET status = 'rejected' WHERE draft_id = ?",
                (target_id,)
            )

            # Get current live site version for response
            cur.execute(
                "SELECT current_version FROM published_websites WHERE user_id = ?",
                (user_id,)
            )
            pub = cur.fetchone()
            current_version = pub["current_version"] if pub else 1

            return {
                "status": "rejected",
                "draft_id": target_id,
                "user_id": user_id,
                "version": current_version,
                "message": f"Draft '{target_id}' has been rejected. Live website remains unchanged at version {current_version}."
            }
    finally:
        conn.close()


def rollback_user_site(user_id: str, db_path: Optional[str] = None) -> Tuple[bool, int, str]:
    """
    Rolls back user's published website to the immediately preceding version.
    Returns (success, version, message).
    """
    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT current_version FROM published_websites WHERE user_id = ?",
                (user_id,)
            )
            row = cur.fetchone()
            if not row:
                return False, 1, "No website exists for user."

            current_version = row["current_version"]
            if current_version <= 1:
                return False, 1, "Already at version 1 (earliest baseline). Nothing to undo."

            target_version = current_version - 1
            cur.execute(
                "SELECT html FROM website_versions WHERE user_id = ? AND version = ?",
                (user_id, target_version)
            )
            prev_row = cur.fetchone()
            if not prev_row:
                return False, current_version, f"Version {target_version} not found in history."

            now_iso = datetime.now(timezone.utc).isoformat()
            # Update published website to previous version
            cur.execute(
                "UPDATE published_websites SET current_version = ?, html = ?, updated_at = ? WHERE user_id = ?",
                (target_version, prev_row["html"], now_iso, user_id)
            )

            # Mark any pending draft as stale because the base version changed
            cur.execute(
                "UPDATE pending_drafts SET status = 'stale' WHERE user_id = ? AND status = 'pending'",
                (user_id,)
            )

            return True, target_version, f"Successfully rolled back to version {target_version}."
    finally:
        conn.close()


def get_website_versions(user_id: str, db_path: Optional[str] = None) -> list[Dict[str, Any]]:
    """Retrieves full published version history for a user, newest first."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT version, instruction, created_at, length(html) as html_size
            FROM website_versions
            WHERE user_id = ?
            ORDER BY version DESC
            """,
            (user_id,)
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_website_version_html(user_id: str, version: int, db_path: Optional[str] = None) -> Optional[str]:
    """Retrieves full HTML of a specific historical version."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT html FROM website_versions WHERE user_id = ? AND version = ?",
            (user_id, version)
        )
        row = cur.fetchone()
        return row["html"] if row else None
    finally:
        conn.close()


def restore_website_version(user_id: str, target_version: int, db_path: Optional[str] = None) -> Tuple[bool, int, str]:
    """
    Restores a previously published version as the new active live version.
    Never allows restoring drafts as published versions.
    Creates a new version history entry.
    """
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.cursor()
            # 1. Fetch target historical published version
            cur.execute(
                "SELECT version, html, instruction FROM website_versions WHERE user_id = ? AND version = ?",
                (user_id, target_version)
            )
            target_row = cur.fetchone()
            if not target_row:
                return False, 0, f"Published version {target_version} does not exist in history."

            # 2. Fetch current live version
            cur.execute(
                "SELECT current_version FROM published_websites WHERE user_id = ?",
                (user_id,)
            )
            pub_row = cur.fetchone()
            current_ver = pub_row["current_version"] if pub_row else 1
            new_version = current_ver + 1
            now_iso = datetime.now(timezone.utc).isoformat()

            # 3. Update published website atomically
            cur.execute(
                "UPDATE published_websites SET current_version = ?, html = ?, updated_at = ? WHERE user_id = ?",
                (new_version, target_row["html"], now_iso, user_id)
            )

            # 4. Insert new version record indicating restore
            instruction = f"Restored from version {target_version}: {target_row['instruction'] or ''}".strip()
            cur.execute(
                """
                INSERT INTO website_versions (user_id, version, html, instruction, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, new_version, target_row["html"], instruction, now_iso)
            )

            # 5. Invalidate any pending drafts because the live version changed
            cur.execute(
                "UPDATE pending_drafts SET status = 'stale' WHERE user_id = ? AND status = 'pending'",
                (user_id,)
            )

            return True, new_version, f"Successfully restored version {target_version} as new live version {new_version}."
    finally:
        conn.close()


# ==============================================================================
# Admin & Session Database Methods
# ==============================================================================

def get_admin_by_email(email: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetches administrator account by email."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, email, password_hash, created_at, updated_at FROM admin_users WHERE email = ?",
            (email.strip().lower(),)
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_admin_user(email: str, password_hash: str, db_path: Optional[str] = None) -> bool:
    """Creates a new administrator account."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        with conn:
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO admin_users (email, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (email.strip().lower(), password_hash, now_iso, now_iso)
            )
            return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def update_admin_password(email: str, new_password_hash: str, db_path: Optional[str] = None) -> bool:
    """Updates administrator password hash."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        with conn:
            now_iso = datetime.now(timezone.utc).isoformat()
            cur = conn.cursor()
            cur.execute(
                "UPDATE admin_users SET password_hash = ?, updated_at = ? WHERE email = ?",
                (new_password_hash, now_iso, email.strip().lower())
            )
            return cur.rowcount > 0
    finally:
        conn.close()


def create_db_session(session_id: str, admin_email: str, expires_at: str, db_path: Optional[str] = None) -> None:
    """Stores an active admin session."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        with conn:
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO admin_sessions (session_id, admin_email, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (session_id, admin_email.strip().lower(), expires_at, now_iso)
            )
    finally:
        conn.close()


def get_db_session(session_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieves session if valid and not expired."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT session_id, admin_email, expires_at, created_at FROM admin_sessions WHERE session_id = ?",
            (session_id,)
        )
        row = cur.fetchone()
        if not row:
            return None
        session = dict(row)
        # Check expiry
        now_iso = datetime.now(timezone.utc).isoformat()
        if session["expires_at"] < now_iso:
            # Expired session; delete it
            with conn:
                conn.execute("DELETE FROM admin_sessions WHERE session_id = ?", (session_id,))
            return None
        return session
    finally:
        conn.close()


def delete_db_session(session_id: str, db_path: Optional[str] = None) -> None:
    """Deletes an active session on logout."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM admin_sessions WHERE session_id = ?", (session_id,))
    finally:
        conn.close()


# ==============================================================================
# Conversations & Chat Messages Database Methods
# ==============================================================================

def create_conversation(admin_email: str, title: str, conv_id: Optional[str] = None, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Creates a new conversation record."""
    init_db(db_path)
    cid = conv_id or f"conv_{uuid.uuid4().hex[:10]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO admin_conversations (id, admin_email, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (cid, admin_email.strip().lower(), title.strip() or "New Conversation", now_iso, now_iso)
            )
            return {
                "id": cid,
                "admin_email": admin_email.strip().lower(),
                "title": title.strip() or "New Conversation",
                "created_at": now_iso,
                "updated_at": now_iso
            }
    finally:
        conn.close()


def get_conversations(admin_email: str, db_path: Optional[str] = None) -> list[Dict[str, Any]]:
    """Retrieves all conversations for the admin, newest first."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, admin_email, title, created_at, updated_at FROM admin_conversations WHERE admin_email = ? ORDER BY updated_at DESC",
            (admin_email.strip().lower(),)
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def add_chat_message(conversation_id: str, sender: str, message: str, draft_id: Optional[str] = None, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Adds a chat message to a conversation."""
    init_db(db_path)
    mid = f"msg_{uuid.uuid4().hex[:10]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO admin_chat_messages (id, conversation_id, sender, message, draft_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (mid, conversation_id, sender, message, draft_id, now_iso)
            )
            # Update conversation updated_at
            conn.execute(
                "UPDATE admin_conversations SET updated_at = ? WHERE id = ?",
                (now_iso, conversation_id)
            )
            return {
                "id": mid,
                "conversation_id": conversation_id,
                "sender": sender,
                "message": message,
                "draft_id": draft_id,
                "created_at": now_iso
            }
    finally:
        conn.close()


def get_chat_messages(conversation_id: str, db_path: Optional[str] = None) -> list[Dict[str, Any]]:
    """Retrieves all chat messages in a conversation in chronological order."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, conversation_id, sender, message, draft_id, created_at
            FROM admin_chat_messages
            WHERE conversation_id = ?
            ORDER BY created_at ASC
            """,
            (conversation_id,)
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

