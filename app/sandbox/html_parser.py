import re
from typing import Dict, Any, List, Optional
from bs4 import BeautifulSoup


def extract_css_variables(html_content: str) -> Dict[str, str]:
    """Extracts CSS variables defined in :root."""
    variables: Dict[str, str] = {}
    soup = BeautifulSoup(html_content, "html.parser")
    for style in soup.find_all("style"):
        if style.string:
            matches = re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", style.string)
            for var_name, var_val in matches:
                variables[var_name.strip()] = var_val.strip()
    return variables


def extract_website_context(html_content: str) -> Dict[str, Any]:
    """
    Inspects HTML and extracts structured context for the LLM:
    - Page sections and their IDs/classes
    - Menu container ID and existing menu items
    - Navigation sections
    - Hero elements
    - CSS root variables (theme colors, background, etc.)
    - Available DOM IDs
    - Existing booking components and runtime script
    """
    soup = BeautifulSoup(html_content, "html.parser")

    # 1. Page title
    title = soup.title.string.strip() if soup.title and soup.title.string else ""

    # 2. CSS variables
    css_variables = extract_css_variables(html_content)

    # 3. Available DOM IDs
    dom_ids = [el.get("id") for el in soup.find_all(id=True) if el.get("id")]

    # 4. Sections
    sections: List[Dict[str, Any]] = []
    for s in soup.find_all(["section", "header", "footer", "nav"]):
        s_id = s.get("id", "")
        s_class = " ".join(s.get("class", []))
        tag = s.name
        heading = s.find(["h1", "h2", "h3"])
        heading_text = heading.get_text(strip=True) if heading else ""
        sections.append({
            "tag": tag,
            "id": s_id,
            "class": s_class,
            "heading": heading_text[:60]
        })

    # 5. Menu container & Menu items
    menu_container = soup.find(id="menu-items") or soup.find(id="menu-grid") or soup.find(class_="menu-grid") or soup.find(id="menu-section")
    menu_target = "#menu-items" if soup.find(id="menu-items") else ("#menu-grid" if soup.find(id="menu-grid") else ".menu-grid")

    menu_items: List[Dict[str, str]] = []
    for item in soup.select(".menu-item, .menu-card, [data-menu-item]"):
        name_elem = item.find(["h3", "h4", "strong", ".item-name"])
        price_elem = item.find([".price", ".item-price", "span"])
        desc_elem = item.find(["p", ".desc", ".item-desc"])
        name = name_elem.get_text(strip=True) if name_elem else ""
        price = price_elem.get_text(strip=True) if price_elem else ""
        desc = desc_elem.get_text(strip=True) if desc_elem else ""
        if name:
            menu_items.append({"name": name, "price": price, "description": desc})

    # 6. Hero info
    hero = soup.select_one(".hero")
    hero_info = {}
    if hero:
        h1 = hero.find("h1")
        p = hero.find("p")
        hero_info = {
            "heading": h1.get_text(strip=True) if h1 else "",
            "heading_id": h1.get("id") if h1 else "",
            "description": p.get_text(strip=True) if p else "",
            "description_id": p.get("id") if p else "",
        }

    # 7. Booking elements check
    booking_ids = [
        "booking-modal",
        "booking-form",
        "open-booking-btn",
        "close-booking-btn",
        "booking-confirmation",
    ]
    present_booking_ids = [bid for bid in booking_ids if soup.find(id=bid)]
    has_runtime_script = soup.find("script", id="booking-runtime-script") is not None

    return {
        "title": title,
        "css_variables": css_variables,
        "dom_ids": dom_ids,
        "sections": sections,
        "menu_target": menu_target,
        "menu_items": menu_items,
        "hero_info": hero_info,
        "present_booking_ids": present_booking_ids,
        "has_runtime_script": has_runtime_script,
    }


def format_context_for_prompt(context: Dict[str, Any]) -> str:
    """Formats extracted context into clean markdown for Groq prompt."""
    sections_summary = [
        f"{s.get('tag')}(id={s.get('id')}, class={s.get('class')})"
        for s in context.get("sections", [])
    ]
    menu_names = [item.get("name") for item in context.get("menu_items", [])]
    lines = [
        f"- Page Title: {context.get('title')}",
        f"- CSS Variables in :root: {context.get('css_variables')}",
        f"- Menu Target Container: {context.get('menu_target')}",
        f"- Existing Menu Items: {menu_names}",
        f"- Key Page Sections: {sections_summary}",
        f"- Important DOM IDs: {context.get('dom_ids')}",
        f"- Hero Details: {context.get('hero_info')}",
        "- Mandatory Booking IDs (PROTECTED & IMMUTABLE): ['booking-modal', 'booking-form', 'open-booking-btn', 'close-booking-btn', 'booking-confirmation']",
        "- Mandatory Script (PROTECTED & IMMUTABLE): 'booking-runtime-script'",
    ]
    return "\n".join(lines)
