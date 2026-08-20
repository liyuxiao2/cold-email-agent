import json
import pathlib

import pytest

from cold_email.profile_extract import (
    MAX_EXTRACTED_CHARS,
    MIN_EXTRACTED_CHARS,
    ResumeUnreadable,
    extract_text,
    suggest_profile,
)

FIXTURES = pathlib.Path("tests/fixtures")


def test_extracts_text_from_a_pdf():
    text = extract_text((FIXTURES / "sample_resume.pdf").read_bytes())
    assert len(text) > MIN_EXTRACTED_CHARS
    assert "Engineer" in text


def test_corrupt_pdf_raises_unreadable():
    with pytest.raises(ResumeUnreadable):
        extract_text(b"%PDF-1.7 truncated garbage")


def test_image_only_pdf_raises_unreadable():
    """A scanned résumé yields near-zero text. Passing that to the LLM produces
    a confidently fabricated profile, which is far worse than an error."""
    with pytest.raises(ResumeUnreadable, match="couldn't read text"):
        extract_text((FIXTURES / "image_only.pdf").read_bytes())


def test_extract_text_truncates_oversized_output(monkeypatch):
    """A dense text PDF can yield millions of characters. Since resume_text is
    committed before the LLM ever sees it, an unbounded value would land in
    every future drafting prompt forever (see profile_extract.MAX_EXTRACTED_CHARS)."""
    huge_text = "x" * (MAX_EXTRACTED_CHARS + 5_000)

    class FakePage:
        def extract_text(self):
            return huge_text

    class FakeReader:
        def __init__(self, _stream):
            self.pages = [FakePage()]

    monkeypatch.setattr("cold_email.profile_extract.PdfReader", FakeReader)

    result = extract_text(b"%PDF-1.7 fake")
    assert len(result) == MAX_EXTRACTED_CHARS


def test_suggest_profile_maps_the_llm_payload(monkeypatch):
    payload = {
        "name": "Liyu Xiao",
        "intro": "My name is Liyu, a CS student at McMaster.",
        "linkedin": "https://linkedin.com/in/liyu",
        "github": "https://github.com/liyuxiao2",
        "website": "https://liyuxiao.ca",
        "experience_pool": [
            "Wealthsimple: Cut logging latency by 80%.",
            "IBM: Built backend services for millions of learners.",
        ],
        "company_links": {"Wealthsimple": "https://www.wealthsimple.com"},
    }
    monkeypatch.setattr(
        "cold_email.profile_extract.generate_json", lambda **kw: json.dumps(payload)
    )

    result = suggest_profile("resume text " * 50)
    assert result["name"] == "Liyu Xiao"
    assert len(result["experience_pool"]) == 2
    assert result["company_links"]["Wealthsimple"].startswith("https://")


def test_suggested_bullets_survive_the_bullet_parser(monkeypatch):
    """_bullet_md does `label, sep, rest = bullet.partition(": ")`. Without the
    ': ' separator every bullet silently loses its bold label and its link."""
    payload = {
        "name": "A",
        "intro": "i",
        "linkedin": None,
        "github": None,
        "website": None,
        "experience_pool": ["Acme: shipped a thing", "Beta: shipped another"],
        "company_links": {},
    }
    monkeypatch.setattr(
        "cold_email.profile_extract.generate_json", lambda **kw: json.dumps(payload)
    )

    for bullet in suggest_profile("text " * 50)["experience_pool"]:
        assert ": " in bullet


def test_short_text_is_rejected_before_calling_the_llm(monkeypatch):
    called = False

    def spy(**kw):
        nonlocal called
        called = True
        return "{}"

    monkeypatch.setattr("cold_email.profile_extract.generate_json", spy)

    with pytest.raises(ResumeUnreadable):
        suggest_profile("too short")
    assert called is False


# ------------------------------------------------- Gemini schema compatibility


def _schema_keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _schema_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _schema_keys(item)


def test_resume_profile_schema_has_no_additional_properties():
    """The Gemini Developer API rejects `additionalProperties` outright, so any
    open-ended map on this model fails every résumé upload with a 503. A
    dict[str, str] field is the easy way to reintroduce it."""
    from cold_email.prompts.resume_profile import ResumeProfile

    offenders = [
        k for k in _schema_keys(ResumeProfile.model_json_schema()) if k == "additionalProperties"
    ]
    assert offenders == []


def test_links_to_dict_accepts_the_schema_bound_list():
    from cold_email.profile_extract import _links_to_dict

    assert _links_to_dict(
        [
            {"label": "Acme", "url": "https://acme.com"},
            {"label": "Globex", "url": "https://globex.com"},
        ]
    ) == {"Acme": "https://acme.com", "Globex": "https://globex.com"}


def test_links_to_dict_still_accepts_a_bare_map():
    """Groq gets the schema injected into its prompt rather than bound, so it can
    answer with the map shape regardless of what ResumeProfile declares."""
    from cold_email.profile_extract import _links_to_dict

    assert _links_to_dict({"Acme": "https://acme.com"}) == {"Acme": "https://acme.com"}


def test_links_to_dict_drops_half_links_and_junk():
    """A label with no URL would render a bold label pointing nowhere."""
    from cold_email.profile_extract import _links_to_dict

    assert (
        _links_to_dict(
            [
                {"label": "Acme"},
                {"url": "https://x.com"},
                "nonsense",
                {"label": "", "url": "https://y.com"},
            ]
        )
        == {}
    )
    assert _links_to_dict(None) == {}
