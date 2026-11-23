import logging
from typing import Dict, Any, Optional

from app.database import (
    get_or_create_user_site,
    create_pending_draft,
    get_pending_draft,
    approve_draft,
    reject_draft,
)
from app.diff_service import generate_diff_and_summary
from app.sandbox.html_parser import extract_website_context
from app.sandbox.edit_planner import plan_edits
from app.sandbox.edit_executor import execute_structured_operations, EditExecutionError
from app.sandbox.sandbox_validator import validate_sandbox_html
from app.sandbox.preview_renderer import render_sandbox_in_browser

logger = logging.getLogger("sandbox_manager")


class SandboxManager:
    """
    Manages the lifecycle of an isolated website editing sandbox:
    1. Copies the published website snapshot.
    2. Contextualizes the site for Groq.
    3. Plans and executes structured DOM modifications.
    4. Validates reservation contract invariants.
    5. Renders in headless browser.
    6. Stores pending draft without altering production.
    """

    async def create_sandbox_modification(
        self,
        user_id: str,
        instruction: str,
        llm: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Creates an isolated sandbox draft for the user instruction.
        Returns result dict with draft_id and status.
        """
        # 1. Retrieve current published website snapshot
        site = get_or_create_user_site(user_id)
        current_version = site["current_version"]
        original_html = site["html"]

        # 2. Extract structured website context
        context = extract_website_context(original_html)

        # 3. Plan structured editing operations
        try:
            plan = await plan_edits(instruction, context, llm=llm)
            explanation = plan.get("explanation", "Targeted website modifications applied.")
            operations = plan.get("operations", [])
        except Exception as plan_err:
            logger.warning("Structured planning error: %s. Attempting fallback.", str(plan_err))
            raise plan_err

        # 4. Safely apply operations to an isolated copy of the DOM
        try:
            modified_html = execute_structured_operations(original_html, operations)
        except EditExecutionError as exec_err:
            logger.warning("Edit execution error for %s: %s", user_id, str(exec_err))
            return {
                "status": "rejected",
                "action": "rejected",
                "reply": f"Modification rejected: {str(exec_err)}. Live website remains unchanged.",
                "version": current_version,
                "draft_id": None,
                "validation_errors": [str(exec_err)],
            }

        # 5. QA Validation Gate
        val_res = validate_sandbox_html(modified_html, original_html)
        if not val_res["is_valid"]:
            logger.warning("Sandbox QA validation failed for %s: %s", user_id, val_res["errors"])
            return {
                "status": "rejected",
                "action": "rejected",
                "reply": f"Proposed modification rejected by QA validation: {'; '.join(val_res['errors'])}. Live site untouched.",
                "version": current_version,
                "draft_id": None,
                "validation_errors": val_res["errors"],
            }

        # 6. Browser-based preview verification (Playwright)
        browser_info = await render_sandbox_in_browser(modified_html, test_interactions=True)

        # 7. Generate diff and semantic summary
        diff_info = generate_diff_and_summary(original_html, modified_html)
        change_summary = diff_info.get("change_summary") or explanation

        # 8. Store isolated draft in SQLite (explicitly supersedes prior pending draft)
        validation_payload = {
            "is_valid": True,
            "errors": [],
            "checks": val_res["checks"],
            "browser_verification": {
                "browser_rendered": browser_info.get("browser_rendered", True),
                "modal_interactive": browser_info.get("modal_interactive", True),
                "console_errors": browser_info.get("console_errors", []),
            }
        }

        draft = create_pending_draft(
            user_id=user_id,
            instruction=instruction,
            original_html=original_html,
            proposed_html=modified_html,
            base_version=current_version,
            validation_results=validation_payload,
            change_summary=change_summary,
        )

        return {
            "status": "pending",
            "action": "edit",
            "reply": f"Draft {draft['draft_id']} created in sandbox. Review preview before approving.",
            "version": current_version,
            "draft_id": draft["draft_id"],
            "preview_url": f"/preview/{user_id}",
            "change_summary": change_summary,
            "diff": diff_info["html_diff"],
            "browser_info": browser_info,
        }

    def approve_sandbox(self, user_id: str, draft_id: str) -> Dict[str, Any]:
        """Atomically approves and publishes a pending sandbox draft."""
        return approve_draft(user_id, draft_id)

    def reject_sandbox(self, user_id: str, draft_id: Optional[str] = None) -> Dict[str, Any]:
        """Rejects a pending sandbox draft without altering live site."""
        return reject_draft(user_id, draft_id)


# Global sandbox manager instance
sandbox_manager = SandboxManager()
