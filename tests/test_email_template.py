"""Tests for placeholder substitution in the email template."""

import pytest

from cold_email.prompts.email_template import TEMPLATE, fill_template


def test_fills_all_tokens_leaves_no_placeholders():
    values = {
        "first_name": "Kenny",
        "intro": "My name is Liyu, a CS student.",
        "company_interest": "how Turo handles car-sharing marketplace tech",
        "admiration_detail": "the high-ownership culture you've built",
        "experience_bullets": "- **IBM:** cut cluster calls 66%",
        "sender_first_name": "Liyu",
        "github_link": "[GitHub](https://github.com/liyuxiao2)",
        "linkedin_link": "[LinkedIn](https://linkedin.com/in/x)",
    }
    out = fill_template(TEMPLATE, values)
    assert "{{" not in out and "}}" not in out
    assert out.startswith("Hi Kenny,")
    assert "how Turo handles car-sharing marketplace tech" in out
    assert "- **IBM:** cut cluster calls 66%" in out
    assert "[GitHub](https://github.com/liyuxiao2)" in out


def test_missing_token_fails_loudly():
    # A template token with no matching value must NOT silently ship as literal.
    with pytest.raises(ValueError):
        fill_template("Hi {{first_name}}, {{company_interest}}", {"first_name": "K"})
