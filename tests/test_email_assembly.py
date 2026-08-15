"""Tests for assemble_email: LLM context + profile → rendered draft."""

import pytest

from cold_email.sender_profile import SenderProfile
from cold_email.workers.drafting.helpers.generation import assemble_email
from cold_email.workers.shared.views import PendingDraft

PROFILE = SenderProfile(
    name="Liyu Xiao",
    intro="My name is Liyu, a CS student at McMaster.",
    linkedin="https://linkedin.com/in/x",
    github="https://github.com/liyuxiao2",
    website="https://liyuxiao.ca",
    experience_pool=["Wealthsimple: did a thing"],
    company_links={"Wealthsimple": "https://wealthsimple.com", "IBM": "https://ibm.com"},
)


@pytest.fixture
def profile():
    return PROFILE


def _row(founder_name="Kenny Chan"):
    return PendingDraft(
        outreach_id="00000000-0000-0000-0000-00000000000a",
        user_id="00000000-0000-0000-0000-00000000000b",
        company_id="00000000-0000-0000-0000-00000000000c",
        contact_id="00000000-0000-0000-0000-00000000000d",
        company_name="Turo",
        company_url="https://turo.com",
        founder_name=founder_name,
        contact_email="kenny@turo.com",
        contact_first_name=founder_name.split()[0] if founder_name else None,
        contact_last_name=founder_name.split()[-1] if founder_name else None,
        contact_position="Founder",
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
        "intro": "My name is Liyu, a CS student at McMaster.",
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
    # Tailored bullets rendered with bold labels and embedded links.
    assert "Wealthsimple (https://wealthsimple.com)" in draft["body"]
    assert "IBM (https://ibm.com)" in draft["body"]
    # HTML variant has real markup and a clickable link.
    assert "<ul" in draft["body_html"]
    assert "GitHub (https://github.com/liyuxiao2)" in draft["body"]
    assert (
        '<a href="https://wealthsimple.com" style="color:#1a73e8;text-decoration:underline;">Wealthsimple</a>'
        in draft["body_html"]
    )
    assert (
        '<a href="https://ibm.com" style="color:#1a73e8;text-decoration:underline;">IBM</a>'
        in draft["body_html"]
    )


def test_assemble_returns_empty_when_context_incomplete():
    ctx = _context()
    del ctx["company_interest"]
    assert assemble_email(ctx, _row(), PROFILE) == {}


def test_greeting_uses_the_contact_not_the_founder(profile):
    """A user emailing the CTO must not be greeted by the founder's name.

    This is the most visible way contact spreading could embarrass a user, and
    it is a one-line mistake to make.
    """
    from cold_email.workers.drafting.helpers.generation import assemble_email
    from cold_email.workers.shared.views import PendingDraft

    row = PendingDraft(
        outreach_id="o1",
        user_id="u1",
        company_id="c1",
        contact_id="ct1",
        company_name="Acme",
        company_url="https://acme.com",
        founder_name="Ann Reed",  # the founder
        contact_email="bo@acme.com",
        contact_first_name="Bo",  # but we are emailing Bo
        contact_last_name="Lin",
        contact_position="CTO",
        raw_content="raw",
        tech_stack=["python"],
        recent_news="news",
        hook="hook",
    )
    context = {
        "subject": "Acme",
        "company_interest": "x",
        "admiration_detail": "y",
        "intro": "I'm someone.",
        "tailored_bullets": ["A: did a thing"],
    }
    result = assemble_email(context, row, profile)
    assert "Hi Bo," in result["body"]
    assert "Ann" not in result["body"]


def test_greeting_falls_back_when_the_contact_has_no_first_name(profile):
    from cold_email.workers.drafting.helpers.generation import assemble_email
    from cold_email.workers.shared.views import PendingDraft

    row = PendingDraft(
        outreach_id="o1",
        user_id="u1",
        company_id="c1",
        contact_id="ct1",
        company_name="Acme",
        company_url="https://acme.com",
        founder_name=None,
        contact_email="team@acme.com",
        contact_first_name=None,
        contact_last_name=None,
        contact_position="CTO",
        raw_content="raw",
        tech_stack=["python"],
        recent_news="n",
        hook="h",
    )
    context = {
        "subject": "Acme",
        "company_interest": "x",
        "admiration_detail": "y",
        "intro": "I'm someone.",
        "tailored_bullets": ["A: did a thing"],
    }
    assert "Hi there," in assemble_email(context, row, profile)["body"]


def test_generate_email_addresses_the_llm_prompt_by_full_contact_name(monkeypatch):
    """contact_last_name comes from the pending_drafts view (migrations/006 +
    views.sql) and the PendingDraft DTO; generate_email's recipient_name
    should use the CONTACT's full name for the LLM prompt when a last name is
    on file, not just the first name."""
    from cold_email.workers.drafting.helpers.generation import generate_email

    captured = {}

    def fake_generate_json(system, prompt, schema):
        captured["prompt"] = prompt
        return "{}"

    monkeypatch.setattr(
        "cold_email.workers.drafting.helpers.generation.generate_json", fake_generate_json
    )

    row = PendingDraft(
        outreach_id="o1",
        user_id="u1",
        company_id="c1",
        contact_id="ct1",
        company_name="Acme",
        company_url="https://acme.com",
        founder_name="Ann Reed",
        contact_email="bo@acme.com",
        contact_first_name="Bo",
        contact_last_name="Lin",
        contact_position="CTO",
        raw_content="raw",
        tech_stack=["python"],
        recent_news="news",
        hook="hook",
    )
    generate_email(row, PROFILE)
    assert "Bo Lin" in str(captured["prompt"])


def test_generate_email_falls_back_to_first_name_only_without_a_last_name(monkeypatch):
    from cold_email.workers.drafting.helpers.generation import generate_email

    captured = {}

    def fake_generate_json(system, prompt, schema):
        captured["prompt"] = prompt
        return "{}"

    monkeypatch.setattr(
        "cold_email.workers.drafting.helpers.generation.generate_json", fake_generate_json
    )

    row = PendingDraft(
        outreach_id="o1",
        user_id="u1",
        company_id="c1",
        contact_id="ct1",
        company_name="Acme",
        company_url="https://acme.com",
        founder_name=None,
        contact_email="bo@acme.com",
        contact_first_name="Bo",
        contact_last_name=None,
        contact_position="CTO",
        raw_content="raw",
        tech_stack=["python"],
        recent_news="news",
        hook="hook",
    )
    generate_email(row, PROFILE)
    assert "Bo" in str(captured["prompt"])
    assert "Bo Lin" not in str(captured["prompt"])


def test_recipient_title_constant_is_gone():
    """'Founder' was hardcoded because there was no title column. There is now,
    and the recipient is frequently not a founder."""
    import cold_email.prompts.email_draft as ed

    assert not hasattr(ed, "RECIPIENT_TITLE")


def test_prompt_carries_the_contacts_real_position():
    from cold_email.prompts.email_draft import build_email_draft_messages

    prompt = build_email_draft_messages(
        recipient_name="Bo Lin",
        recipient_position="CTO",
        company_name="Acme",
        tech_stack=["python"],
        recent_news="news",
        hook="hook",
        resume_text="resume",
    )
    assert "CTO" in prompt
    assert "Bo Lin" in prompt


def test_prompt_falls_back_to_founder_when_position_is_missing():
    from cold_email.prompts.email_draft import build_email_draft_messages

    prompt = build_email_draft_messages(
        recipient_name="Bo Lin",
        recipient_position=None,
        company_name="Acme",
        tech_stack=[],
        recent_news="",
        hook="",
        resume_text="r",
    )
    assert "Founder" in prompt
