import asyncio
import logging
import re
from typing import Tuple, Dict, Any, Optional
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage

from app.config import GROQ_API_KEY, LLM_MODEL, LLM_TIMEOUT_SECONDS
from app.validation import clean_code_fences

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Base exception for LLM-related issues."""
    pass


class LLMTimeoutError(LLMError):
    pass


class LLMConfigurationError(LLMError):
    pass


# Fast keywords and regex patterns for conversational messages
CASUAL_KEYWORDS = [
    "hi", "hello", "hey", "greetings", "hola", "namaste", "yo", "howdy",
    "thanks", "thank you", "thx",
    "awesome", "great", "nice", "cool", "clean", "amazing",
    "cost", "price", "pricing", "who are you", "what can you do", "help",
]

EDIT_ACTION_WORDS = [
    "change", "update", "add", "remove", "delete", "replace", "switch",
    "set", "modify", "edit", "make", "put", "insert", "style", "rename",
    "color", "background", "title", "hours", "menu", "heading", "logo",
    "font", "button", "section", "footer", "header", "text"
]

UNDO_PATTERNS = [
    r"^undo[\s!.,?]*$",
    r"^revert[\s!.,?]*$",
    r"^undo\s+last\s+(change|edit)[\s!.,?]*$",
    r"^roll\s*back[\s!.,?]*$",
]


def classify_intent(message: str) -> str:
    """
    Classifies user message intent into 'chat', 'undo', or 'edit'.
    """
    msg = message.strip().lower()

    # 1. Check for undo
    for pattern in UNDO_PATTERNS:
        if re.match(pattern, msg, re.IGNORECASE):
            return "undo"

    # Check if message contains clear edit actions or instructions
    has_edit_intent = any(re.search(r"\b" + re.escape(w) + r"\b", msg) for w in EDIT_ACTION_WORDS)

    # 2. Check for casual chat / non-editing queries
    has_casual_tokens = any(re.search(r"\b" + re.escape(w) + r"\b", msg) for w in CASUAL_KEYWORDS)
    if has_casual_tokens and not has_edit_intent:
        return "chat"

    # Also handle casual phrases directly
    casual_phrases = [
        "looks great", "looks awesome", "looks good", "looks cool", "looks nice",
        "how much", "how much does", "thank you", "thanks so much"
    ]
    if any(p in msg for p in casual_phrases) and not has_edit_intent:
        return "chat"

    # 3. Default to edit
    return "edit"


def get_llm(model_name: str = LLM_MODEL, groq_api_key: Optional[str] = None) -> ChatGroq:
    """
    Initializes ChatGroq exactly as specified:
    from langchain_groq import ChatGroq
    model = ChatGroq(model="openai/gpt-oss-20b", api_key=groq_api_key)
    """
    api_key = groq_api_key or GROQ_API_KEY
    if not api_key:
        raise LLMConfigurationError(
            "GROQ_API_KEY is not configured. Please set GROQ_API_KEY in your .env or environment variables."
        )

    # Initialize model exactly as required
    timeout_sec = min(float(LLM_TIMEOUT_SECONDS), 20.0)
    model = ChatGroq(
        model=model_name,
        api_key=api_key,
        request_timeout=timeout_sec,
    )
    return model


SYSTEM_PROMPT = """
You are an expert website editing AI.

IMPORTANT RULES:

1. You must modify the EXISTING HTML, not recreate the entire website.
2. Preserve all existing sections, components, CSS and JavaScript.
3. Never remove unrelated website content.
4. Never remove or rename these mandatory DOM IDs:

   - booking-modal
   - booking-form
   - open-booking-btn
   - close-booking-btn
   - booking-confirmation

5. Always preserve the complete booking-runtime-script element.
6. Do not rewrite or remove existing booking functionality.
7. When adding a product, menu item, or changing colors,
   modify only the relevant section.
8. Preserve existing element IDs and CSS classes.
9. Return the complete modified HTML.
10. Never return explanations or Markdown fences.

If the user asks to add a food item, add it to the
existing menu section without changing other sections.
"""


async def generate_modified_html(
    instruction: str,
    current_html: str,
    llm: Optional[ChatGroq] = None
) -> str:
    """
    Calls Groq LLM with LangChain ChatGroq to generate modified HTML.
    Enforces explicit timeout of 20 seconds or less.
    Strips markdown code fences if present.
    """
    model = llm if llm is not None else get_llm()

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(
            content=f"USER INSTRUCTION:\n{instruction}\n\nCURRENT WEBSITE HTML:\n{current_html}\n\nMODIFIED HTML:"
        )
    ]

    timeout_sec = min(float(LLM_TIMEOUT_SECONDS), 20.0)

    try:
        # Enforce explicit async timeout <= 20s
        response = await asyncio.wait_for(
            model.ainvoke(messages),
            timeout=timeout_sec
        )
        raw_output = response.content if hasattr(response, "content") else str(response)
        cleaned_html = clean_code_fences(raw_output)
        return cleaned_html
    except asyncio.TimeoutError:
        logger.error("LLM request timed out after %.1f seconds", timeout_sec)
        raise LLMTimeoutError(f"LLM request timed out after {timeout_sec:.0f} seconds.")
    except Exception as e:
        logger.error("LLM invocation failed: %s", str(e))
        raise LLMError(f"LLM invocation error: {str(e)}")
