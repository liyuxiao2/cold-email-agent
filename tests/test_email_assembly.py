"""Tests for assemble_email: LLM context + profile → rendered draft."""

from cold_email.sender_profile import SenderProfile
from cold_email.workers.drafting.helpers.generation import assemble_email
from cold_email.workers.shared.views import PendingDraft

PROFILE = SenderProfile(
    name="Liyu Xiao",
    intro="My name is Liyu, a CS student at McMaster.",
    linkedin="https://linkedin.com/in/x",
    github="https://github.com/liyuxiao2",
    experience_pool=["Wealthsimple: did a thing"],
)


def _row(founder_name="Kenny Chan"):
    return PendingDraft(
        lead_id="00000000-0000-0000-0000-00000000000a",
        company_name="Turo",
        founder_name=founder_name,
        founder_email="kenny@turo.com",
        company_url="https://turo.com",
        raw_content="",
        tech_stack="Go",
        recent_news="Launched marketplace v2",
        hook="marketplace scaling",
    )


def _context():
    return {
        "subject": "Interested in Turo",
        "company_interest": "how Turo handles car-sharing marketplace technology",
        "admiration_detail": "the high-ownership culture you've built",
        "tailored_bullets": [
            "Wealthsimple: led an RESP engine for 3M+ clients",
            "IBM: cut cluster calls 66%",
        ],
    }


def test_assemble_produces_subject_body_and_html():
    draft = assemble_email(_context(), _row(), PROFILE)
    assert draft["subject"] == "Interested in Turo"
    # Greeting uses recipient's FIRST name only.
    assert draft["body"].startswith("Hi Kenny,")
    # Contextual slot woven in; no leftover template tokens.
    assert "how Turo handles car-sharing marketplace technology" in draft["body"]
    assert "{{" not in draft["body"]
    # Tailored bullets rendered with bold labels.
    assert "Wealthsimple" in draft["body"] and "IBM" in draft["body"]
    # HTML variant has real markup and a clickable link.
    assert "<ul" in draft["body_html"]
    assert '<a href="https://github.com/liyuxiao2"' in draft["body_html"]


def test_assemble_returns_empty_when_context_incomplete():
    ctx = _context()
    del ctx["company_interest"]
    assert assemble_email(ctx, _row(), PROFILE) == {}
