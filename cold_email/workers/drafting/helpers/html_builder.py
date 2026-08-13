"""Markdown → inline-styled HTML for polished Gmail drafts, plus a plain-text
fallback. Adapted from the ColdApproach-AI reference html_builder.

We support only the lightweight markdown the template produces: **bold**,
`- ` bullet lists, [label](url) links, and blank-line paragraph breaks. Inline
styles (not a stylesheet) because Gmail strips <style> blocks.
"""

import re

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def markdown_to_html(text: str) -> str:
    """Convert the template's markdown subset to inline-styled HTML."""
    # Escape entities first so real body text can't inject tags.
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = _LINK_RE.sub(
        r'<a href="\2" style="color:#1a73e8;text-decoration:underline;">\1</a>', text
    )

    html_parts: list[str] = []
    for para in re.split(r"\n{2,}", text.strip()):
        lines = [ln.strip() for ln in para.split("\n") if ln.strip()]
        if lines and all(ln.startswith("- ") for ln in lines):
            items = "".join(
                f'<li style="margin-bottom:4px;">{ln[2:]}</li>' for ln in lines
            )
            html_parts.append(f'<ul style="margin:8px 0;padding-left:20px;">{items}</ul>')
        else:
            html_parts.append(f'<p style="margin:0 0 12px 0;">{"<br>".join(lines)}</p>')
    return "\n".join(html_parts)


def plain_text_fallback(text: str) -> str:
    """Strip markdown to a readable plain-text body (links become 'label (url)')."""
    text = _BOLD_RE.sub(r"\1", text)
    text = _LINK_RE.sub(r"\1 (\2)", text)
    return text
