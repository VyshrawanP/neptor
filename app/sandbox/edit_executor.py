import re
from typing import Dict, Any, List, Optional
from bs4 import BeautifulSoup


class EditExecutionError(Exception):
    """Exception raised when an editing operation fails or is rejected."""
    pass


MANDATORY_BOOKING_IDS = {
    "booking-modal",
    "booking-form",
    "open-booking-btn",
    "close-booking-btn",
    "booking-confirmation",
    "booking-runtime-script",
}


def sanitize_css_value(value: str) -> str:
    """Sanitizes CSS values against injection."""
    cleaned = re.sub(r"[;<>{}]", "", str(value)).strip()
    return cleaned


def update_css_variable(soup: BeautifulSoup, var_name: str, new_value: str) -> bool:
    """Updates or inserts a CSS variable in the first <style> element."""
    var_name = var_name if var_name.startswith("--") else f"--{var_name}"
    clean_val = sanitize_css_value(new_value)

    style_tag = soup.find("style")
    if not style_tag:
        style_tag = soup.new_tag("style")
        if soup.head:
            soup.head.append(style_tag)
        else:
            soup.insert(0, style_tag)

    content = style_tag.string or ""
    pattern = re.compile(rf"({re.escape(var_name)}\s*:\s*)([^;]+)(;)")
    if pattern.search(content):
        updated_content = pattern.sub(rf"\g<1>{clean_val}\g<3>", content)
        style_tag.string = updated_content
        return True
    else:
        # Append to :root or create :root block
        if ":root" in content:
            updated_content = content.replace(":root {", f":root {{\n      {var_name}: {clean_val};")
        else:
            updated_content = f":root {{\n  {var_name}: {clean_val};\n}}\n" + content
        style_tag.string = updated_content
        return True


def execute_structured_operations(
    original_html: str,
    operations: List[Dict[str, Any]]
) -> str:
    """
    Applies structured operations safely to a copy of the HTML DOM.
    Never alters unrelated elements or mandatory booking components.
    """
    soup = BeautifulSoup(original_html, "html.parser")

    for op in operations:
        op_type = op.get("type")
        target = str(op.get("target", "")).strip()
        data = op.get("data", {}) or {}

        # 1. Safety check: Target must never be a mandatory booking component for deletion/destructive edits
        target_clean_id = target.lstrip("#").strip()
        if target_clean_id in MANDATORY_BOOKING_IDS and op_type in ("remove_section", "remove_menu_item", "change_text"):
            raise EditExecutionError(f"Operation rejected: Attempted destructive modification on mandatory component '{target}'.")

        # 2. Execute operation
        if op_type == "add_menu_item":
            _op_add_menu_item(soup, target, data)
        elif op_type == "remove_menu_item":
            _op_remove_menu_item(soup, target, data)
        elif op_type == "update_menu_item":
            _op_update_menu_item(soup, target, data)
        elif op_type in ("change_background", "change_color"):
            _op_change_color_or_background(soup, target, data, op_type)
        elif op_type == "change_text":
            _op_change_text(soup, target, data)
        elif op_type == "change_font_size":
            _op_change_font_size(soup, target, data)
        elif op_type == "change_font_family":
            _op_change_font_family(soup, target, data)
        elif op_type == "change_border_radius":
            _op_change_border_radius(soup, target, data)
        elif op_type == "change_spacing":
            _op_change_spacing(soup, target, data)
        elif op_type == "add_section":
            _op_add_section(soup, target, data)
        elif op_type == "remove_section":
            _op_remove_section(soup, target)
        else:
            raise EditExecutionError(f"Unknown operation type: '{op_type}'.")

    return str(soup)


def _op_add_menu_item(soup: BeautifulSoup, target: str, data: Dict[str, Any]) -> None:
    name = str(data.get("name", "Special Item")).strip()
    raw_price = data.get("price", "250")
    # Format price with ₹ or Rs
    price_str = f"₹{raw_price}" if not str(raw_price).startswith(("₹", "Rs", "$")) else str(raw_price)
    description = str(data.get("description", "")).strip()

    # Find menu container
    container = None
    if target:
        container = soup.select_one(target)

    if not container:
        # Fallbacks: #menu-items, #menu-grid, .menu-grid, #menu-section
        container = (
            soup.find(id="menu-items")
            or soup.find(id="menu-grid")
            or soup.find(class_="menu-grid")
            or soup.find(class_="menu-section")
        )

    if not container:
        # Look for existing menu item to append beside it
        existing_item = soup.select_one(".menu-item, .menu-card")
        if existing_item and existing_item.parent:
            container = existing_item.parent

    if not container:
        raise EditExecutionError("Could not locate menu container to insert item.")

    # Create safe HTML element
    item_div = soup.new_tag("div", attrs={"class": "menu-item", "data-menu-item": "true"})
    item_div["style"] = "background: var(--surface-color, #ffffff); border: 1px solid var(--border-color, #e7e5e4); border-radius: 8px; padding: 1.25rem; margin-bottom: 1rem;"

    header_div = soup.new_tag("div", style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 0.35rem;")
    h3 = soup.new_tag("h3", style="font-size: 1.1rem; font-weight: 600; color: var(--text-primary);")
    h3.string = name

    price_span = soup.new_tag("span", attrs={"class": "price"}, style="font-weight: 700; color: var(--accent-color);")
    price_span.string = price_str

    header_div.append(h3)
    header_div.append(price_span)
    item_div.append(header_div)

    if description:
        p_desc = soup.new_tag("p", attrs={"class": "item-desc"}, style="color: var(--text-muted); font-size: 0.9rem;")
        p_desc.string = description
        item_div.append(p_desc)

    container.append(item_div)


def _op_remove_menu_item(soup: BeautifulSoup, target: str, data: Dict[str, Any]) -> None:
    name = (data.get("name") or target or "").strip().lower()
    if not name:
        raise EditExecutionError("Menu item name is required for removal.")

    found = False
    for item in soup.select(".menu-item, .menu-card, [data-menu-item]"):
        h = item.find(["h3", "h4", "strong", ".item-name"])
        if h and name in h.get_text(strip=True).lower():
            item.decompose()
            found = True
            break

    if not found:
        raise EditExecutionError(f"Menu item '{name}' was not found in the menu.")


def _op_update_menu_item(soup: BeautifulSoup, target: str, data: Dict[str, Any]) -> None:
    name = (data.get("name") or target or "").strip().lower()
    found = False

    for item in soup.select(".menu-item, .menu-card, [data-menu-item]"):
        h = item.find(["h3", "h4", "strong", ".item-name"])
        if h and name in h.get_text(strip=True).lower():
            if "price" in data:
                raw_price = data["price"]
                price_str = f"₹{raw_price}" if not str(raw_price).startswith(("₹", "Rs", "$")) else str(raw_price)
                price_elem = item.find([".price", ".item-price", "span"])
                if price_elem:
                    price_elem.string = price_str
            if "description" in data:
                desc_elem = item.find(["p", ".desc", ".item-desc"])
                if desc_elem:
                    desc_elem.string = str(data["description"]).strip()
            found = True
            break

    if not found:
        raise EditExecutionError(f"Menu item '{name}' was not found to update.")


def _op_change_color_or_background(soup: BeautifulSoup, target: str, data: Dict[str, Any], op_type: str) -> None:
    val = data.get("background") or data.get("color") or ""
    clean_val = sanitize_css_value(str(val))
    if not clean_val:
        raise EditExecutionError("Color value is missing.")

    # 1. Check if target or instruction indicates theme / overall background
    if op_type == "change_background" and (
        target in ("--bg-color", "body", "website", "theme", "")
        or "theme" in target.lower()
        or "background" in target.lower()
    ):
        update_css_variable(soup, "--bg-color", clean_val)
        return

    # 2. Check if accent color
    if op_type == "change_color" and (
        target in ("--accent-color", "accent", "accent-color", "theme", "")
        or "accent" in target.lower()
    ):
        update_css_variable(soup, "--accent-color", clean_val)
        return

    # 3. Check CSS variable target directly
    if target.startswith("--"):
        update_css_variable(soup, target, clean_val)
        return

    # 4. Check element target (e.g. navbar / header, hero, footer)
    elem = soup.select_one(target) if target else None
    if not elem:
        if "nav" in target.lower() or "navbar" in target.lower():
            elem = soup.find("header") or soup.find("nav")
        elif "hero" in target.lower():
            elem = soup.select_one(".hero")
        elif "footer" in target.lower():
            elem = soup.find("footer")

    if elem:
        prop = "background-color" if op_type == "change_background" else "color"
        existing_style = elem.get("style", "")
        elem["style"] = f"{existing_style}; {prop}: {clean_val};".strip("; ")
    else:
        # Fallback to CSS rule in style tag
        style = soup.find("style")
        if style:
            prop = "background-color" if op_type == "change_background" else "color"
            style.string = (style.string or "") + f"\n{target} {{ {prop}: {clean_val} !important; }}\n"


def _op_change_text(soup: BeautifulSoup, target: str, data: Dict[str, Any]) -> None:
    new_text = str(data.get("text", "")).strip()
    if not new_text:
        raise EditExecutionError("Text data is missing.")

    elem = None
    if target:
        elem = soup.select_one(target)

    if not elem:
        if "title" in target.lower() or "brand" in target.lower():
            elem = soup.find(id="brand-title") or soup.select_one(".hero h1") or soup.title
        elif "logo" in target.lower():
            elem = soup.find(id="brand-logo") or soup.select_one(".logo")
        elif "heading" in target.lower():
            elem = soup.select_one(".hero h1") or soup.find(["h1", "h2"])

    if not elem:
        raise EditExecutionError(f"Target element '{target}' not found for text change.")

    elem.string = new_text


def _op_change_font_size(soup: BeautifulSoup, target: str, data: Dict[str, Any]) -> None:
    size = sanitize_css_value(str(data.get("font_size", "1.5rem")))
    elem = soup.select_one(target) if target else soup.select_one(".hero h1, h1")
    if elem:
        existing = elem.get("style", "")
        elem["style"] = f"{existing}; font-size: {size} !important;".strip("; ")
    else:
        style = soup.find("style")
        if style:
            style.string = (style.string or "") + f"\n{target or 'h1, h2, .hero h1'} {{ font-size: {size} !important; }}\n"


def _op_change_font_family(soup: BeautifulSoup, target: str, data: Dict[str, Any]) -> None:
    family = sanitize_css_value(str(data.get("font_family", "sans-serif")))
    style = soup.find("style")
    if style:
        style.string = (style.string or "") + f"\n{target or 'body'} {{ font-family: {family} !important; }}\n"


def _op_change_border_radius(soup: BeautifulSoup, target: str, data: Dict[str, Any]) -> None:
    radius = sanitize_css_value(str(data.get("border_radius", "999px")))
    style = soup.find("style")
    if style:
        selector = target or ".btn-primary, button, input, select"
        style.string = (style.string or "") + f"\n{selector} {{ border-radius: {radius} !important; }}\n"


def _op_change_spacing(soup: BeautifulSoup, target: str, data: Dict[str, Any]) -> None:
    padding = sanitize_css_value(str(data.get("padding", "")))
    margin = sanitize_css_value(str(data.get("margin", "")))
    elem = soup.select_one(target) if target else None
    if elem:
        style_parts = []
        if padding:
            style_parts.append(f"padding: {padding} !important")
        if margin:
            style_parts.append(f"margin: {margin} !important")
        elem["style"] = f"{elem.get('style', '')}; {'; '.join(style_parts)};".strip("; ")


def _op_add_section(soup: BeautifulSoup, target: str, data: Dict[str, Any]) -> None:
    title = str(data.get("title", "About Us")).strip()
    content = str(data.get("content", "Welcome to our café.")).strip()

    new_section = soup.new_tag("section", attrs={"class": "custom-section", "style": "padding: 4rem 2rem; max-width: 960px; margin: 0 auto; text-align: center;"})
    h2 = soup.new_tag("h2", style="font-size: 2rem; margin-bottom: 1rem; color: var(--text-primary);")
    h2.string = title
    p = soup.new_tag("p", style="color: var(--text-muted); font-size: 1.05rem; line-height: 1.7;")
    p.string = content

    new_section.append(h2)
    new_section.append(p)

    # Insert before footer
    footer = soup.find("footer")
    if footer:
        footer.insert_before(new_section)
    else:
        body = soup.find("body")
        if body:
            body.append(new_section)


def _op_remove_section(soup: BeautifulSoup, target: str) -> None:
    if not target:
        raise EditExecutionError("Target is required for remove_section.")
    elem = soup.select_one(target)
    if not elem:
        raise EditExecutionError(f"Section '{target}' was not found.")

    # Guard: must not contain booking elements
    for bid in MANDATORY_BOOKING_IDS:
        if elem.find(id=bid) or elem.get("id") == bid:
            raise EditExecutionError(f"Cannot remove section containing mandatory booking element '{bid}'.")

    elem.decompose()
