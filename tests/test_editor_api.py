import asyncio
import pytest
from fastapi.testclient import TestClient
from app.database import (
    get_or_create_user_site,
    get_pending_draft,
    load_starter_html,
)
from app.llm_service import LLMTimeoutError


def mock_successful_llm(new_title: str = "Midnight Roasters"):
    """Returns a mock generator that replaces title and accent color in starter site."""
    async def _mock(instruction, current_html, llm=None):
        return current_html.replace(
            "Solstice Roasters", new_title
        ).replace(
            "--accent-color: #b45309;", "--accent-color: #047857;"
        )
    return _mock


def mock_invalid_html():
    """Returns a mock generator that outputs invalid HTML (no closing tags, broken structure)."""
    async def _mock(instruction, current_html, llm=None):
        return "<div>Invalid incomplete snippet without head or body</div>"
    return _mock


def mock_missing_booking_ids():
    """Returns a mock generator that strips mandatory booking modal IDs."""
    async def _mock(instruction, current_html, llm=None):
        # Gut the booking elements
        cleaned = current_html.replace('id="booking-modal"', 'id="old-modal"')
        cleaned = cleaned.replace('id="booking-form"', 'id="old-form"')
        return cleaned
    return _mock


def mock_missing_script():
    """Returns a mock generator that removes #booking-runtime-script."""
    async def _mock(instruction, current_html, llm=None):
        cleaned = current_html.replace('id="booking-runtime-script"', 'id="other-script"')
        return cleaned
    return _mock


def mock_timeout_llm():
    """Returns a mock generator that simulates LLM timeout."""
    async def _mock(instruction, current_html, llm=None):
        raise LLMTimeoutError("LLM request timed out after 20.0 seconds.")
    return _mock


# ==============================================================================
# TEST 1: Successful Draft Generation
# ==============================================================================
def test_1_successful_draft_generation(client, monkeypatch):
    monkeypatch.setattr("app.main.generate_modified_html", mock_successful_llm("Midnight Roasters"))

    user_id = "test_user_01"
    response = client.post("/webhook", json={
        "user_id": user_id,
        "message": "Change the cafe name to Midnight Roasters and accent color to dark emerald."
    })

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "pending"
    assert data["action"] == "edit"
    assert data["draft_id"] is not None
    assert data["version"] == 1
    assert "Midnight Roasters" in data["change_summary"]

    # Live website MUST remain unchanged at version 1
    site_res = client.get(f"/site/{user_id}")
    assert site_res.status_code == 200
    assert "Solstice Roasters" in site_res.text
    assert "Midnight Roasters" not in site_res.text


# ==============================================================================
# TEST 2: Successful HTML Preview Retrieval
# ==============================================================================
def test_2_successful_html_preview_retrieval(client, monkeypatch):
    monkeypatch.setattr("app.main.generate_modified_html", mock_successful_llm("Midnight Roasters"))

    user_id = "test_user_02"
    webhook_res = client.post("/webhook", json={
        "user_id": user_id,
        "message": "Change title to Midnight Roasters"
    })
    draft_id = webhook_res.json()["draft_id"]

    # 1. JSON Preview retrieval
    preview_res = client.get(f"/preview/{user_id}")
    assert preview_res.status_code == 200
    pdata = preview_res.json()
    assert pdata["draft_id"] == draft_id
    assert pdata["user_id"] == user_id
    assert "Midnight Roasters" in pdata["instruction"] or "Midnight Roasters" in pdata["user_instruction"]
    assert "Midnight Roasters" in pdata["proposed_html"]
    assert "Solstice Roasters" in pdata["original_html"]
    assert "html_diff" in pdata
    assert pdata["base_version"] == 1
    assert pdata["status"] == "pending"
    assert pdata["validation_results"]["is_valid"] is True

    # 2. HTML View Preview retrieval
    html_preview_res = client.get(f"/preview/{user_id}?format=html")
    assert html_preview_res.status_code == 200
    assert "text/html" in html_preview_res.headers["content-type"]
    assert "AI Preview & Approval System" in html_preview_res.text
    assert draft_id in html_preview_res.text

    # 3. Sandboxed render endpoint
    render_res = client.get(f"/preview/{user_id}/render")
    assert render_res.status_code == 200
    assert "Midnight Roasters" in render_res.text


# ==============================================================================
# TEST 3: Successful Approval and Publishing
# ==============================================================================
def test_3_successful_approval_and_publishing(client, monkeypatch):
    monkeypatch.setattr("app.main.generate_modified_html", mock_successful_llm("Midnight Roasters"))

    user_id = "test_user_03"
    webhook_res = client.post("/webhook", json={
        "user_id": user_id,
        "message": "Change title to Midnight Roasters"
    })
    draft_id = webhook_res.json()["draft_id"]

    # Before approval: live site is version 1
    site_v1 = client.get(f"/site/{user_id}")
    assert site_v1.headers["X-Website-Version"] == "1"
    assert "Solstice Roasters" in site_v1.text

    # Approve draft
    app_res = client.post(f"/approve/{user_id}", json={"draft_id": draft_id})
    assert app_res.status_code == 200
    adata = app_res.json()
    assert adata["status"] == "approved"
    assert adata["version"] == 2
    assert adata["draft_id"] == draft_id

    # After approval: live site is published version 2
    site_v2 = client.get(f"/site/{user_id}")
    assert site_v2.headers["X-Website-Version"] == "2"
    assert "Midnight Roasters" in site_v2.text


# ==============================================================================
# TEST 4: Successful Rejection Without Changing Live HTML
# ==============================================================================
def test_4_successful_rejection_without_changing_live_html(client, monkeypatch):
    monkeypatch.setattr("app.main.generate_modified_html", mock_successful_llm("Midnight Roasters"))

    user_id = "test_user_04"
    webhook_res = client.post("/webhook", json={
        "user_id": user_id,
        "message": "Change title to Midnight Roasters"
    })
    draft_id = webhook_res.json()["draft_id"]

    # Reject draft
    rej_res = client.post(f"/reject/{user_id}", json={"draft_id": draft_id})
    assert rej_res.status_code == 200
    rdata = rej_res.json()
    assert rdata["status"] == "rejected"
    assert rdata["version"] == 1
    assert rdata["draft_id"] == draft_id

    # Live website MUST remain version 1 with original title
    site_res = client.get(f"/site/{user_id}")
    assert site_res.headers["X-Website-Version"] == "1"
    assert "Solstice Roasters" in site_res.text
    assert "Midnight Roasters" not in site_res.text


# ==============================================================================
# TEST 5: Invalid HTML Cannot Become a Pending Draft
# ==============================================================================
def test_5_invalid_html_cannot_become_a_pending_draft(client, monkeypatch):
    monkeypatch.setattr("app.main.generate_modified_html", mock_invalid_html())

    user_id = "test_user_05"
    webhook_res = client.post("/webhook", json={
        "user_id": user_id,
        "message": "Corrupt the HTML layout"
    })
    assert webhook_res.status_code == 200
    data = webhook_res.json()
    assert data["status"] == "rejected"
    assert data["action"] == "rejected"
    assert data["draft_id"] is None
    assert len(data["validation_errors"]) > 0

    # Ensure no pending draft was saved
    prev_res = client.get(f"/preview/{user_id}")
    assert prev_res.status_code == 404

    # Live site unchanged
    site_res = client.get(f"/site/{user_id}")
    assert site_res.headers["X-Website-Version"] == "1"


# ==============================================================================
# TEST 6: Missing Booking IDs Prevent Draft Creation
# ==============================================================================
def test_6_missing_booking_ids_prevent_draft_creation(client, monkeypatch):
    monkeypatch.setattr("app.main.generate_modified_html", mock_missing_booking_ids())

    user_id = "test_user_06"
    webhook_res = client.post("/webhook", json={
        "user_id": user_id,
        "message": "Remove the reservation modal"
    })
    assert webhook_res.status_code == 200
    data = webhook_res.json()
    assert data["status"] == "rejected"
    assert data["action"] == "rejected"
    assert data["draft_id"] is None
    assert any("booking" in err.lower() for err in data["validation_errors"])

    # Preview returns 404 because draft was rejected
    prev_res = client.get(f"/preview/{user_id}")
    assert prev_res.status_code == 404


# ==============================================================================
# TEST 7: Repeated Approval is Rejected
# ==============================================================================
def test_7_repeated_approval_is_rejected(client, monkeypatch):
    monkeypatch.setattr("app.main.generate_modified_html", mock_successful_llm("Midnight Roasters"))

    user_id = "test_user_07"
    wh = client.post("/webhook", json={"user_id": user_id, "message": "Change title"})
    draft_id = wh.json()["draft_id"]

    # First approval succeeds
    first_app = client.post(f"/approve/{user_id}", json={"draft_id": draft_id})
    assert first_app.status_code == 200
    assert first_app.json()["version"] == 2

    # Second approval of the same draft MUST be rejected
    second_app = client.post(f"/approve/{user_id}", json={"draft_id": draft_id})
    assert second_app.status_code in (400, 409)
    assert "already been approved" in second_app.json()["detail"].lower()


# ==============================================================================
# TEST 8: Stale Drafts Cannot Overwrite Newer Versions
# ==============================================================================
def test_8_stale_drafts_cannot_overwrite_newer_versions(client, monkeypatch):
    monkeypatch.setattr("app.main.generate_modified_html", mock_successful_llm("Midnight Roasters"))

    user_id = "test_user_08"
    # Draft 1 based on version 1
    wh1 = client.post("/webhook", json={"user_id": user_id, "message": "First edit"})
    draft_1 = wh1.json()["draft_id"]

    # Draft 2 creates a newer pending draft; explicitly supersedes draft 1
    monkeypatch.setattr("app.main.generate_modified_html", mock_successful_llm("Sunrise Roasters"))
    wh2 = client.post("/webhook", json={"user_id": user_id, "message": "Second edit"})
    draft_2 = wh2.json()["draft_id"]

    # Approve draft 2 -> moves live site to version 2
    app2 = client.post(f"/approve/{user_id}", json={"draft_id": draft_2})
    assert app2.status_code == 200
    assert app2.json()["version"] == 2

    # Attempting to approve draft 1 now MUST fail (it was based on version 1, now version 2)
    app1 = client.post(f"/approve/{user_id}", json={"draft_id": draft_1})
    assert app1.status_code == 409  # Conflict / Stale
    assert "stale" in app1.json()["detail"].lower()


# ==============================================================================
# TEST 9: Users Cannot Approve Another User's Draft
# ==============================================================================
def test_9_users_cannot_approve_another_users_draft(client, monkeypatch):
    monkeypatch.setattr("app.main.generate_modified_html", mock_successful_llm("Midnight Roasters"))

    user_a = "user_alice"
    user_b = "user_bob"

    wh = client.post("/webhook", json={"user_id": user_a, "message": "Alice's edit"})
    alice_draft_id = wh.json()["draft_id"]

    # Bob attempts to approve Alice's draft
    bob_attempt = client.post(f"/approve/{user_b}", json={"draft_id": alice_draft_id})
    assert bob_attempt.status_code == 403
    assert "belong" in bob_attempt.json()["detail"].lower()


# ==============================================================================
# TEST 10: Concurrent Approval Requests Do Not Publish Twice
# ==============================================================================
@pytest.mark.anyio
async def test_10_concurrent_approval_requests_do_not_publish_twice(monkeypatch):
    import httpx
    from app.main import app
    monkeypatch.setattr("app.main.generate_modified_html", mock_successful_llm("Midnight Roasters"))

    user_id = "test_user_10"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        wh = await ac.post("/webhook", json={"user_id": user_id, "message": "Edit"})
        draft_id = wh.json()["draft_id"]

        # Fire two concurrent approvals simultaneously on the event loop
        res1, res2 = await asyncio.gather(
            ac.post(f"/approve/{user_id}", json={"draft_id": draft_id}),
            ac.post(f"/approve/{user_id}", json={"draft_id": draft_id}),
        )

        statuses = [res1.status_code, res2.status_code]
        assert 200 in statuses
        # Exactly one succeeds (200), the second must fail (400 or 409)
        assert (400 in statuses) or (409 in statuses)

        # Site version must be exactly 2
        site = await ac.get(f"/site/{user_id}")
        assert site.headers["X-Website-Version"] == "2"


# ==============================================================================
# TEST 11: Rejected Drafts Never Affect the Live Website
# ==============================================================================
def test_11_rejected_drafts_never_affect_live_website(client, monkeypatch):
    monkeypatch.setattr("app.main.generate_modified_html", mock_successful_llm("Midnight Roasters"))

    user_id = "test_user_11"
    wh = client.post("/webhook", json={"user_id": user_id, "message": "Edit"})
    draft_id = wh.json()["draft_id"]

    # Reject draft
    rej = client.post(f"/reject/{user_id}", json={"draft_id": draft_id})
    assert rej.status_code == 200

    # Live website unchanged
    site = client.get(f"/site/{user_id}")
    assert site.headers["X-Website-Version"] == "1"
    assert "Solstice Roasters" in site.text

    # Approving a rejected draft must fail
    app_rej = client.post(f"/approve/{user_id}", json={"draft_id": draft_id})
    assert app_rej.status_code in (400, 409)


# ==============================================================================
# TEST 12: Undo Restores the Previous Published Version
# ==============================================================================
def test_12_undo_restores_previous_published_version(client, monkeypatch):
    monkeypatch.setattr("app.main.generate_modified_html", mock_successful_llm("Midnight Roasters"))

    user_id = "test_user_12"
    # Publish version 2
    wh = client.post("/webhook", json={"user_id": user_id, "message": "Change to Midnight"})
    draft_id = wh.json()["draft_id"]
    client.post(f"/approve/{user_id}", json={"draft_id": draft_id})

    site_v2 = client.get(f"/site/{user_id}")
    assert site_v2.headers["X-Website-Version"] == "2"
    assert "Midnight Roasters" in site_v2.text

    # Send undo
    undo_res = client.post("/webhook", json={"user_id": user_id, "message": "undo"})
    assert undo_res.status_code == 200
    udata = undo_res.json()
    assert udata["status"] == "rolled_back"
    assert udata["action"] == "undo"
    assert udata["version"] == 1

    # Live site is restored to version 1
    site_restored = client.get(f"/site/{user_id}")
    assert site_restored.headers["X-Website-Version"] == "1"
    assert "Solstice Roasters" in site_restored.text


# ==============================================================================
# TEST 13: Final Verification Scenario (Section 12)
# ==============================================================================
def test_13_final_verification_scenario(client, monkeypatch):
    """
    Simulates Section 12 exact scenario:
    - User starts with website version 1.
    - User requests background color change.
    - AI generates modified HTML -> validated -> stored as pending draft.
    - GET /site/{user_id} still returns version 1.
    - GET /preview/{user_id} returns proposed changes.
    - User approves draft.
    - GET /site/{user_id} now returns version 2.
    - User requests another change -> generates draft.
    - User rejects draft -> live website remains version 2.
    - User sends 'undo' -> live website returns to version 1.
    """
    user_id = "cafe_scenario_user"

    # 1. User starts with website version 1
    v1_site = client.get(f"/site/{user_id}")
    assert v1_site.status_code == 200
    assert v1_site.headers["X-Website-Version"] == "1"

    # 2. User requests background color change
    monkeypatch.setattr(
        "app.main.generate_modified_html",
        lambda instruction, current_html, llm=None: asyncio.sleep(0, result=current_html.replace(
            "--bg-color: #000000;", "--bg-color: #f0fdf4;"
        ))
    )
    wh1 = client.post("/webhook", json={
        "user_id": user_id,
        "message": "Change the background color to soft light emerald"
    })
    assert wh1.status_code == 200
    wh1_data = wh1.json()
    assert wh1_data["status"] == "pending"
    draft1_id = wh1_data["draft_id"]

    # 3. GET /site/{user_id} still returns version 1
    assert client.get(f"/site/{user_id}").headers["X-Website-Version"] == "1"

    # 4. GET /preview/{user_id} returns proposed changes
    prev1 = client.get(f"/preview/{user_id}")
    assert prev1.status_code == 200
    assert prev1.json()["draft_id"] == draft1_id
    assert "#f0fdf4" in prev1.json()["proposed_html"]

    # 5. User approves draft
    app1 = client.post(f"/approve/{user_id}", json={"draft_id": draft1_id})
    assert app1.status_code == 200
    assert app1.json()["version"] == 2

    # 6. GET /site/{user_id} now returns version 2
    site_v2 = client.get(f"/site/{user_id}")
    assert site_v2.headers["X-Website-Version"] == "2"
    assert "#f0fdf4" in site_v2.text

    # 7. User requests another change -> generates draft
    monkeypatch.setattr(
        "app.main.generate_modified_html",
        lambda instruction, current_html, llm=None: asyncio.sleep(0, result=current_html.replace(
            "Solstice Roasters", "Aura Roasters"
        ))
    )
    wh2 = client.post("/webhook", json={
        "user_id": user_id,
        "message": "Change brand name to Aura Roasters"
    })
    assert wh2.status_code == 200
    draft2_id = wh2.json()["draft_id"]

    # 8. User rejects draft
    rej = client.post(f"/reject/{user_id}", json={"draft_id": draft2_id})
    assert rej.status_code == 200
    assert rej.json()["status"] == "rejected"

    # 9. Live website remains at version 2
    assert client.get(f"/site/{user_id}").headers["X-Website-Version"] == "2"

    # 10. User sends undo -> live website returns to version 1
    undo = client.post("/webhook", json={"user_id": user_id, "message": "undo"})
    assert undo.status_code == 200
    assert undo.json()["status"] == "rolled_back"
    assert undo.json()["version"] == 1

    site_final = client.get(f"/site/{user_id}")
    assert site_final.headers["X-Website-Version"] == "1"
    assert "--bg-color: #000000;" in site_final.text


# ==============================================================================
# TEST 14: Casual Chat and LLM Timeout
# ==============================================================================
def test_14_casual_chat_and_llm_timeout(client, monkeypatch):
    user_id = "test_user_14"

    # Casual chat
    chat_res = client.post("/webhook", json={"user_id": user_id, "message": "hi looks awesome thanks"})
    assert chat_res.status_code == 200
    assert chat_res.json()["status"] == "no_change"
    assert chat_res.json()["action"] == "chat"

    # LLM timeout
    monkeypatch.setattr("app.main.generate_modified_html", mock_timeout_llm())
    timeout_res = client.post("/webhook", json={"user_id": user_id, "message": "Make a big change"})
    assert timeout_res.status_code == 200
    tdata = timeout_res.json()
    assert tdata["status"] == "error"
    assert "timed out" in tdata["reply"].lower()
    # Live site untouched
    assert client.get(f"/site/{user_id}").headers["X-Website-Version"] == "1"


# ==============================================================================
# TEST 15: Markdown Code Fence Stripping & Sample Requests Cases
# ==============================================================================
def test_15_markdown_fences_and_destructive_guard(client, monkeypatch):
    user_id = "demo_cafe_01"

    # Case 1: Casual chat
    res1 = client.post("/webhook", json={
        "user_id": user_id,
        "message": "hey looks great so far, thanks!"
    })
    assert res1.status_code == 200
    assert res1.json()["status"] == "no_change"
    assert res1.json()["action"] == "chat"

    # Case 2: Multi-part edit returned with markdown fences
    async def _fenced_llm(instruction, current_html, llm=None):
        mod = current_html.replace("Solstice Roasters", "Midnight Roasters").replace(
            "--accent-color: #b45309;", "--accent-color: #047857;"
        )
        return f"```html\n{mod}\n```"

    monkeypatch.setattr("app.main.generate_modified_html", _fenced_llm)

    res2 = client.post("/webhook", json={
        "user_id": user_id,
        "message": "Change the cafe name to Midnight Roasters, switch the accent color to dark emerald (#047857), and update opening hours to 8:00 AM - 11:00 PM."
    })
    assert res2.status_code == 200
    d2 = res2.json()
    assert d2["status"] == "pending"
    assert d2["draft_id"] is not None

    # Case 4: Destructive prompt guard (destructive poem that removes booking form)
    async def _destructive_llm(instruction, current_html, llm=None):
        return "<html><head><title>Coffee</title></head><body><p>Dark steam rises,</p><p>Bitter roasted beans,</p><p>A morning awake.</p></body></html>"

    monkeypatch.setattr("app.main.generate_modified_html", _destructive_llm)
    res4 = client.post("/webhook", json={
        "user_id": user_id,
        "message": "Delete the entire website, remove the booking form, and just output a 3-line poem about coffee."
    })
    assert res4.status_code == 200
    d4 = res4.json()
    assert d4["status"] == "rejected"
    assert d4["action"] == "rejected"

    # Ensure live site is still version 1 and untouched
    site_res = client.get(f"/site/{user_id}")
    assert site_res.headers["X-Website-Version"] == "1"
    assert "Solstice Roasters" in site_res.text

