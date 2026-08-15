import pytest

VALID_PDF = b"%PDF-1.7\n" + b"x" * 2048


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/profile"),
        ("PUT", "/api/profile"),
        ("POST", "/api/profile/resume"),
        ("GET", "/api/profile/resume"),
        ("DELETE", "/api/profile/resume"),
    ],
)
async def test_all_profile_routes_require_auth(client, method, path):
    assert (await client.request(method, path)).status_code == 401


@pytest.mark.asyncio
async def test_get_profile_never_returns_the_pdf_bytes(user_client, seeded_profile):
    """Beyond payload size: base64-ing a PDF into every profile fetch pulls the
    bytes out of the TOAST table on a request that only wanted a name — exactly
    the cost STORAGE EXTERNAL was chosen to avoid."""
    body = (await user_client.get("/api/profile")).json()
    assert "resume_pdf" not in body
    assert body["has_resume"] is True
    assert body["resume_filename"] == "cv.pdf"


@pytest.mark.asyncio
async def test_put_profile_rejects_an_empty_name(user_client, seeded_profile):
    response = await user_client.put("/api/profile", json={"name": "", "intro": "hi"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_upload_over_the_cap_is_413(user_client, seeded_profile):
    from cold_email.resume_store import MAX_RESUME_BYTES

    response = await user_client.post(
        "/api/profile/resume",
        files={"file": ("big.pdf", b"%PDF-" + b"x" * MAX_RESUME_BYTES, "application/pdf")},
    )
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_upload_without_pdf_magic_bytes_is_415(user_client, seeded_profile):
    response = await user_client.post(
        "/api/profile/resume",
        files={"file": ("evil.pdf", b"MZ\x90\x00", "application/pdf")},
    )
    assert response.status_code == 415


@pytest.mark.asyncio
async def test_unparseable_pdf_is_422_and_stores_nothing(
    user_client, seeded_profile, async_session
):
    """A PDF pypdf can't read is rejected before put_resume ever runs, so it
    must not clobber whatever résumé was already on the row (here, the one
    seeded_profile stored) — "stores nothing" means no NEW bytes land, not
    that a working résumé gets wiped by an unrelated bad re-upload."""
    from cold_email.database import Profile

    original_resume = bytes(seeded_profile.resume_pdf)

    response = await user_client.post(
        "/api/profile/resume",
        files={"file": ("bad.pdf", b"%PDF-1.7 truncated", "application/pdf")},
    )
    assert response.status_code == 422

    profile = await async_session.get(Profile, seeded_profile.user_id)
    await async_session.refresh(profile)
    assert bytes(profile.resume_pdf) == original_resume


@pytest.mark.asyncio
async def test_llm_failure_keeps_the_uploaded_bytes(
    user_client, seeded_profile, async_session, monkeypatch
):
    """The upload succeeded and only the SUGGESTION failed. Discarding a 5MB
    upload the user just waited for would be gratuitous."""
    from cold_email.api.routes import profile as profile_routes

    monkeypatch.setattr(profile_routes, "extract_text", lambda data: "text " * 100)

    def boom(text):
        raise RuntimeError("all models exhausted")

    monkeypatch.setattr(profile_routes, "suggest_profile", boom)

    response = await user_client.post(
        "/api/profile/resume", files={"file": ("cv.pdf", VALID_PDF, "application/pdf")}
    )
    assert response.status_code == 503

    from cold_email.database import Profile

    profile = await async_session.get(Profile, seeded_profile.user_id)
    await async_session.refresh(profile)
    assert profile.resume_pdf is not None  # bytes survived


@pytest.mark.asyncio
async def test_upload_returns_a_suggestion_without_committing_it(
    user_client, seeded_profile, async_session, monkeypatch
):
    """The LLM will occasionally mangle a name. Every draft this user sends is
    built from these fields, so nothing is authoritative until they confirm."""
    from cold_email.api.routes import profile as profile_routes

    monkeypatch.setattr(profile_routes, "extract_text", lambda data: "text " * 100)
    monkeypatch.setattr(
        profile_routes,
        "suggest_profile",
        lambda text: {
            "name": "Suggested Name",
            "intro": "Suggested intro.",
            "linkedin": None,
            "github": None,
            "website": None,
            "experience_pool": ["Acme: a thing"],
            "company_links": {},
        },
    )

    body = (
        await user_client.post(
            "/api/profile/resume", files={"file": ("cv.pdf", VALID_PDF, "application/pdf")}
        )
    ).json()
    assert body["suggested"]["name"] == "Suggested Name"

    from cold_email.database import Profile

    profile = await async_session.get(Profile, seeded_profile.user_id)
    await async_session.refresh(profile)
    assert profile.name != "Suggested Name"  # not committed


@pytest.mark.asyncio
async def test_download_returns_only_the_callers_own_resume(
    user_client, seeded_profile, other_users_profile
):
    """Tenancy isolation: a user must never receive another user's résumé."""
    response = await user_client.get("/api/profile/resume")
    assert response.status_code == 200
    assert response.content == VALID_PDF  # the caller's, not the other user's


@pytest.mark.asyncio
async def test_me_reports_profile_completeness(user_client, seeded_profile):
    body = (await user_client.get("/api/auth/me")).json()
    assert body["profile_complete"] is True
