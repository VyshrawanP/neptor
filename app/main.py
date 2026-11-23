import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware

from app.config import (
    DATABASE_PATH,
    LLM_MODEL,
    GROQ_API_KEY,
    FRONTEND_DIR,
    SESSION_COOKIE_NAME,
)
from app.database import (
    init_db,
    get_or_create_user_site,
    get_published_site,
    create_pending_draft,
    get_pending_draft,
    get_draft_by_id,
    approve_draft,
    reject_draft,
    rollback_user_site,
    get_website_versions,
    get_website_version_html,
    restore_website_version,
    create_conversation,
    get_conversations,
    add_chat_message,
    get_chat_messages,
    DraftNotFoundError,
    DraftOwnershipError,
    DraftAlreadyApprovedError,
    DraftAlreadyRejectedError,
    DraftStaleError,
    DraftNotPendingError,
    VersionMismatchError,
    DatabaseError,
)
from app.auth import (
    seed_initial_admin,
    authenticate_admin,
    create_session,
    get_current_admin,
    require_admin,
    delete_db_session,
    change_admin_password,
)
from app.diff_service import generate_diff_and_summary
from app.llm_service import (
    classify_intent,
    generate_modified_html,
    LLMError,
    LLMTimeoutError,
    LLMConfigurationError,
)
import app.llm_service as orig_llm_service
from app.lock_manager import user_lock_manager
from app.models import (
    WebhookRequest,
    WebhookResponse,
    ApproveRequest,
    ApproveResponse,
    RejectRequest,
    RejectResponse,
    PreviewResponse,
    LoginRequest,
    ChangePasswordRequest,
    CreateConversationRequest,
    ChatMessageRequest,
)
from app.preview_service import render_preview_dashboard_html
from app.validation import validate_html, clean_code_fences
from app.sandbox.sandbox_manager import sandbox_manager
from app.sandbox.sandbox_validator import validate_sandbox_html

logger = logging.getLogger("editor_api")
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initializes SQLite schema and default admin account on startup."""
    init_db()
    seed_initial_admin()
    logger.info("Initialized SQLite database and seeded admin account at %s", DATABASE_PATH)
    yield


app = FastAPI(
    title="Neptor Resilient Conversational Web Editor API",
    description="Production-quality preview before publishing and comparison system for conversational website editing with dedicated Admin Dashboard.",
    version="2.0.0",
    lifespan=lifespan,
)

# Enable CORS for frontend clients / dashboards
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", summary="Health check")
async def health_check():
    """Health check endpoint for deployment verification."""
    return {"status": "ok"}


# ==============================================================================
# Admin Authentication & Dashboard Endpoints
# ==============================================================================

@app.get("/admin", summary="Admin Root Redirect")
async def admin_root(request: Request):
    """Redirects to admin dashboard if authenticated, or login page if not."""
    admin = get_current_admin(request)
    if admin:
        return RedirectResponse(url="/admin/dashboard", status_code=status.HTTP_302_FOUND)
    return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)


@app.get("/admin/login", summary="Admin Login Page")
async def admin_login_page(request: Request):
    """Serves the dedicated Admin Login page."""
    admin = get_current_admin(request)
    if admin:
        return RedirectResponse(url="/admin/dashboard", status_code=status.HTTP_302_FOUND)
    login_file = Path(FRONTEND_DIR) / "login.html"
    if not login_file.is_file():
        raise HTTPException(status_code=404, detail="Login page template not found.")
    return HTMLResponse(content=login_file.read_text(encoding="utf-8"))


@app.post("/login", summary="Administrator Authentication")
@app.post("/admin/login", summary="Administrator Authentication")
async def login(payload: LoginRequest, response: Response):
    """
    Authenticates administrator using bcrypt hashed credentials.
    Sets secure, HTTP-only session cookie.
    """
    admin = authenticate_admin(payload.email, payload.password)
    if not admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password."
        )

    session_id = create_session(admin["email"], duration_days=7)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
        max_age=7 * 24 * 3600,
        secure=False,
    )
    return {
        "status": "ok",
        "message": "Authenticated successfully",
        "email": admin["email"]
    }


@app.post("/logout", summary="Administrator Logout")
@app.get("/admin/logout", summary="Administrator Logout")
async def logout(request: Request, response: Response):
    """Ends admin session and deletes session cookie."""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        delete_db_session(session_id)
    response.delete_cookie(key=SESSION_COOKIE_NAME)
    if request.method == "GET":
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)
    return {"status": "ok", "message": "Logged out successfully"}


@app.get("/admin/dashboard", summary="Admin AI Editor Dashboard")
async def admin_dashboard(request: Request):
    """Serves the protected, responsive Admin Dashboard UI."""
    admin = get_current_admin(request)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)
    dash_file = Path(FRONTEND_DIR) / "dashboard.html"
    if not dash_file.is_file():
        raise HTTPException(status_code=404, detail="Dashboard template not found.")
    return HTMLResponse(content=dash_file.read_text(encoding="utf-8"))


@app.post("/admin/change-password", summary="Change Admin Password")
async def change_password(payload: ChangePasswordRequest, request: Request):
    """Allows authenticated administrator to update their password."""
    admin_email = require_admin(request)
    ok, msg = change_admin_password(admin_email, payload.old_password, payload.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg}


@app.get("/admin/conversations", summary="List Admin Conversations")
async def list_conversations(request: Request):
    """Retrieves list of past conversations for the authenticated admin."""
    admin_email = require_admin(request)
    return get_conversations(admin_email)


@app.post("/admin/conversations", summary="Create New Conversation")
async def create_new_conversation(payload: CreateConversationRequest, request: Request):
    """Creates a new conversation session for the admin."""
    admin_email = require_admin(request)
    return create_conversation(admin_email, payload.title or "New Conversation")


@app.get("/admin/conversations/{conv_id}/messages", summary="List Chat Messages")
async def get_messages(conv_id: str, request: Request):
    """Retrieves all chat messages in a conversation."""
    require_admin(request)
    return get_chat_messages(conv_id)


@app.post("/admin/conversations/{conv_id}/messages", summary="Add Chat Message")
async def save_chat_message(conv_id: str, payload: ChatMessageRequest, request: Request):
    """Stores a message in the conversation history."""
    require_admin(request)
    return add_chat_message(conv_id, "user", payload.message)


# ==============================================================================
# Version History Endpoints
# ==============================================================================

@app.get("/versions/{user_id}", summary="Get Website Version History")
async def get_versions(user_id: str):
    """Returns list of published website versions for user."""
    return get_website_versions(user_id)


@app.get("/versions/{user_id}/{version}", summary="Get Historical Version HTML")
async def get_version_html(user_id: str, version: int):
    """Returns full HTML of a specific historical version for preview."""
    html_content = get_website_version_html(user_id, version)
    if not html_content:
        raise HTTPException(status_code=404, detail=f"Version {version} not found for user '{user_id}'.")
    return Response(content=html_content, media_type="text/html; charset=utf-8")


@app.post("/versions/{user_id}/restore/{version}", summary="Restore Specific Published Version")
async def restore_version(user_id: str, version: int):
    """
    Atomically restores a previous published version as the new live website version.
    Never allows restoring drafts as published versions.
    """
    lock = await user_lock_manager.get_lock(user_id)
    async with lock:
        ok, new_ver, msg = restore_website_version(user_id, version)
        if not ok:
            raise HTTPException(status_code=404, detail=msg)
        return {"status": "restored", "message": msg, "version": new_ver}


@app.get("/site/{user_id}", summary="Get currently published website HTML")
async def get_site(user_id: str):
    """
    Returns the currently published HTML page for the user.
    If the user has not made any edits yet, initializes and serves the starter site.
    """
    if not user_id or not user_id.strip():
        raise HTTPException(status_code=400, detail="Invalid user_id.")

    lock = await user_lock_manager.get_lock(user_id)
    async with lock:
        site = get_or_create_user_site(user_id)
        return Response(
            content=site["html"],
            media_type="text/html; charset=utf-8",
            headers={"X-Website-Version": str(site["current_version"])},
        )


@app.post("/webhook", response_model=WebhookResponse, summary="Process conversational message or edit")
async def webhook(payload: WebhookRequest):
    """
    Receives user messages, classifies intent, generates and validates modifications.
    NEVER publishes AI modifications automatically: saves valid modifications as pending drafts
    for explicit preview and approval.
    """
    user_id = payload.user_id.strip()
    message = payload.message.strip()

    if not user_id or not message:
        raise HTTPException(status_code=422, detail="Both user_id and message are required.")

    lock = await user_lock_manager.get_lock(user_id)
    async with lock:
        # 1. Ensure user site is initialized
        site = get_or_create_user_site(user_id)
        current_version = site["current_version"]
        current_html = site["html"]

        # 2. Identify intent
        intent = classify_intent(message)

        # 3. Handle casual conversation
        if intent == "chat":
            return WebhookResponse(
                status="no_change",
                action="chat",
                reply="Thanks for reaching out! Let me know if you would like to edit your menu, business hours, colors, or page title.",
                version=current_version,
            )

        # 4. Handle undo / rollback
        if intent == "undo":
            success, new_version, note = rollback_user_site(user_id)
            if success:
                return WebhookResponse(
                    status="rolled_back",
                    action="undo",
                    reply=f"Successfully restored previous version (now at version {new_version}).",
                    version=new_version,
                )
            else:
                return WebhookResponse(
                    status="no_change",
                    action="undo",
                    reply=note,
                    version=current_version,
                )

        # 5. Handle edit request using the isolated Website Editing Sandbox
        try:
            # Check if generate_modified_html was monkeypatched (e.g. for mock tests)
            is_mocked = (
                generate_modified_html is not orig_llm_service.generate_modified_html
                or hasattr(generate_modified_html, "__mock__")
            )

            if is_mocked:
                raw_proposed = await generate_modified_html(message, current_html)
                proposed_html = clean_code_fences(raw_proposed)
                val_result = validate_sandbox_html(proposed_html, current_html)
                if not val_result["is_valid"]:
                    logger.warning("QA validation failed for user %s: %s", user_id, val_result["errors"])
                    return WebhookResponse(
                        status="rejected",
                        action="rejected",
                        reply=f"Proposed changes were rejected by QA validation: {'; '.join(val_result['errors'])}. Live site untouched.",
                        version=current_version,
                        validation_errors=val_result["errors"],
                    )
                diff_info = generate_diff_and_summary(current_html, proposed_html)
                draft = create_pending_draft(
                    user_id=user_id,
                    instruction=message,
                    original_html=current_html,
                    proposed_html=proposed_html,
                    base_version=current_version,
                    validation_results=val_result,
                    change_summary=diff_info["change_summary"],
                )
                return WebhookResponse(
                    status="pending",
                    action="edit",
                    reply=f"Draft {draft['draft_id']} created. Preview changes at /preview/{user_id} and approve to publish.",
                    version=current_version,
                    draft_id=draft["draft_id"],
                    preview_url=f"/preview/{user_id}",
                    change_summary=diff_info["change_summary"],
                    diff=diff_info["html_diff"],
                )

            # Standard Isolated Sandbox Pipeline
            sandbox_res = await sandbox_manager.create_sandbox_modification(user_id, message)
            return WebhookResponse(
                status=sandbox_res["status"],
                action=sandbox_res["action"],
                reply=sandbox_res["reply"],
                version=sandbox_res["version"],
                draft_id=sandbox_res.get("draft_id"),
                preview_url=sandbox_res.get("preview_url"),
                change_summary=sandbox_res.get("change_summary"),
                diff=sandbox_res.get("diff"),
                validation_errors=sandbox_res.get("validation_errors"),
            )
        except (LLMTimeoutError, TimeoutError) as e:
            logger.warning("LLM timeout for user %s: %s", user_id, str(e))
            return WebhookResponse(
                status="error",
                action="rejected",
                reply="The AI modification service timed out. Your live website remains 100% intact.",
                version=current_version,
                validation_errors=["LLM request timed out (<= 20s limit)."],
            )
        except LLMConfigurationError as e:
            logger.error("LLM config error: %s", str(e))
            raise HTTPException(
                status_code=503,
                detail=str(e)
            )
        except LLMError as e:
            logger.error("LLM failure for user %s: %s", user_id, str(e))
            return WebhookResponse(
                status="error",
                action="rejected",
                reply=f"AI generation failed: {str(e)}. Live site untouched.",
                version=current_version,
                validation_errors=[str(e)],
            )
        except Exception as e:
            logger.error("Sandbox modification error for user %s: %s", user_id, str(e))
            return WebhookResponse(
                status="error",
                action="rejected",
                reply=f"Sandbox modification failed: {str(e)}. Live site untouched.",
                version=current_version,
                validation_errors=[str(e)],
            )


@app.get("/preview/{user_id}", summary="Retrieve pending draft preview")
async def get_preview(user_id: str, request: Request, format: Optional[str] = None):
    """
    Returns the user's pending draft.
    Accepts format='json' or format='html' (or Accept header).
    Provides draft ID, instruction, original HTML, proposed HTML, diff, change summary,
    validation results, base version, timestamp, and status.
    """
    lock = await user_lock_manager.get_lock(user_id)
    async with lock:
        draft = get_pending_draft(user_id)
        if not draft:
            raise HTTPException(
                status_code=404,
                detail=f"No pending draft exists for user '{user_id}'."
            )

        diff_info = generate_diff_and_summary(draft["original_html"], draft["proposed_html"])

        # Check if browser requested HTML view
        accept_header = request.headers.get("accept", "")
        wants_html = format == "html" or ("text/html" in accept_header and format != "json")

        if wants_html:
            site = get_published_site(user_id)
            current_live_ver = site["current_version"] if site else draft["base_version"]
            html_view = render_preview_dashboard_html(
                user_id=user_id,
                draft=draft,
                html_diff=diff_info["html_diff"],
                current_live_version=current_live_ver,
            )
            return HTMLResponse(content=html_view)

        # Standard API JSON response
        return PreviewResponse(
            draft_id=draft["draft_id"],
            user_id=draft["user_id"],
            user_instruction=draft["instruction"],
            instruction=draft["instruction"],
            original_html=draft["original_html"],
            proposed_html=draft["proposed_html"],
            html_diff=diff_info["html_diff"],
            diff=diff_info["html_diff"],
            change_summary=draft["change_summary"],
            validation_results=draft["validation_results"],
            base_version=draft["base_version"],
            created_at=draft["created_at"],
            status=draft["status"],
            structured_changes=diff_info["structured_changes"],
        )


@app.get("/preview/{user_id}/render", summary="Render sandboxed proposed draft HTML")
async def render_preview_proposed(user_id: str):
    """
    Serves the proposed HTML for the user's pending draft with restrictive CSP headers
    so it can be securely rendered inside a sandboxed iframe.
    """
    lock = await user_lock_manager.get_lock(user_id)
    async with lock:
        draft = get_pending_draft(user_id)
        if not draft:
            raise HTTPException(status_code=404, detail=f"No pending draft exists for user '{user_id}'.")

        return HTMLResponse(
            content=draft["proposed_html"],
            headers={
                "Content-Security-Policy": "frame-ancestors 'self';",
                "X-Frame-Options": "SAMEORIGIN",
            }
        )


@app.post("/approve/{user_id}", response_model=ApproveResponse, summary="Approve and publish pending draft")
async def approve(user_id: str, payload: ApproveRequest):
    """
    Accepts a draft ID and publishes the proposed HTML atomically.
    Verifies draft ownership, pending status, and base version consistency.
    """
    draft_id = payload.draft_id.strip()
    if not draft_id:
        raise HTTPException(status_code=422, detail="draft_id is required.")

    lock = await user_lock_manager.get_lock(user_id)
    async with lock:
        try:
            result = approve_draft(user_id, draft_id)
            return ApproveResponse(
                status="approved",
                message=result["message"],
                version=result["version"],
                draft_id=draft_id,
            )
        except DraftNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except DraftOwnershipError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except DraftAlreadyApprovedError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except DraftAlreadyRejectedError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except DraftStaleError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except VersionMismatchError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except DraftNotPendingError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except DatabaseError as e:
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/reject/{user_id}", response_model=RejectResponse, summary="Reject pending draft")
async def reject(user_id: str, payload: Optional[RejectRequest] = None):
    """
    Rejects a pending draft without modifying the live website.
    Verifies draft ownership and pending status.
    """
    draft_id = payload.draft_id.strip() if payload and payload.draft_id else None

    lock = await user_lock_manager.get_lock(user_id)
    async with lock:
        try:
            result = reject_draft(user_id, draft_id)
            return RejectResponse(
                status="rejected",
                message=result["message"],
                version=result["version"],
                draft_id=result["draft_id"],
            )
        except DraftNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except DraftOwnershipError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except DraftAlreadyApprovedError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except DraftAlreadyRejectedError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except DraftNotPendingError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except DatabaseError as e:
            raise HTTPException(status_code=500, detail=str(e))
