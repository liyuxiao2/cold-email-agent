"""Tests for placeholder substitution in the email template."""

import pytest

from cold_email.prompts.email_template import TEMPLATE, fill_template


def test_fills_all_tokens_leaves_no_placeholders():
    values = {
        "first_name": "Kenny",
        "intro": "My name is Liyu, a CS student.",
        "why_company": "I've been following how Turo handles marketplace technology and would love to contribute.",
        "experience_bullets": "- **IBM:** cut cluster calls 66%",
        "sender_first_name": "Liyu",
        "github_link": "[GitHub](https://github.com/liyuxiao2)",
        "linkedin_link": "[LinkedIn](https://linkedin.com/in/x)",
    }
    out = fill_template(TEMPLATE, values)
    assert "{{" not in out and "}}" not in out
    assert out.startswith("Hi Kenny,")
    assert "I've been following how Turo handles marketplace technology" in out
    assert "- **IBM:** cut cluster calls 66%" in out
    assert "[GitHub](https://github.com/liyuxiao2)" in out


def test_missing_token_fails_loudly():
    with pytest.raises(ValueError):
        fill_template("Hi {{first_name}}, {{why_company}}", {"first_name": "K"})
