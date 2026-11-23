import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.auth import seed_initial_admin
import app.config as config


@pytest.fixture
def auth_client(setup_test_db):
    """Provides a TestClient with initial admin account seeded."""
    seed_initial_admin(setup_test_db)
    with TestClient(app) as test_client:
        yield test_client


def test_admin_login_redirect_unauthenticated(auth_client):
    """Unauthenticated users visiting /admin/dashboard must be redirected to /admin/login."""
    res = auth_client.get("/admin/dashboard", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == "/admin/login"


def test_admin_login_page_renders(auth_client):
    """GET /admin/login must serve the login HTML."""
    res = auth_client.get("/admin/login")
    assert res.status_code == 200
    assert "Neptor Technologies" in res.text
    assert "Admin Console" in res.text


def test_admin_login_invalid_credentials(auth_client):
    """Invalid credentials return 401 Unauthorized."""
    res = auth_client.post("/login", json={"email": "admin@neptor.in", "password": "wrongpassword"})
    assert res.status_code == 401
    assert "Invalid email or password" in res.json()["detail"]


def test_admin_login_success_and_session_cookie(auth_client):
    """Successful login returns 200 and sets HTTP-only session cookie."""
    res = auth_client.post("/login", json={"email": "admin@neptor.in", "password": "neptor123"})
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert config.SESSION_COOKIE_NAME in res.cookies


def test_admin_dashboard_authenticated(auth_client):
    """Authenticated admin can access /admin/dashboard and get 200."""
    # Login first
    login_res = auth_client.post("/login", json={"email": "admin@neptor.in", "password": "neptor123"})
    assert login_res.status_code == 200

    dash_res = auth_client.get("/admin/dashboard")
    assert dash_res.status_code == 200
    assert "AI Website Editor" in dash_res.text
    assert "admin@neptor.in" in dash_res.text


def test_admin_login_page_redirects_if_already_logged_in(auth_client):
    """Visiting /admin/login when already authenticated redirects to /admin/dashboard."""
    auth_client.post("/login", json={"email": "admin@neptor.in", "password": "neptor123"})

    login_page_res = auth_client.get("/admin/login", follow_redirects=False)
    assert login_page_res.status_code == 302
    assert login_page_res.headers["location"] == "/admin/dashboard"


def test_admin_change_password_workflow(auth_client):
    """Admin can change password; old password stops working and new password works."""
    auth_client.post("/login", json={"email": "admin@neptor.in", "password": "neptor123"})

    # Fails with wrong old password
    fail_res = auth_client.post("/admin/change-password", json={
        "old_password": "wrongOldPassword",
        "new_password": "newPassword789"
    })
    assert fail_res.status_code == 400

    # Succeeds with correct old password
    success_res = auth_client.post("/admin/change-password", json={
        "old_password": "neptor123",
        "new_password": "newPassword789"
    })
    assert success_res.status_code == 200
    assert "successfully updated" in success_res.json()["message"].lower()

    # Logout
    auth_client.post("/logout")

    # Old password no longer works
    old_try = auth_client.post("/login", json={"email": "admin@neptor.in", "password": "neptor123"})
    assert old_try.status_code == 401

    # New password works
    new_try = auth_client.post("/login", json={"email": "admin@neptor.in", "password": "newPassword789"})
    assert new_try.status_code == 200


def test_admin_logout_terminates_session(auth_client):
    """Logging out clears session and prevents access to protected routes."""
    auth_client.post("/login", json={"email": "admin@neptor.in", "password": "neptor123"})
    assert auth_client.get("/admin/dashboard").status_code == 200

    # Logout
    logout_res = auth_client.post("/logout")
    assert logout_res.status_code == 200

    # Access is now forbidden / redirects to login
    protected_res = auth_client.get("/admin/dashboard", follow_redirects=False)
    assert protected_res.status_code == 302
    assert protected_res.headers["location"] == "/admin/login"


def test_admin_conversations_api(auth_client):
    """Admin can create and list editing conversations."""
    auth_client.post("/login", json={"email": "admin@neptor.in", "password": "neptor123"})

    # Create conversation
    create_res = auth_client.post("/admin/conversations", json={"title": "Pink Theme Redesign"})
    assert create_res.status_code == 200
    conv_data = create_res.json()
    assert conv_data["title"] == "Pink Theme Redesign"
    conv_id = conv_data["id"]

    # List conversations
    list_res = auth_client.get("/admin/conversations")
    assert list_res.status_code == 200
    conv_list = list_res.json()
    assert any(c["id"] == conv_id for c in conv_list)

    # Save and get chat message
    msg_res = auth_client.post(f"/admin/conversations/{conv_id}/messages", json={"message": "Change navbar to dark blue"})
    assert msg_res.status_code == 200

    get_msgs = auth_client.get(f"/admin/conversations/{conv_id}/messages")
    assert get_msgs.status_code == 200
    assert len(get_msgs.json()) >= 1


def test_version_history_and_restore(auth_client, monkeypatch):
    """Verifies version history retrieval and atomic restoration."""
    user_id = "admin@neptor.in"

    # Start with version 1
    v1_site = auth_client.get(f"/site/{user_id}")
    assert v1_site.status_code == 200

    # Publish version 2
    async def mock_llm(instruction, current_html, llm=None):
        return current_html.replace("--bg-color: #000000;", "--bg-color: #fbcfe8;") # pink

    monkeypatch.setattr("app.main.generate_modified_html", mock_llm)
    wh = auth_client.post("/webhook", json={"user_id": user_id, "message": "Change website theme to pink"})
    draft_id = wh.json()["draft_id"]
    auth_client.post(f"/approve/{user_id}", json={"draft_id": draft_id})

    # Site is now version 2
    assert auth_client.get(f"/site/{user_id}").headers["X-Website-Version"] == "2"

    # Get version history
    v_res = auth_client.get(f"/versions/{user_id}")
    assert v_res.status_code == 200
    versions = v_res.json()
    assert len(versions) == 2
    assert versions[0]["version"] == 2
    assert versions[1]["version"] == 1

    # Get historical HTML
    html_res = auth_client.get(f"/versions/{user_id}/1")
    assert html_res.status_code == 200
    assert "--bg-color: #000000;" in html_res.text

    # Restore version 1
    restore_res = auth_client.post(f"/versions/{user_id}/restore/1")
    assert restore_res.status_code == 200
    rdata = restore_res.json()
    assert rdata["status"] == "restored"
    assert rdata["version"] == 3

    # Site is now restored to original background
    restored_site = auth_client.get(f"/site/{user_id}")
    assert restored_site.headers["X-Website-Version"] == "3"
    assert "--bg-color: #000000;" in restored_site.text


def test_complete_admin_dashboard_workflow(auth_client, monkeypatch):
    """
    Simulates Section 10 complete flow:
    1. Admin logs in with credentials.
    2. Admin enters dashboard.
    3. Admin types: 'Change the website theme to pink.'
    4. Webhook generates draft via Groq LLM.
    5. Admin reviews preview before publishing.
    6. Admin accepts changes.
    7. Public website displays the new pink theme.
    8. Version history records the modification.
    """
    # 1. Login
    login_res = auth_client.post("/login", json={"email": "admin@neptor.in", "password": "neptor123"})
    assert login_res.status_code == 200

    # 2. Enter dashboard
    dash_res = auth_client.get("/admin/dashboard")
    assert dash_res.status_code == 200

    # 3. Request theme change
    async def mock_pink(instruction, current_html, llm=None):
        return current_html.replace("--bg-color: #000000;", "--bg-color: #fce7f3;")

    monkeypatch.setattr("app.main.generate_modified_html", mock_pink)

    wh_res = auth_client.post("/webhook", json={
        "user_id": "admin@neptor.in",
        "message": "Change the entire website theme to pink."
    })
    assert wh_res.status_code == 200
    wh_data = wh_res.json()
    assert wh_data["status"] == "pending"
    draft_id = wh_data["draft_id"]

    # 4. Public website is still version 1 (unmodified)
    public_site = auth_client.get("/site/admin@neptor.in")
    assert public_site.headers["X-Website-Version"] == "1"
    assert "--bg-color: #000000;" in public_site.text

    # 5. Admin retrieves preview
    prev_res = auth_client.get("/preview/admin@neptor.in")
    assert prev_res.status_code == 200
    assert prev_res.json()["draft_id"] == draft_id
    assert "#fce7f3" in prev_res.json()["proposed_html"]

    # 6. Admin accepts changes
    app_res = auth_client.post("/approve/admin@neptor.in", json={"draft_id": draft_id})
    assert app_res.status_code == 200
    assert app_res.json()["version"] == 2

    # 7. Public website displays new pink theme
    live_site = auth_client.get("/site/admin@neptor.in")
    assert live_site.headers["X-Website-Version"] == "2"
    assert "#fce7f3" in live_site.text

    # 8. Version history records the modification
    versions = auth_client.get("/versions/admin@neptor.in").json()
    assert len(versions) == 2
    assert versions[0]["version"] == 2


def test_concurrency1_user_and_strict_isolation(client, monkeypatch):
    """
    Verifies that:
    1. 'concurrency1' can authenticate with password 'neptor123'.
    2. 'concurrency1' and 'admin@neptor.in' have completely independent websites.
    3. Changes made and published by 'concurrency1' NEVER leak or alter 'admin@neptor.in'.
    4. Drafts created by 'concurrency1' are isolated from 'admin@neptor.in'.
    """
    # 1. Authenticate concurrency1
    login_res = client.post("/login", json={"email": "concurrency1", "password": "neptor123"})
    assert login_res.status_code == 200
    assert login_res.json()["status"] == "ok"

    # Also test concurrency1@neptor.in
    login_res2 = client.post("/login", json={"email": "concurrency1@neptor.in", "password": "neptor123"})
    assert login_res2.status_code == 200

    # 2. Both users start with version 1 baseline sites
    site_admin = client.get("/site/admin@neptor.in")
    assert site_admin.status_code == 200
    assert site_admin.headers["X-Website-Version"] == "1"

    site_c1 = client.get("/site/concurrency1")
    assert site_c1.status_code == 200
    assert site_c1.headers["X-Website-Version"] == "1"

    # 3. concurrency1 requests an edit (e.g. emerald accent color)
    async def mock_concurrency_edit(instruction, current_html, llm=None):
        return current_html.replace("--accent-color: #00e5ff;", "--accent-color: #10b981;")

    monkeypatch.setattr("app.main.generate_modified_html", mock_concurrency_edit)

    wh_c1 = client.post("/webhook", json={
        "user_id": "concurrency1",
        "message": "Change accent color to emerald"
    })
    assert wh_c1.status_code == 200
    data_c1 = wh_c1.json()
    assert data_c1["status"] == "pending"
    draft_c1_id = data_c1["draft_id"]

    # 4. admin@neptor.in has NO pending draft
    prev_admin = client.get("/preview/admin@neptor.in")
    assert prev_admin.status_code == 404

    # 5. concurrency1 approves draft -> becomes version 2
    app_res = client.post("/approve/concurrency1", json={"draft_id": draft_c1_id})
    assert app_res.status_code == 200
    assert app_res.json()["version"] == 2

    # 6. Verify concurrency1 site is version 2 with #10b981
    c1_live = client.get("/site/concurrency1")
    assert c1_live.headers["X-Website-Version"] == "2"
    assert "#10b981" in c1_live.text

    # 7. CRITICAL ISOLATION: admin@neptor.in's site remains 100% untouched at version 1!
    admin_live = client.get("/site/admin@neptor.in")
    assert admin_live.headers["X-Website-Version"] == "1"
    assert "#10b981" not in admin_live.text
    assert "#00e5ff" in admin_live.text
