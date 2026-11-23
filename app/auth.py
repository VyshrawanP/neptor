import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple

import bcrypt
from fastapi import Request, HTTPException, status
from fastapi.responses import RedirectResponse

from app.config import (
    ADMIN_EMAIL,
    ADMIN_INITIAL_PASSWORD,
    SESSION_COOKIE_NAME,
)
from app.database import (
    get_admin_by_email,
    create_admin_user,
    update_admin_password,
    create_db_session,
    get_db_session,
    delete_db_session,
)

logger = logging.getLogger("admin_auth")


def hash_password(plain_password: str) -> str:
    """Hashes a password using bcrypt."""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(plain_password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception as e:
        logger.error("Password verification error: %s", str(e))
        return False


def seed_initial_admin(db_path: Optional[str] = None) -> None:
    """
    Initializes the default administrator account if none exists.
    Uses credentials from environment variables (defaults to admin@neptor.in / neptor123).
    """
    existing = get_admin_by_email(ADMIN_EMAIL, db_path=db_path)
    if not existing:
        pwd_hash = hash_password(ADMIN_INITIAL_PASSWORD)
        created = create_admin_user(ADMIN_EMAIL, pwd_hash, db_path=db_path)
        if created:
            logger.info("Seeded initial administrator account: %s", ADMIN_EMAIL)


def authenticate_admin(email: str, password: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Authenticates admin credentials, returning user record if valid."""
    admin = get_admin_by_email(email, db_path=db_path)
    if not admin:
        return None
    if verify_password(password, admin["password_hash"]):
        return admin
    return None


def create_session(admin_email: str, duration_days: int = 7, db_path: Optional[str] = None) -> str:
    """Generates a secure session token and persists it in SQLite."""
    session_id = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=duration_days)).isoformat()
    create_db_session(session_id, admin_email, expires_at, db_path=db_path)
    return session_id


def get_current_admin(request: Request, db_path: Optional[str] = None) -> Optional[str]:
    """
    Extracts and validates active session from HTTP-only cookie or Authorization header.
    Returns admin_email or None.
    """
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            session_id = auth_header[7:].strip()

    if not session_id:
        return None

    session = get_db_session(session_id, db_path=db_path)
    if not session:
        return None

    return session["admin_email"]


def require_admin(request: Request, db_path: Optional[str] = None) -> str:
    """
    FastAPI dependency: verifies admin authentication.
    Raises 401 HTTPException if unauthenticated.
    """
    admin_email = get_current_admin(request, db_path=db_path)
    if not admin_email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please sign in to access the administrator dashboard."
        )
    return admin_email


def require_admin_html(request: Request, db_path: Optional[str] = None) -> str:
    """
    Helper for HTML page routes: if unauthenticated, returns None
    so route can redirect to /admin/login.
    """
    return get_current_admin(request, db_path=db_path)


def change_admin_password(email: str, old_password: str, new_password: str, db_path: Optional[str] = None) -> Tuple[bool, str]:
    """Validates old password and updates to new hashed password."""
    admin = authenticate_admin(email, old_password, db_path=db_path)
    if not admin:
        return False, "Incorrect current password."
    if len(new_password) < 6:
        return False, "New password must be at least 6 characters long."
    new_hash = hash_password(new_password)
    updated = update_admin_password(email, new_hash, db_path=db_path)
    if updated:
        return True, "Password successfully updated."
    return False, "Failed to update password in database."
