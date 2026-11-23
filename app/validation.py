import re
from typing import Dict, Any, List
from bs4 import BeautifulSoup


MANDATORY_DOM_IDS = [
    "booking-modal",
    "booking-form",
    "open-booking-btn",
    "close-booking-btn",
    "booking-confirmation",
]

MANDATORY_SCRIPT_ID = "booking-runtime-script"


def clean_code_fences(raw_text: str) -> str:
    """
    Removes Markdown code fences such as ```html ... ``` or ``` ... ```
    and strips leading/trailing whitespace.
    """
    cleaned = raw_text.strip()
    # Match ```html ... ``` or ``` ... ```
    fence_pattern = re.compile(r"^```(?:html)?\s*\n?(.*?)\n?```$", re.DOTALL | re.IGNORECASE)
    match = fence_pattern.search(cleaned)
    if match:
        cleaned = match.group(1).strip()
    else:
        # Check if text starts with ``` or ends with ```
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:html)?\s*", "", cleaned, flags=re.IGNORECASE).strip()
        if cleaned.endswith("```"):
            cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    return cleaned


def validate_html(html_content: str, original_html: str = "") -> Dict[str, Any]:
    """
    Validates generated HTML against production QA requirements:
    1. Valid non-empty HTML structure (<html>, <head>, <body>).
    2. Mandatory booking DOM IDs remain intact.
    3. Mandatory #booking-runtime-script remains intact with code.
    4. Guard against destructive or empty wipes (body must have content).
    """
    errors: List[str] = []
    checks = {
        "html_structure": False,
        "booking_elements": False,
        "runtime_script": False,
        "content_integrity": False,
    }
    missing_ids: List[str] = []

    if not html_content or not html_content.strip():
        errors.append("HTML output is empty.")
        return {
            "is_valid": False,
            "errors": errors,
            "missing_ids": MANDATORY_DOM_IDS,
            "checks": checks,
        }

    soup = BeautifulSoup(html_content, "html.parser")

    # 1. HTML structure check
    has_html = soup.find("html") is not None
    has_head = soup.find("head") is not None
    has_body = soup.find("body") is not None

    if has_html and has_head and has_body:
        checks["html_structure"] = True
    else:
        missing_tags = []
        if not has_html:
            missing_tags.append("<html>")
        if not has_head:
            missing_tags.append("<head>")
        if not has_body:
            missing_tags.append("<body>")
        errors.append(f"Incomplete HTML structure; missing: {', '.join(missing_tags)}")

    # 2. Mandatory booking DOM IDs
    for dom_id in MANDATORY_DOM_IDS:
        elem = soup.find(id=dom_id)
        if not elem:
            missing_ids.append(dom_id)

    if not missing_ids:
        checks["booking_elements"] = True
    else:
        errors.append(f"Missing mandatory booking DOM IDs: {', '.join(missing_ids)}")

    # 3. Mandatory booking runtime script
    script = soup.find("script", id=MANDATORY_SCRIPT_ID)
    if script and script.string and len(script.string.strip()) > 30:
        checks["runtime_script"] = True
    else:
        errors.append(f"Missing or corrupted '{MANDATORY_SCRIPT_ID}' script element.")

    # 4. Content integrity / destructive prompt guard
    body = soup.find("body")
    body_text = body.get_text(strip=True) if body else ""
    if len(body_text) < 120:
        errors.append(
            f"HTML body content appears truncated or gutted ({len(body_text)} characters); rejecting as destructive."
        )
    else:
        checks["content_integrity"] = True

    is_valid = len(errors) == 0
    return {
        "is_valid": is_valid,
        "errors": errors,
        "missing_ids": missing_ids,
        "checks": checks,
    }
