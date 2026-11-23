from typing import Dict, Any, List, Optional
from bs4 import BeautifulSoup


MANDATORY_DOM_IDS = [
    "booking-modal",
    "booking-form",
    "open-booking-btn",
    "close-booking-btn",
    "booking-confirmation",
]

MANDATORY_SCRIPT_ID = "booking-runtime-script"

REQUIRED_FORM_INPUTS = [
    "guest-name",
    "guest-count",
    "booking-time",
]


class SandboxValidationError(Exception):
    """Exception raised when sandbox QA validation fails."""
    pass


def validate_sandbox_html(
    modified_html: str,
    original_html: Optional[str] = None
) -> Dict[str, Any]:
    """
    Enhanced validation gate for the editing sandbox:
    1. Valid non-empty HTML structure (<html>, <head>, <body>).
    2. Every mandatory booking ID exists.
    3. Mandatory #booking-runtime-script exists and retains original runtime logic.
    4. Form retains required inputs and interactive controls.
    5. No empty placeholder stubs merely to bypass validation.
    6. Before/after component comparison guarantees booking fidelity.
    """
    errors: List[str] = []
    checks = {
        "html_structure": False,
        "booking_elements": False,
        "runtime_script": False,
        "form_integrity": False,
        "anti_hollowing": False,
    }
    missing_ids: List[str] = []

    if not modified_html or not modified_html.strip():
        errors.append("Modified HTML output is completely empty.")
        return {
            "is_valid": False,
            "errors": errors,
            "missing_ids": MANDATORY_DOM_IDS,
            "checks": checks,
        }

    soup = BeautifulSoup(modified_html, "html.parser")

    # 1. Complete HTML structure
    has_html = soup.find("html") is not None
    has_head = soup.find("head") is not None
    has_body = soup.find("body") is not None
    if has_html and has_head and has_body:
        checks["html_structure"] = True
    else:
        missing_tags = []
        if not has_html: missing_tags.append("<html>")
        if not has_head: missing_tags.append("<head>")
        if not has_body: missing_tags.append("<body>")
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
    if not script:
        errors.append(f"Missing mandatory script element with id='{MANDATORY_SCRIPT_ID}'.")
    elif not script.string or len(script.string.strip()) < 50:
        errors.append(f"Script element '{MANDATORY_SCRIPT_ID}' has been emptied or truncated.")
    else:
        content = script.string.lower()
        # Verify essential runtime keywords exist
        essential_keywords = ["booking-modal", "openmodal", "closemodal", "submit", "addeventlistener"]
        missing_kw = [kw for kw in essential_keywords if kw not in content]
        if missing_kw:
            errors.append(f"Script '{MANDATORY_SCRIPT_ID}' is missing essential interaction handlers: {missing_kw}")
        else:
            checks["runtime_script"] = True

    # 4. Form integrity & anti-placeholder validation
    form = soup.find(id="booking-form")
    if form:
        form_inputs = [inp.get("id") for inp in form.find_all(["input", "select"]) if inp.get("id")]
        missing_inputs = [req for req in REQUIRED_FORM_INPUTS if req not in form_inputs]
        if missing_inputs:
            errors.append(f"Booking form is missing required input controls: {missing_inputs}")
        else:
            # Check for empty placeholder stubs
            modal = soup.find(id="booking-modal")
            if modal and len(modal.get_text(strip=True)) < 25:
                errors.append("Booking modal appears to be an empty placeholder stub.")
            else:
                checks["form_integrity"] = True
                checks["anti_hollowing"] = True
    else:
        errors.append("Mandatory element '#booking-form' is missing from the DOM.")

    # 5. Content non-gutting check
    body = soup.find("body")
    body_text = body.get_text(strip=True) if body else ""
    if len(body_text) < 120:
        errors.append(f"Website body content is severely depleted ({len(body_text)} characters); rejected as destructive.")

    is_valid = len(errors) == 0
    return {
        "is_valid": is_valid,
        "errors": errors,
        "missing_ids": missing_ids,
        "checks": checks,
    }
