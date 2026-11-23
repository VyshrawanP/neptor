import asyncio
import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from app.database import (
    get_or_create_user_site,
    get_pending_draft,
    get_draft_by_id,
    load_starter_html,
)
from app.llm_service import LLMTimeoutError
from app.sandbox.html_parser import extract_website_context, format_context_for_prompt
from app.sandbox.edit_planner import parse_plan_json
from app.sandbox.edit_executor import execute_structured_operations, EditExecutionError
from app.sandbox.sandbox_validator import validate_sandbox_html
from app.sandbox.preview_renderer import render_sandbox_in_browser
from app.sandbox.sandbox_manager import sandbox_manager


# ==============================================================================
# Helper Mock Factories
# ==============================================================================

def mock_plan(operations, explanation="Applying requested edits."):
    """Returns an async plan_edits mock returning structured operations."""
    async def _mock(instruction, context, llm=None):
        return {
            "explanation": explanation,
            "operations": operations,
        }
    return _mock


# ==============================================================================
# SCENARIO 1: "Add Masala Dosa for ₹250"
# ==============================================================================
def test_scenario_01_add_masala_dosa_250(client, monkeypatch):
    """
    Scenario 1:
    - User sends instruction: 'Add Masala Dosa for 250.'
    - Verifies item appears in rendered menu (#menu-items).
    - Price is formatted as ₹250.
    - Mandatory booking elements intact.
    - Live site at /site/{user_id} changes only after explicit approval.
    """
    user_id = "chef_user_01"

    operations = [
        {
            "type": "add_menu_item",
            "target": "#menu-items",
            "data": {
                "name": "Masala Dosa",
                "price": 250,
                "description": "Crispy golden fermented crepe filled with spiced potato masala."
            }
        }
    ]
    monkeypatch.setattr("app.sandbox.sandbox_manager.plan_edits", mock_plan(operations, "Added Masala Dosa for ₹250 to menu."))

    # 1. Post to webhook
    res = client.post("/webhook", json={
        "user_id": user_id,
        "message": "Add Masala Dosa for 250."
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "pending"
    assert data["action"] == "edit"
    draft_id = data["draft_id"]
    assert draft_id is not None
    assert data["version"] == 1

    # 2. Verify live site is UNCHANGED
    site_res = client.get(f"/site/{user_id}")
    assert site_res.status_code == 200
    assert "Masala Dosa" not in site_res.text
    assert site_res.headers["X-Website-Version"] == "1"

    # 3. Verify preview contains Masala Dosa with price ₹250 and booking elements intact
    prev_res = client.get(f"/preview/{user_id}")
    assert prev_res.status_code == 200
    prev_data = prev_res.json()
    proposed_html = prev_data["proposed_html"]

    assert "Masala Dosa" in proposed_html
    assert "₹250" in proposed_html
    assert 'id="booking-modal"' in proposed_html
    assert 'id="booking-form"' in proposed_html
    assert 'id="open-booking-btn"' in proposed_html
    assert 'id="booking-runtime-script"' in proposed_html

    # Check browser verification status
    browser_ver = prev_data["validation_results"].get("browser_verification", {})
    assert browser_ver.get("browser_rendered") is True
    assert browser_ver.get("modal_interactive") is True

    # 4. Approve draft
    app_res = client.post(f"/approve/{user_id}", json={"draft_id": draft_id})
    assert app_res.status_code == 200
    app_data = app_res.json()
    assert app_data["status"] == "approved"
    assert app_data["version"] == 2

    # 5. Live site now reflects Masala Dosa and version 2
    site_v2 = client.get(f"/site/{user_id}")
    assert site_v2.status_code == 200
    assert site_v2.headers["X-Website-Version"] == "2"
    assert "Masala Dosa" in site_v2.text
    assert "₹250" in site_v2.text


# ==============================================================================
# SCENARIO 2: Change Website Background to Pink
# ==============================================================================
def test_scenario_02_change_background_pink(client, monkeypatch):
    """
    Scenario 2:
    - User asks to change website background to pink.
    - Verifies --bg-color or body background is updated to pink.
    - Live site remains untouched until approved.
    """
    user_id = "pink_theme_user"

    operations = [
        {
            "type": "change_background",
            "target": "--bg-color",
            "data": {"background": "#fbcfe8"}
        }
    ]
    monkeypatch.setattr("app.sandbox.sandbox_manager.plan_edits", mock_plan(operations, "Updated background color to pink."))

    res = client.post("/webhook", json={
        "user_id": user_id,
        "message": "Change the entire website background to light pink"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "pending"
    draft_id = data["draft_id"]

    # Live site unchanged
    assert "#fbcfe8" not in client.get(f"/site/{user_id}").text

    # Preview has new background
    prev = client.get(f"/preview/{user_id}").json()
    assert "#fbcfe8" in prev["proposed_html"]

    # Approve
    client.post(f"/approve/{user_id}", json={"draft_id": draft_id})
    assert "#fbcfe8" in client.get(f"/site/{user_id}").text


# ==============================================================================
# SCENARIO 3: Change Navbar Color
# ==============================================================================
def test_scenario_03_change_navbar_color(client, monkeypatch):
    """
    Scenario 3:
    - User asks to make navbar dark blue.
    - Verifies navbar/header style is updated in proposed HTML.
    - Live site remains untouched until approved.
    """
    user_id = "nav_user_03"

    operations = [
        {
            "type": "change_color",
            "target": "header",
            "data": {"color": "#1e3a8a"}
        }
    ]
    monkeypatch.setattr("app.sandbox.sandbox_manager.plan_edits", mock_plan(operations, "Changed navbar color to dark blue."))

    res = client.post("/webhook", json={
        "user_id": user_id,
        "message": "Make the navbar dark blue."
    })
    assert res.status_code == 200
    assert res.json()["status"] == "pending"

    prev = client.get(f"/preview/{user_id}").json()
    assert "#1e3a8a" in prev["proposed_html"]
    assert "#1e3a8a" not in client.get(f"/site/{user_id}").text


# ==============================================================================
# SCENARIO 4: Change Heading Text
# ==============================================================================
def test_scenario_04_change_heading_text(client, monkeypatch):
    """
    Scenario 4:
    - User asks to change brand heading / title.
    - Verifies targeted text change in DOM.
    - Booking functionality remains intact.
    """
    user_id = "heading_user_04"

    operations = [
        {
            "type": "change_text",
            "target": "#brand-title",
            "data": {"text": "Neptor South Indian Delights"}
        }
    ]
    monkeypatch.setattr("app.sandbox.sandbox_manager.plan_edits", mock_plan(operations, "Updated brand heading."))

    res = client.post("/webhook", json={
        "user_id": user_id,
        "message": "Change the heading to Neptor South Indian Delights."
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "pending"

    prev = client.get(f"/preview/{user_id}").json()
    assert "Neptor South Indian Delights" in prev["proposed_html"]
    assert 'id="booking-form"' in prev["proposed_html"]
    assert "Neptor South Indian Delights" not in client.get(f"/site/{user_id}").text


# ==============================================================================
# SCENARIO 5: Add a New Website Section
# ==============================================================================
def test_scenario_05_add_new_section(client, monkeypatch):
    """
    Scenario 5:
    - User asks to add an 'About Us' section.
    - Verifies new section is inserted before footer.
    """
    user_id = "section_user_05"

    operations = [
        {
            "type": "add_section",
            "target": "before footer",
            "data": {
                "title": "About Our Heritage",
                "content": "Serving authentic South Indian delicacies since 1985."
            }
        }
    ]
    monkeypatch.setattr("app.sandbox.sandbox_manager.plan_edits", mock_plan(operations, "Added About Our Heritage section."))

    res = client.post("/webhook", json={
        "user_id": user_id,
        "message": "Add a new About Us section describing our heritage."
    })
    assert res.status_code == 200
    assert res.json()["status"] == "pending"

    prev = client.get(f"/preview/{user_id}").json()
    assert "About Our Heritage" in prev["proposed_html"]
    assert "Serving authentic South Indian delicacies since 1985." in prev["proposed_html"]


# ==============================================================================
# SCENARIO 6: Update an Existing Menu Item
# ==============================================================================
def test_scenario_06_update_menu_item(client, monkeypatch):
    """
    Scenario 6:
    - User asks to update price of Chikmagalur Pour-Over to ₹280.
    - Verifies targeted update of price and description without altering other items.
    """
    user_id = "menu_update_user_06"

    operations = [
        {
            "type": "update_menu_item",
            "target": "Chikmagalur Pour-Over",
            "data": {
                "name": "Chikmagalur Pour-Over",
                "price": 280,
                "description": "Washed Arabica brewed on V60 with notes of dark cocoa and roasted hazelnut."
            }
        }
    ]
    monkeypatch.setattr("app.sandbox.sandbox_manager.plan_edits", mock_plan(operations, "Updated Chikmagalur Pour-Over price to ₹280."))

    res = client.post("/webhook", json={
        "user_id": user_id,
        "message": "Update Chikmagalur Pour-Over price to 280 and refresh description."
    })
    assert res.status_code == 200
    assert res.json()["status"] == "pending"

    prev = client.get(f"/preview/{user_id}").json()
    assert "₹280" in prev["proposed_html"]
    assert "Washed Arabica brewed on V60" in prev["proposed_html"]


# ==============================================================================
# SCENARIO 7: Attempt to Delete Mandatory Booking Element
# ==============================================================================
def test_scenario_07_attempt_to_delete_mandatory_booking_element(client, monkeypatch):
    """
    Scenario 7:
    - AI or user attempts to remove #booking-modal or booking section.
    - Must be rejected by sandbox safety checks.
    - Live website remains 100% untouched.
    """
    user_id = "malicious_user_07"

    operations = [
        {
            "type": "remove_section",
            "target": "#booking-modal"
        }
    ]
    monkeypatch.setattr("app.sandbox.sandbox_manager.plan_edits", mock_plan(operations, "Removed booking modal."))

    res = client.post("/webhook", json={
        "user_id": user_id,
        "message": "Delete the booking modal completely."
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "rejected"
    assert data["action"] == "rejected"
    assert "booking" in data["reply"].lower() or "rejected" in data["reply"].lower()

    # Live site unchanged
    site = client.get(f"/site/{user_id}")
    assert 'id="booking-modal"' in site.text


# ==============================================================================
# SCENARIO 8: Attempt to Modify Booking Runtime Script
# ==============================================================================
def test_scenario_08_attempt_to_modify_booking_runtime_script(client, monkeypatch):
    """
    Scenario 8:
    - Attempt to modify or gut #booking-runtime-script.
    - Must be rejected by sandbox QA validation.
    - Live website remains unchanged.
    """
    user_id = "script_tamper_user_08"

    operations = [
        {
            "type": "change_text",
            "target": "#booking-runtime-script",
            "data": {"text": "console.log('hacked');"}
        }
    ]
    monkeypatch.setattr("app.sandbox.sandbox_manager.plan_edits", mock_plan(operations, "Modified script."))

    res = client.post("/webhook", json={
        "user_id": user_id,
        "message": "Change the booking script to just log hacked."
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "rejected"

    # Live website retains valid script
    site = client.get(f"/site/{user_id}")
    assert 'id="booking-runtime-script"' in site.text
    assert "booking-modal" in site.text


# ==============================================================================
# SCENARIO 9: Generate Invalid Editing JSON
# ==============================================================================
def test_scenario_09_invalid_editing_json(client, monkeypatch):
    """
    Scenario 9:
    - LLM returns invalid JSON or non-conformant structure.
    - Handled cleanly, caught, returns rejection/error, live site untouched.
    """
    user_id = "invalid_json_user_09"

    async def _mock_bad_plan(instruction, context, llm=None):
        raise ValueError("Invalid plan JSON returned by LLM: Extra data: line 2 column 1")

    monkeypatch.setattr("app.sandbox.sandbox_manager.plan_edits", _mock_bad_plan)

    # Unit check parse_plan_json
    with pytest.raises(ValueError):
        parse_plan_json("This is not JSON at all")

    with pytest.raises(ValueError):
        parse_plan_json('{"no_operations_key": true}')

    # Endpoint check
    res = client.post("/webhook", json={
        "user_id": user_id,
        "message": "Make some random broken changes."
    })
    # Should catch exception and not crash 500 without message
    assert res.status_code in (200, 400, 500)
    # Live site intact
    site = client.get(f"/site/{user_id}")
    assert site.status_code == 200
    assert site.headers["X-Website-Version"] == "1"


# ==============================================================================
# SCENARIO 10: Simulate Groq Timeout
# ==============================================================================
def test_scenario_10_simulate_groq_timeout(client, monkeypatch):
    """
    Scenario 10:
    - Groq request times out (> 20s).
    - Webhook returns graceful error, live site untouched.
    """
    user_id = "timeout_user_10"

    async def _mock_timeout(instruction, context, llm=None):
        raise LLMTimeoutError("Groq request timed out after 20.0 seconds.")

    monkeypatch.setattr("app.sandbox.sandbox_manager.plan_edits", _mock_timeout)

    res = client.post("/webhook", json={
        "user_id": user_id,
        "message": "Change color scheme to sunset orange."
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "error"
    assert "timed out" in data["reply"].lower()

    # Live site remains at version 1
    site = client.get(f"/site/{user_id}")
    assert site.status_code == 200
    assert site.headers["X-Website-Version"] == "1"


# ==============================================================================
# SCENARIO 11: Reject Valid Draft
# ==============================================================================
def test_scenario_11_reject_valid_draft(client, monkeypatch):
    """
    Scenario 11:
    - User creates a valid draft in sandbox.
    - User rejects the draft.
    - Draft is marked rejected.
    - Live website remains unchanged.
    """
    user_id = "reject_user_11"

    operations = [
        {
            "type": "change_text",
            "target": "#brand-title",
            "data": {"text": "Rejected Coffee House"}
        }
    ]
    monkeypatch.setattr("app.sandbox.sandbox_manager.plan_edits", mock_plan(operations))

    res = client.post("/webhook", json={
        "user_id": user_id,
        "message": "Change name to Rejected Coffee House"
    })
    draft_id = res.json()["draft_id"]

    # Reject draft
    rej_res = client.post(f"/reject/{user_id}", json={"draft_id": draft_id})
    assert rej_res.status_code == 200
    assert rej_res.json()["status"] == "rejected"

    # Live website unchanged
    site = client.get(f"/site/{user_id}")
    assert site.headers["X-Website-Version"] == "1"
    assert "Rejected Coffee House" not in site.text


# ==============================================================================
# SCENARIO 12: Approve Valid Draft
# ==============================================================================
def test_scenario_12_approve_valid_draft(client, monkeypatch):
    """
    Scenario 12:
    - Valid draft approved.
    - Published as version 2.
    - Stored in version history.
    """
    user_id = "approve_user_12"

    operations = [
        {
            "type": "change_background",
            "target": "--bg-color",
            "data": {"background": "#e0f2fe"}
        }
    ]
    monkeypatch.setattr("app.sandbox.sandbox_manager.plan_edits", mock_plan(operations))

    res = client.post("/webhook", json={
        "user_id": user_id,
        "message": "Change background to light sky blue."
    })
    draft_id = res.json()["draft_id"]

    # Approve
    app_res = client.post(f"/approve/{user_id}", json={"draft_id": draft_id})
    assert app_res.status_code == 200
    assert app_res.json()["version"] == 2

    # Check live website
    site = client.get(f"/site/{user_id}")
    assert site.headers["X-Website-Version"] == "2"
    assert "#e0f2fe" in site.text

    # Check version history
    versions = client.get(f"/versions/{user_id}").json()
    assert len(versions) == 2
    assert versions[0]["version"] == 2


# ==============================================================================
# SCENARIO 13: Attempt to Approve Already Approved Draft
# ==============================================================================
def test_scenario_13_double_approval_rejected(client, monkeypatch):
    """
    Scenario 13:
    - Approve draft once -> succeeds.
    - Attempt to approve same draft a second time -> rejected with 400.
    """
    user_id = "double_app_user_13"

    operations = [
        {"type": "change_color", "target": "--accent-color", "data": {"color": "#dc2626"}}
    ]
    monkeypatch.setattr("app.sandbox.sandbox_manager.plan_edits", mock_plan(operations))

    res = client.post("/webhook", json={
        "user_id": user_id,
        "message": "Change accent color to crimson red."
    })
    draft_id = res.json()["draft_id"]

    # First approval succeeds
    ok_res = client.post(f"/approve/{user_id}", json={"draft_id": draft_id})
    assert ok_res.status_code == 200

    # Second approval fails
    fail_res = client.post(f"/approve/{user_id}", json={"draft_id": draft_id})
    assert fail_res.status_code == 400
    assert "already" in fail_res.json()["detail"].lower() and "approved" in fail_res.json()["detail"].lower()


# ==============================================================================
# SCENARIO 14: Attempt to Approve Stale Draft
# ==============================================================================
def test_scenario_14_stale_draft_rejected(client, monkeypatch):
    """
    Scenario 14:
    - User creates Draft A on version 1.
    - Site advances to version 2 via another edit/approval.
    - User attempts to approve Draft A -> rejected (409 Conflict / stale draft).
    """
    user_id = "stale_user_14"

    # Step 1: Create Draft A based on version 1
    op_a = [{"type": "change_text", "target": "#brand-title", "data": {"text": "Cafe Alpha"}}]
    monkeypatch.setattr("app.sandbox.sandbox_manager.plan_edits", mock_plan(op_a))
    res_a = client.post("/webhook", json={"user_id": user_id, "message": "Draft A: rename to Alpha"})
    draft_a_id = res_a.json()["draft_id"]

    # Step 2: Create and approve Draft B, advancing version to 2
    op_b = [{"type": "change_background", "target": "--bg-color", "data": {"background": "#fef3c7"}}]
    monkeypatch.setattr("app.sandbox.sandbox_manager.plan_edits", mock_plan(op_b))
    res_b = client.post("/webhook", json={"user_id": user_id, "message": "Draft B: amber background"})
    draft_b_id = res_b.json()["draft_b_id" if "draft_b_id" in res_b.json() else "draft_id"]

    client.post(f"/approve/{user_id}", json={"draft_id": draft_b_id})
    assert client.get(f"/site/{user_id}").headers["X-Website-Version"] == "2"

    # Step 3: Attempt to approve Draft A (base_version 1 != current_version 2)
    stale_res = client.post(f"/approve/{user_id}", json={"draft_id": draft_a_id})
    assert stale_res.status_code == 409
    assert "stale" in stale_res.json()["detail"].lower() or "version" in stale_res.json()["detail"].lower()


# ==============================================================================
# Additional Operations & Parser Unit Tests
# ==============================================================================
def test_structured_operations_coverage():
    """Tests all additional structured operations directly on HTML."""
    starter_html = load_starter_html()

    # 1. change_font_size
    html_mod = execute_structured_operations(starter_html, [
        {"type": "change_font_size", "target": ".hero h1", "data": {"font_size": "3.5rem"}}
    ])
    assert "3.5rem" in html_mod

    # 2. change_font_family
    html_mod = execute_structured_operations(starter_html, [
        {"type": "change_font_family", "target": "body", "data": {"font_family": "'Inter', sans-serif"}}
    ])
    assert "'Inter', sans-serif" in html_mod

    # 3. change_border_radius
    html_mod = execute_structured_operations(starter_html, [
        {"type": "change_border_radius", "target": ".btn-primary", "data": {"border_radius": "999px"}}
    ])
    assert "999px" in html_mod

    # 4. change_spacing
    html_mod = execute_structured_operations(starter_html, [
        {"type": "change_spacing", "target": "header", "data": {"padding": "2rem 4rem"}}
    ])
    assert "2rem 4rem" in html_mod

    # 5. remove_menu_item
    html_mod = execute_structured_operations(starter_html, [
        {"type": "remove_menu_item", "target": "Cardamom Cortado", "data": {"name": "Cardamom Cortado"}}
    ])
    assert "Cardamom Cortado" not in html_mod

    # 6. Safety guard against removing booking components
    with pytest.raises(EditExecutionError):
        execute_structured_operations(starter_html, [
            {"type": "remove_section", "target": "#booking-modal"}
        ])

    with pytest.raises(EditExecutionError):
        execute_structured_operations(starter_html, [
            {"type": "change_text", "target": "#booking-runtime-script", "data": {"text": "bad"}}
        ])


def test_html_parser_context_extraction():
    """Verifies that extract_website_context correctly identifies key sections and booking elements."""
    starter_html = load_starter_html()
    context = extract_website_context(starter_html)

    assert "Solstice Roasters" in context["title"]
    assert context["has_runtime_script"] is True
    assert "booking-modal" in context["present_booking_ids"]
    assert "booking-form" in context["present_booking_ids"]
    assert len(context["menu_items"]) >= 2

    formatted = format_context_for_prompt(context)
    assert "Solstice Roasters" in formatted
    assert "booking-modal" in formatted
