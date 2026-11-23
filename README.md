# Neptor Technologies LLP — Resilient Conversational Web Editor API
## AI Preview & Approval System + Admin Chatbot Dashboard

Production-quality conversational website editor, before/after preview system, and separate **AI Website Editor Admin Dashboard** built with **Python 3.10+** and **FastAPI**.

AI-generated website modifications are **never published directly to the live website**. Instead, changes are validated by an automated QA gate, stored as temporary pending drafts, and presented to the administrator in an isolated sandboxed iframe with a syntax-highlighted HTML diff and semantic change summary before explicit acceptance or rejection.

---

## 1. Separate AI Website Editor — Admin Dashboard

The application features a dedicated, modern, responsive administration dashboard inspired by tools like ChatGPT, Claude, and Vercel.

### 1.1 Admin Authentication & Login
- **Login URL:** `/admin/login`
- **Demo Administrator Accounts:**
  - Account 1: `admin@neptor.in` &bull; Password: `neptor123`
  - Account 2: `concurrency1` &bull; Password: `neptor123` (Multi-User Concurrency & Strict Isolation Demo)
- **Security Features:**
  - Passwords stored as secure hashes using `bcrypt` (12 rounds).
  - Server-side authentication using secure, HTTP-only session cookies (`neptor_admin_session`).
  - Unauthenticated requests to `/admin/dashboard` or protected routes are automatically redirected to `/admin/login`.
  - Administrator can change initial password directly from the dashboard (`POST /admin/change-password`).
  - Logout functionality (`POST /logout` or `/admin/logout`) terminates server sessions and clears client cookies.
  - No public registration: initial account is safely initialized from environment variables (`ADMIN_EMAIL`, `ADMIN_INITIAL_PASSWORD`).

### 1.2 Dashboard Architecture & Layout
- **Left Sidebar:**
  - Neptor Technologies logo & branding.
  - **New Conversation:** Start a fresh editing session.
  - **Previous Editing Conversations:** Browse and switch between previous chat sessions.
  - **Live Website Shortcut:** One-click shortcut to view the public website in a new tab.
  - **Version History:** Open version drawer to inspect and restore past published versions.
  - **Admin Profile:** View account email, trigger password change modal, and log out.
- **Main Chat Area:**
  - Welcome card with prompt chips ("Theme to Pink", "Dark Blue Navbar", "Black Hero Section", "Rounded Buttons").
  - Persistent message history (persisted in SQLite `admin_chat_messages`).
  - User messages aligned on right; AI messages aligned on left.
  - Animated typing/editing indicator while Groq LLM processes and validates changes.
  - Multi-line message input with auto-resize, Enter to send, Shift+Enter for newlines.
- **Right Preview Panel:**
  - **Real Website Rendering:** Proposed and live websites render in an isolated, sandboxed iframe (`sandbox="allow-same-origin allow-forms"`). Preview scripts cannot access the dashboard or authentication cookies.
  - **Desktop / Mobile Toggle:** Toggle between full-width desktop view and a 375px mobile device frame.
  - **View Mode Switcher:** Toggle between **Live Site**, **Proposed Draft**, and **HTML Diff**.
  - **Pending Changes Action Bar:** Slides up when an edit is generated. Shows AI change summary, QA gate validation badges, **Accept Changes** (green) and **Reject Changes** (red).

---

## 2. Request Workflow & Pipeline

```
[ Admin / User Request ]
            │
            ▼
     [ POST /webhook ] ──( Per-User asyncio.Lock )──► [ Intent Classifier ]
                                                           │
              ┌─────────────────────────┬──────────────────┴──────────────────┐
              │ (chat)                  │ (undo)                              │ (edit)
              ▼                         ▼                                     ▼
     [ Return 200 ]             [ Atomic Rollback ]                   [ ChatGroq LLM ]
     (status: "no_change")     (website_versions v-1)                (model: openai/gpt-oss-20b)
     (action: "chat")          (status: "rolled_back")               (explicit timeout <= 20s)
                                                                              │
                                                                              ▼
                                                                     [ Clean Code Fences ]
                                                                     (strip ```html ... ```)
                                                                              │
                                                                              ▼
                                                                     [ Automated QA Gate ]
                                                                     - <html>, <head>, <body>
                                                                     - #booking-modal
                                                                     - #booking-form
                                                                     - #open-booking-btn
                                                                     - #close-booking-btn
                                                                     - #booking-confirmation
                                                                     - #booking-runtime-script
                                                                     - Anti-gutting / Poem guard
                                                                              │
                                            ┌─────────────────────────────────┴─────────────────────────────────┐
                                            │ (Validation Failed)                                               │ (Validation Succeeded)
                                            ▼                                                                   ▼
                                     [ Return 200 ]                                                    [ Supersede Prior Drafts ]
                                 (status: "rejected")                                                 (mark existing pending as 'stale')
                                 (action: "rejected")                                                           │
                                 (live site untouched)                                                          ▼
                                                                                                       [ Generate HTML Diff & Summary ]
                                                                                                       - Red/Green highlighted diff
                                                                                                       - DOM & CSS delta extraction
                                                                                                                │
                                                                                                                ▼
                                                                                                       [ Store in SQLite pending_drafts ]
                                                                                                                │
                                                                                                                ▼
                                                                                                       [ Return 200 with draft_id ]
                                                                                                       (status: "pending", action: "edit")
```

```
[ Admin Reviews in Dashboard ]
            │
            ▼
 [ View Before/After Preview ]
            │
    ┌───────┴────────────────────────┐
    ▼                                ▼
[ Accept Changes ]           [ Reject Changes ]
(POST /approve/{user_id})     (POST /reject/{user_id})
- Verify ownership            - Verify ownership
- Verify base version match   - Mark draft rejected
- Publish atomically to v+1   - Live site untouched
- Add to version history      - Retain current version
```

---

## 3. Mandatory LLM Configuration (Groq & LangChain)

Integration is encapsulated in `app/llm_service.py` using **Groq with LangChain's ChatGroq**:

```python
from langchain_groq import ChatGroq

model = ChatGroq(
    model="openai/gpt-oss-20b",
    api_key=groq_api_key,
    request_timeout=timeout_sec,
)
```

- **Model:** `openai/gpt-oss-20b`
- **Timeout:** Enforced at $\le 20$ seconds using `asyncio.wait_for`.
- **API Key Security:** Read securely from `GROQ_API_KEY` (never committed or exposed to the client).

---

## 4. Database Schema (SQLite with WAL Mode)

Database management is encapsulated in `app/database.py` with WAL journal mode (`PRAGMA journal_mode = WAL`) and atomic transactions.

### Tables
1. `published_websites` (`user_id` PK, `current_version`, `html`, `updated_at`)
2. `website_versions` (`id` PK, `user_id`, `version`, `html`, `instruction`, `created_at`, `UNIQUE(user_id, version)`)
3. `pending_drafts` (`draft_id` PK, `user_id`, `instruction`, `original_html`, `proposed_html`, `base_version`, `validation_results`, `change_summary`, `created_at`, `status`)
4. `admin_users` (`id` PK, `email` UNIQUE, `password_hash`, `created_at`, `updated_at`)
5. `admin_sessions` (`session_id` PK, `admin_email`, `expires_at`, `created_at`)
6. `admin_conversations` (`id` PK, `admin_email`, `title`, `created_at`, `updated_at`)
7. `admin_chat_messages` (`id` PK, `conversation_id`, `sender`, `message`, `draft_id`, `created_at`)

---

## 5. API Reference

### Admin & Dashboard Endpoints
- `GET /admin` – Redirects to dashboard if authenticated, or login page if not.
- `GET /admin/login` – Dedicated admin login page.
- `POST /login` (or `/admin/login`) – Authenticate administrator, set HTTP-only cookie.
- `POST /logout` (or `GET /admin/logout`) – Invalidate session and clear cookie.
- `GET /admin/dashboard` – Protected admin AI editor dashboard.
- `POST /admin/change-password` – Change admin password (requires old password).
- `GET /admin/conversations` – List editing conversations.
- `POST /admin/conversations` – Create a new conversation session.
- `GET /admin/conversations/{conv_id}/messages` – Get messages for conversation.
- `POST /admin/conversations/{conv_id}/messages` – Save chat message.

### Core Website Editing Endpoints
- `GET /site/{user_id}` – Returns currently published live HTML (served as `text/html`).
- `POST /webhook` – Processes user/admin natural language messages, generates pending drafts.
- `GET /preview/{user_id}` – Returns pending draft info (JSON API or HTML dashboard via `?format=html`).
- `GET /preview/{user_id}/render` – Serves proposed draft HTML with restrictive CSP headers for iframe sandboxing.
- `POST /approve/{user_id}` – Publishes pending draft atomically, increments version.
- `POST /reject/{user_id}` – Discards pending draft; retains live website unchanged.
- `GET /versions/{user_id}` – Retrieves full published version history.
- `GET /versions/{user_id}/{version}` – Renders specific historical published version.
- `POST /versions/{user_id}/restore/{version}` – Atomically restores a historical published version.
- `GET /health` – Deployment health check (`{"status": "ok"}`).

---

## 6. Installation & Local Setup

### 1. Clone & Setup Environment
```bash
git clone <repo-url>
cd Neptor_Backend_Intern_Assignment

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
playwright install chromium
```

### 2. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Ensure `.env` contains:
```ini
GROQ_API_KEY=your_groq_api_key_here
LLM_MODEL=openai/gpt-oss-20b
LLM_TIMEOUT_SECONDS=20
DATABASE_PATH=editor.db
ADMIN_EMAIL=admin@neptor.in
ADMIN_INITIAL_PASSWORD=neptor123
SESSION_COOKIE_NAME=neptor_admin_session
SESSION_SECRET=neptor-secret-key-change-in-production-2026
```

### 3. Run FastAPI Application
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Open:
- **Admin Dashboard:** `http://localhost:8000/admin` (or `http://localhost:8000/admin/login`)
- **Public Website:** `http://localhost:8000/site/admin@neptor.in`
- **Swagger API Docs:** `http://localhost:8000/docs`

---

## 7. Isolated Website Editing Sandbox Architecture

Rather than relying on risky full-HTML regeneration for every minor modification, the system features a dedicated **Isolated Website Editing Sandbox** under `app/sandbox/`.

```
app/sandbox/
├── sandbox_manager.py     # Orchestrates sandbox lifecycle (copy -> plan -> execute -> validate -> render -> draft)
├── html_parser.py         # Extracts structured DOM context (sections, classes, CSS variables, menu items, IDs)
├── edit_planner.py        # LangChain ChatGroq planner returning structured JSON operation plans
├── edit_executor.py       # Safe BeautifulSoup DOM manipulator applying structured operations
├── sandbox_validator.py   # Enhanced QA validator enforcing mandatory booking IDs and anti-hollowing rules
└── preview_renderer.py    # Playwright headless browser renderer testing console errors and modal click interactions
```

### 7.1 Sandbox Modification Lifecycle
1. **Copy:** When an edit is requested, the live published website snapshot is copied into an isolated sandbox environment. Production files remain 100% unchanged.
2. **Contextualize:** `html_parser.py` parses the HTML DOM and feeds only relevant identifiers, CSS variables, sections, and menu containers to Groq.
3. **Plan:** Groq (`openai/gpt-oss-20b`) returns a targeted JSON plan containing structured editing operations (no Markdown fences, no full HTML output).
4. **Execute:** `edit_executor.py` safely mutates a BeautifulSoup copy of the DOM according to the operation allowlist. Destructive edits to mandatory booking elements are strictly blocked.
5. **QA Validation Gate:** `sandbox_validator.py` inspects the resulting DOM for mandatory booking IDs (`#booking-modal`, `#booking-form`, `#open-booking-btn`, `#close-booking-btn`, `#booking-confirmation`), the runtime script (`#booking-runtime-script`), and form fields (`guest-name`, `guest-count`, `booking-time`).
6. **Browser Rendering:** `preview_renderer.py` loads the proposed HTML in a headless Playwright Chromium instance to catch JavaScript runtime errors, failed asset loads, and verifies booking modal click interactivity.
7. **Draft & Preview:** Changes are saved as a pending draft. The administrator reviews the before/after preview in the dashboard and clicks **Accept** to publish or **Reject** to discard.

### 7.2 Supported Structured Operations Allowlist
- `change_text`: Target element by ID/selector and replace text (e.g. `#brand-title`, `h1`).
- `change_color`: Update CSS variable (e.g. `--accent-color`) or element text color.
- `change_background`: Update background CSS variable (e.g. `--bg-color`) or section background.
- `change_font_size`: Update typography font-size styling on target selector.
- `change_font_family`: Update font-family rule in stylesheet.
- `add_menu_item`: Insert new menu card with name, price (formatted as ₹<price>), and description.
- `remove_menu_item`: Remove targeted menu item from the menu grid.
- `update_menu_item`: Surgically update price or description of an existing menu item.
- `add_section`: Append custom section (title and body content) before footer.
- `remove_section`: Remove target section (with safety guard preventing removal of booking components).
- `update_image`: Update image source URL and alt attributes.
- `change_border_radius`: Update border-radius across buttons or cards.
- `change_spacing`: Update padding or margin on target selectors.

### 7.3 Protection of Mandatory Booking Components
The reservation contract is strictly immutable:
- `#booking-modal`: Reservation dialog container.
- `#booking-form`: Booking submission form.
- `#open-booking-btn`: Button triggering the booking modal.
- `#close-booking-btn`: Button dismissing the modal.
- `#booking-confirmation`: Booking confirmation alert.
- `#booking-runtime-script`: Inline JavaScript driving modal toggle, keyboard accessibility, and form submission.

Any operation attempting to delete or overwrite these components is rejected by `edit_executor.py` before execution, and independently caught by `sandbox_validator.py`.

---

## 8. Automated Test Suite

Run the full automated test suite:
```bash
pytest -v
```

The test suite includes **42 comprehensive automated tests** across three test modules:

### A. Sandbox Test Suite (`tests/test_sandbox.py` - 16 tests)
Covers all 14 required sandbox test scenarios from the specification:
1. **Scenario 1:** Add Masala Dosa for ₹250 (item appears in menu, price is ₹250, booking components intact, live site changes only after approval).
2. **Scenario 2:** Change website background to pink (`change_background`).
3. **Scenario 3:** Change navbar color (`change_color`).
4. **Scenario 4:** Change heading text (`change_text`).
5. **Scenario 5:** Add a new website section (`add_section`).
6. **Scenario 6:** Update an existing menu item (`update_menu_item`).
7. **Scenario 7:** Attempt to delete mandatory booking element (`#booking-modal`) -> cleanly rejected.
8. **Scenario 8:** Attempt to modify booking runtime script -> cleanly rejected.
9. **Scenario 9:** Generate invalid editing JSON -> caught and rejected cleanly without crashing.
10. **Scenario 10:** Simulate Groq timeout -> returns error response, live site 100% untouched.
11. **Scenario 11:** Reject valid draft -> draft marked rejected, live site unchanged.
12. **Scenario 12:** Approve valid draft -> published as version 2, added to version history.
13. **Scenario 13:** Attempt to approve already approved draft -> rejected with 400 Bad Request.
14. **Scenario 14:** Attempt to approve stale draft -> rejected with 409 Conflict.
15. **Unit Test:** Coverage of all additional structured operations (`change_font_size`, `change_font_family`, `change_border_radius`, `change_spacing`, `remove_menu_item`).
16. **Unit Test:** Context extraction and prompt formatting from HTML.

### B. Admin Dashboard Test Suite (`tests/test_admin_dashboard.py` - 11 tests)
- Admin login and cookie authentication.
- Invalid login rejection.
- Route protection and unauthenticated redirect.
- Admin password change workflow.
- Admin logout and session termination.
- Conversation creation and retrieval.
- Chat message persistence.
- Full end-to-end admin workflow (login -> chat edit -> preview -> approve -> publish).
- Version history drawer and historical HTML viewing.
- Atomic version restore.
- Session expiration and revocation.

### C. Editor API Test Suite (`tests/test_editor_api.py` - 15 tests)
- Successful draft creation and version retention.
- Validation rejection of broken HTML.
- Validation rejection of missing booking DOM IDs.
- Validation rejection of missing `#booking-runtime-script`.
- Draft approval and atomic version publishing.
- Stale draft rejection (version mismatch).
- Double approval prevention.
- Draft rejection without modifying live site.
- Rollback functionality (`undo`).
- Concurrency locks under parallel edit requests.
- Before/after diff generation with color-coded syntax.
- HTML preview dashboard rendering.
- End-to-end editing lifecycle.
- Casual conversation handling without LLM invocation.
- Fenced code block stripping and destructive prompt guards.
