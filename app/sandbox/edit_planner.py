import json
import logging
import re
from typing import Dict, Any, List, Optional
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage

from app.config import GROQ_API_KEY, LLM_MODEL, LLM_TIMEOUT_SECONDS
from app.llm_service import get_llm
from app.sandbox.html_parser import format_context_for_prompt

logger = logging.getLogger("edit_planner")

SUPPORTED_OPERATIONS = {
    "change_text",
    "change_color",
    "change_background",
    "change_font_size",
    "change_font_family",
    "add_menu_item",
    "remove_menu_item",
    "update_menu_item",
    "add_section",
    "remove_section",
    "update_image",
    "change_border_radius",
    "change_spacing",
}

PLANNER_SYSTEM_PROMPT = """You are an expert Website Editor Assistant that plans targeted, surgical modifications to an existing café website.

DO NOT REGENERATE THE ENTIRE WEBSITE HTML.
Instead, return a structured JSON plan containing the exact editing operations required.

SUPPORTED OPERATION TYPES:
1. "change_text": {"type": "change_text", "target": "<id or selector, e.g. #brand-title or .logo>", "data": {"text": "<new text>"}}
2. "change_color": {"type": "change_color", "target": "<selector or CSS variable, e.g. --accent-color>", "data": {"color": "<css color, e.g. #047857>"}}
3. "change_background": {"type": "change_background", "target": "<selector or CSS variable, e.g. --bg-color or .hero or header>", "data": {"background": "<color/css, e.g. #fbcfe8>"}}
4. "change_font_size": {"type": "change_font_size", "target": "<selector, e.g. h1 or .hero h1>", "data": {"font_size": "<e.g. 2.5rem or 32px>"}}
5. "change_font_family": {"type": "change_font_family", "target": "<selector, e.g. body or h1>", "data": {"font_family": "<font name>"}}
6. "add_menu_item": {"type": "add_menu_item", "target": "<menu container, e.g. #menu-items or .menu-grid>", "data": {"name": "<item name>", "price": <number or string>, "description": "<description>"}}
7. "remove_menu_item": {"type": "remove_menu_item", "target": "<item name>", "data": {"name": "<item name>"}}
8. "update_menu_item": {"type": "update_menu_item", "target": "<item name>", "data": {"name": "<item name>", "price": <number or string>, "description": "<description>"}}
9. "add_section": {"type": "add_section", "target": "<before footer>", "data": {"title": "<section title>", "content": "<html or text content>"}}
10. "remove_section": {"type": "remove_section", "target": "<section id or selector>"}
11. "change_border_radius": {"type": "change_border_radius", "target": "<selector, e.g. .btn-primary or button>", "data": {"border_radius": "<e.g. 999px or 16px>"}}
12. "change_spacing": {"type": "change_spacing", "target": "<selector>", "data": {"padding": "<e.g. 1rem 2rem>", "margin": "<e.g. 2rem 0>"}}

CRITICAL SAFETY CONSTRAINTS:
- You must NEVER attempt to remove, rename, or modify any mandatory booking element:
  - #booking-modal
  - #booking-form
  - #open-booking-btn
  - #close-booking-btn
  - #booking-confirmation
  - #booking-runtime-script
- Your response MUST be valid raw JSON with NO markdown code fences, NO explanation outside the JSON.

OUTPUT JSON FORMAT:
{
  "explanation": "Brief human-readable summary of the planned edits",
  "operations": [
    {
      "type": "<operation_type>",
      "target": "<target selector or variable>",
      "data": { ... }
    }
  ]
}"""


def parse_plan_json(raw_json_str: str) -> Dict[str, Any]:
    """Cleans and parses JSON plan from LLM output."""
    cleaned = raw_json_str.strip()
    # Strip markdown code fences if present
    fence_pattern = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL | re.IGNORECASE)
    match = fence_pattern.search(cleaned)
    if match:
        cleaned = match.group(1).strip()
    else:
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE).strip()
        if cleaned.endswith("```"):
            cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    try:
        plan = json.loads(cleaned)
        if not isinstance(plan, dict) or "operations" not in plan:
            raise ValueError("Plan JSON must be an object with an 'operations' list.")
        return plan
    except Exception as e:
        logger.error("Failed to parse plan JSON: %s. Output was: %s", str(e), raw_json_str[:200])
        raise ValueError(f"Invalid plan JSON returned by LLM: {str(e)}")


async def plan_edits(
    instruction: str,
    website_context: Dict[str, Any],
    llm: Optional[ChatGroq] = None
) -> Dict[str, Any]:
    """
    Asks Groq LLM to generate structured editing operations given the instruction
    and inspected website context.
    """
    model = llm if llm is not None else get_llm()
    formatted_context = format_context_for_prompt(website_context)

    messages = [
        SystemMessage(content=PLANNER_SYSTEM_PROMPT),
        HumanMessage(content=f"WEBSITE CONTEXT:\n{formatted_context}\n\nUSER INSTRUCTION:\n{instruction}\n\nSTRUCTURED JSON PLAN:")
    ]

    import asyncio
    timeout_sec = min(float(LLM_TIMEOUT_SECONDS), 20.0)

    try:
        response = await asyncio.wait_for(
            model.ainvoke(messages),
            timeout=timeout_sec
        )
        raw_text = response.content if hasattr(response, "content") else str(response)
        plan = parse_plan_json(raw_text)

        # Validate operations
        for op in plan.get("operations", []):
            op_type = op.get("type")
            if op_type not in SUPPORTED_OPERATIONS:
                raise ValueError(f"Unsupported operation type: '{op_type}'.")

        return plan
    except asyncio.TimeoutError:
        raise TimeoutError(f"Groq planning request timed out after {timeout_sec:.0f} seconds.")
