# Submission Notes

**Candidate Name:** Backend Engineering Intern Candidate  
**Live Deployed URL:** https://neptor-web-editor-api.onrender.com *(replace with active deployment URL)*  
**GitHub Repository URL:** https://github.com/vyshrawan/neptor-conversational-web-editor *(replace with active repository URL)*  
**LLM Provider & Model Used:** Groq — `openai/gpt-oss-20b` via LangChain's `ChatGroq`  
**Approximate Hours Spent:** 4.5 hours  

---

## Engineering Reflection (Required — Keep Each Answer to 3–5 Concise Bullets)

### 1. Architecture & Request Flow
*Briefly explain how a `POST /webhook` request moves through intent detection, locking, LLM generation, QA validation, and storage.*

- **Per-User Serialization:** The request acquires a per-user `asyncio.Lock` to serialize all incoming operations for that user without blocking requests from other users.
- **Intent Classification:** Fast regex and token-based heuristics classify the message into `chat` (polite reply, `no_change`), `undo` (atomic rollback to `current_version - 1`), or `edit`.
- **LLM Modification with Timeout:** For edits, LangChain's `ChatGroq` (`openai/gpt-oss-20b`) is called under an explicit $\le 20$s `asyncio.wait_for` timeout with instructions enforcing HTML integrity and booking ID preservation.
- **Automated QA Gate:** Output is stripped of markdown fences and parsed with BeautifulSoup to verify `<html>/<head>/<body>`, mandatory booking modal IDs (`#booking-modal`, `#booking-form`, etc.), `#booking-runtime-script`, and content non-gutting.
- **Draft Creation & Diffing:** On validation success, any existing pending draft is explicitly marked `stale`, an HTML line diff (red/green) and semantic change summary are generated, and a new draft is persisted in SQLite with `status='pending'` without altering the live website.

### 2. Guarding Against AI Hallucinations & Broken HTML
*How did you ensure the LLM does not strip `#booking-form`, corrupt JavaScript, or leak markdown fences into the live page?*

- **Code Fence Pre-Processing:** An automated regex stripper removes leading/trailing ```html and ``` code blocks before HTML parsing or storage.
- **Mandatory DOM Contract Verification:** BeautifulSoup verifies that all required reservation elements (`#booking-modal`, `#booking-form`, `#open-booking-btn`, `#close-booking-btn`, `#booking-confirmation`) are present in the DOM tree.
- **Runtime Script Integrity:** The validator checks that `<script id="booking-runtime-script">` is present and contains functional reservation event listener code.
- **Anti-Destructive Prompt Guard:** Adversarial attempts to wipe the site (e.g., replacing the site with a short poem) are rejected if the body content drops below baseline length thresholds or structural elements are gutted.
- **Never Auto-Publish:** The live site is never touched directly; invalid modifications are immediately rejected with error details, leaving the live site 100% intact.

### 3. Concurrency & Failure Recovery
*How do you prevent two back-to-back messages for the same `user_id` from clobbering each other, and what happens if the LLM API hangs for 30 seconds?*

- **Per-User In-Memory Locks:** All webhook, approval, and rejection handlers acquire an `asyncio.Lock` keyed by `user_id`, guaranteeing sequential execution of rapid back-to-back messages.
- **Strict 20-Second LLM Timeout:** Calls to Groq are wrapped with `asyncio.wait_for(timeout=20.0)`. If the API hangs beyond 20s, a `TimeoutError` is caught, logged, and a clean error response is returned while keeping the live website untouched.
- **Optimistic Version Verification:** Approvals verify that `draft.base_version == published.current_version`. If a concurrent change updated the site in the interim, the draft is marked `stale` and rejected with `409 Conflict`.
- **Atomic Database Transactions:** SQLite `BEGIN IMMEDIATE` / `with conn:` blocks wrap all publishing and version insertion operations, preventing race conditions or partial updates.

### 4. Scaling Bottleneck (100 Concurrent Users / Multiple Instances)
*What is the first thing in your current implementation that would break under 100 concurrent users or if deployed across 2+ server replicas, and how would you fix it?*

- **In-Memory Lock Boundary:** The current `asyncio.Lock` dictionary is process-local. Across 2+ replicas behind a load balancer, two concurrent requests for the same `user_id` hitting different pods would bypass in-memory serialization.
- **SQLite Write Contention:** Under 100 concurrent users performing writes, SQLite's single-writer architecture can encounter `database is locked` / `busy_timeout` bottlenecks.
- **Fix — Distributed Locking (Redis):** Replace `LockManager` with a distributed locking mechanism using Redis (e.g., `aioredis` or Redlock) keyed by `lock:user:{user_id}` with automatic TTL.
- **Fix — Relational Database (PostgreSQL):** Migrate from SQLite to PostgreSQL with row-level locking (`SELECT ... FOR UPDATE`) or optimistic version checks, backed by connection pooling (e.g., `asyncpg` / SQLAlchemy pool).

### 5. AI Tool Usage Disclosure
*Which AI tools did you use, what parts did they accelerate, and what bug or design flaw did you have to catch and fix manually?*

- **Tools Used:** Antigravity (Google DeepMind agentic assistant) and Cursor for scaffolding FastAPI endpoints, writing diff generation routines, and drafting pytest test cases.
- **Accelerated Parts:** Rapidly generated boilerplate Pydantic models, BeautifulSoup DOM delta parsing, and the syntax-highlighted diff HTML formatting.
- **Flaws Caught & Fixed Manually:**
  1. *Default Parameter Evaluation:* `db_path: str = DATABASE_PATH` captured default values at module import time, causing tests with monkeypatched database paths to still write to `editor.db`. Fixed by evaluating `target = db_path or config.DATABASE_PATH` dynamically inside function bodies.
  2. *Cross-Thread Event Loop Deadlock:* `ThreadPoolExecutor` in concurrency tests created separate threads with their own event loops trying to acquire an `asyncio.Lock` bound to another event loop. Refactored concurrency testing to use `httpx.AsyncClient` with `asyncio.gather` on the native event loop.
  3. *Casual Chat Intent Edge Cases:* Initial regex required strict full-string matching (`^hi$`), causing compound pleasantries like `"hi looks awesome thanks"` to miss the chat filter. Expanded intent classification with keyword token matching and action-verb exclusion.
