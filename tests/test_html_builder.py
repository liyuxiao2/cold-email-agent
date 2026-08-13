"""Tests for the markdown→HTML email rendering helpers."""

from cold_email.workers.drafting.helpers.html_builder import (
    markdown_to_html,
    plain_text_fallback,
)


def test_bold_becomes_strong():
    assert "<strong>Wealthsimple</strong>" in markdown_to_html("**Wealthsimple** rocks")


def test_bullet_lines_become_ul():
    html = markdown_to_html("- one\n- two")
    assert "<ul" in html and html.count("<li") == 2
    assert "one" in html and "two" in html


def test_markdown_link_becomes_anchor():
    html = markdown_to_html("[GitHub](https://github.com/liyuxiao2)")
    assert '<a href="https://github.com/liyuxiao2"' in html
    assert ">GitHub</a>" in html


def test_double_newline_splits_paragraphs():
    html = markdown_to_html("Hi there\n\nSecond para")
    assert html.count("<p") == 2


def test_html_entities_escaped():
    # A literal < in body text must not become a rogue tag.
    assert "&lt;script&gt;" in markdown_to_html("a<script>b")


def test_plain_fallback_strips_markdown():
    plain = plain_text_fallback("**bold** and [GitHub](https://gh.com)")
    assert "**" not in plain
    assert "bold" in plain
    # Link renders as "label (url)" so the URL survives in plain text.
    assert "GitHub" in plain and "https://gh.com" in plain
