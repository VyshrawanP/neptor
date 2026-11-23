import difflib
import html
import re
from typing import Dict, Any, List
from bs4 import BeautifulSoup


def generate_raw_html_diff(original_html: str, proposed_html: str) -> str:
    """
    Generates a syntax-highlighted HTML diff with:
    - Red highlighting for removed lines.
    - Green highlighting for added lines.
    - Neutral styling for unchanged lines.
    All content is properly escaped to prevent untrusted script execution.
    """
    orig_lines = original_html.splitlines()
    prop_lines = proposed_html.splitlines()

    diff = difflib.unified_diff(
        orig_lines,
        prop_lines,
        fromfile="Published (Live)",
        tofile="Proposed (Draft)",
        lineterm=""
    )

    diff_html_lines: List[str] = [
        '<div class="diff-container" style="font-family: monospace; font-size: 13px; line-height: 1.5; background: #0f172a; color: #e2e8f0; border-radius: 8px; padding: 16px; overflow-x: auto; max-height: 500px;">'
    ]

    for line in diff:
        escaped = html.escape(line)
        if line.startswith("+++") or line.startswith("---"):
            diff_html_lines.append(
                f'<div style="color: #94a3b8; font-weight: bold; background: #1e293b; padding: 2px 6px;">{escaped}</div>'
            )
        elif line.startswith("@@"):
            diff_html_lines.append(
                f'<div style="color: #38bdf8; background: #0369a122; padding: 2px 6px;">{escaped}</div>'
            )
        elif line.startswith("+"):
            diff_html_lines.append(
                f'<div class="diff-line diff-ins" style="background-color: #14532d; color: #86efac; padding: 1px 6px; border-left: 3px solid #22c55e;">{escaped}</div>'
            )
        elif line.startswith("-"):
            diff_html_lines.append(
                f'<div class="diff-line diff-del" style="background-color: #7f1d1d; color: #fca5a5; padding: 1px 6px; border-left: 3px solid #ef4444; text-decoration: line-through;">{escaped}</div>'
            )
        else:
            diff_html_lines.append(
                f'<div class="diff-line diff-neutral" style="color: #cbd5e1; padding: 1px 6px;">{escaped}</div>'
            )

    diff_html_lines.append("</div>")
    return "\n".join(diff_html_lines)


def extract_css_variables(html_content: str) -> Dict[str, str]:
    """Extracts CSS root variables from style tags."""
    vars_dict: Dict[str, str] = {}
    soup = BeautifulSoup(html_content, "html.parser")
    for style in soup.find_all("style"):
        if style.string:
            matches = re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", style.string)
            for var_name, var_val in matches:
                vars_dict[var_name.strip()] = var_val.strip()
    return vars_dict


def generate_structured_changes(original_html: str, proposed_html: str) -> Dict[str, Any]:
    """
    Analyzes actual DOM differences and style modifications.
    Only reports changes that are genuinely supported by the difference.
    """
    orig_soup = BeautifulSoup(original_html, "html.parser")
    prop_soup = BeautifulSoup(proposed_html, "html.parser")

    added_elements: List[Dict[str, Any]] = []
    removed_elements: List[Dict[str, Any]] = []
    modified_attributes_and_styles: List[Dict[str, Any]] = []
    summary_points: List[str] = []

    # 1. Compare Page Title
    orig_title = orig_soup.title.string.strip() if orig_soup.title and orig_soup.title.string else ""
    prop_title = prop_soup.title.string.strip() if prop_soup.title and prop_soup.title.string else ""
    if orig_title != prop_title and prop_title:
        modified_attributes_and_styles.append({
            "element": "title",
            "from": orig_title,
            "to": prop_title
        })
        summary_points.append(f"Updated page title from '{orig_title}' to '{prop_title}'.")

    # 2. Compare CSS Variables (e.g. colors, accent, fonts)
    orig_vars = extract_css_variables(original_html)
    prop_vars = extract_css_variables(proposed_html)

    for k, v in prop_vars.items():
        if k not in orig_vars:
            added_elements.append({"type": "css_variable", "name": k, "value": v})
            summary_points.append(f"Added CSS variable {k}: {v}.")
        elif orig_vars[k] != v:
            modified_attributes_and_styles.append({
                "type": "css_variable",
                "name": k,
                "from": orig_vars[k],
                "to": v
            })
            summary_points.append(f"Changed CSS variable {k} from '{orig_vars[k]}' to '{v}'.")

    for k, v in orig_vars.items():
        if k not in prop_vars:
            removed_elements.append({"type": "css_variable", "name": k, "value": v})
            summary_points.append(f"Removed CSS variable {k}.")

    # 3. Compare Brand / Logo
    orig_logo = orig_soup.find(class_="logo")
    prop_logo = prop_soup.find(class_="logo")
    if orig_logo and prop_logo:
        orig_logo_text = orig_logo.get_text(strip=True)
        prop_logo_text = prop_logo.get_text(strip=True)
        if orig_logo_text != prop_logo_text:
            modified_attributes_and_styles.append({
                "element": "logo",
                "from": orig_logo_text,
                "to": prop_logo_text
            })
            summary_points.append(f"Updated brand logo text from '{orig_logo_text}' to '{prop_logo_text}'.")

    # 4. Compare Hero Heading and Description
    orig_hero_h1 = orig_soup.select_one(".hero h1")
    prop_hero_h1 = prop_soup.select_one(".hero h1")
    if orig_hero_h1 and prop_hero_h1:
        orig_h1_text = orig_hero_h1.get_text(strip=True)
        prop_h1_text = prop_hero_h1.get_text(strip=True)
        if orig_h1_text != prop_h1_text:
            modified_attributes_and_styles.append({
                "element": "hero_heading",
                "from": orig_h1_text,
                "to": prop_h1_text
            })
            summary_points.append(f"Updated hero heading from '{orig_h1_text}' to '{prop_h1_text}'.")

    orig_hero_p = orig_soup.select_one(".hero p")
    prop_hero_p = prop_soup.select_one(".hero p")
    if orig_hero_p and prop_hero_p:
        orig_p_text = orig_hero_p.get_text(strip=True)
        prop_p_text = prop_hero_p.get_text(strip=True)
        if orig_p_text != prop_p_text:
            modified_attributes_and_styles.append({
                "element": "hero_description",
                "from": orig_p_text,
                "to": prop_p_text
            })
            summary_points.append("Updated hero description text.")

    # 5. Compare Menu Items
    def get_menu_items(soup: BeautifulSoup) -> Dict[str, str]:
        items: Dict[str, str] = {}
        # Solstice café starter site has menu items or list items
        for item in soup.select(".menu-item, .menu-card, [data-menu-item]"):
            title_elem = item.find(["h3", "h4", "strong", ".item-name"])
            price_elem = item.find([".price", ".item-price", "span"])
            title = title_elem.get_text(strip=True) if title_elem else item.get_text(strip=True)[:30]
            price = price_elem.get_text(strip=True) if price_elem else ""
            items[title] = price
        return items

    orig_menu = get_menu_items(orig_soup)
    prop_menu = get_menu_items(prop_soup)

    for item, price in prop_menu.items():
        if item not in orig_menu:
            added_elements.append({"type": "menu_item", "name": item, "price": price})
            summary_points.append(f"Added menu item: '{item}' ({price})." if price else f"Added menu item: '{item}'.")
        elif orig_menu[item] != price and price:
            modified_attributes_and_styles.append({
                "type": "menu_item",
                "name": item,
                "from_price": orig_menu[item],
                "to_price": price
            })
            summary_points.append(f"Updated price of '{item}' from '{orig_menu[item]}' to '{price}'.")

    for item, price in orig_menu.items():
        if item not in prop_menu:
            removed_elements.append({"type": "menu_item", "name": item})
            summary_points.append(f"Removed menu item: '{item}'.")

    # 6. Fallback if no specific high-level points were found but text changed
    if not summary_points:
        orig_text = orig_soup.get_text()
        prop_text = prop_soup.get_text()
        if orig_text != prop_text:
            summary_points.append("Modified website content and text elements.")
        else:
            summary_points.append("Modified website structure or styling.")

    readable_summary = " ".join(summary_points)

    return {
        "added_elements": added_elements,
        "removed_elements": removed_elements,
        "modified_attributes_and_styles": modified_attributes_and_styles,
        "summary_points": summary_points,
        "readable_summary": readable_summary,
    }


def generate_diff_and_summary(original_html: str, proposed_html: str) -> Dict[str, Any]:
    """Combines raw HTML diff and structured analysis."""
    html_diff = generate_raw_html_diff(original_html, proposed_html)
    structured = generate_structured_changes(original_html, proposed_html)

    return {
        "html_diff": html_diff,
        "structured_changes": structured,
        "change_summary": structured["readable_summary"],
        "summary_points": structured["summary_points"],
    }
