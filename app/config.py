import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env if present
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

GROQ_API_KEY = os.getenv("GROQ_API_KEY") or os.getenv("LLM_API_KEY") or ""
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-20b")
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "20.0"))

DATABASE_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "editor.db"))
STARTER_SITE_PATH = os.getenv("STARTER_SITE_PATH", str(BASE_DIR / "starter_site.html"))

# Administrator Configuration
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@neptor.in")
ADMIN_INITIAL_PASSWORD = os.getenv("ADMIN_INITIAL_PASSWORD", "neptor123")
SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "neptor_admin_session")
SESSION_SECRET = os.getenv("SESSION_SECRET", "neptor-secret-key-change-in-production-2026")
FRONTEND_DIR = os.getenv("FRONTEND_DIR", str(BASE_DIR / "frontend"))
