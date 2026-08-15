import pytest

from cold_email.sender_profile import SenderProfile


def test_module_constant_is_gone():
    """One person's identity must no longer be compiled into the codebase."""
    import cold_email.sender_profile as sp

    assert not hasattr(sp, "PROFILE")
    assert not hasattr(sp, "load_resume")


@pytest.mark.asyncio
async def test_from_row_maps_every_field(async_session, admin_user_id):
    from cold_email.database import Profile

    row = Profile(
        user_id=admin_user_id,
        name="Liyu Xiao",
        intro="My name is Liyu.",
        linkedin="https://linkedin.com/in/liyu",
        github="https://github.com/liyuxiao2",
        website="https://liyuxiao.ca",
        experience_pool=["Acme: shipped a thing"],
        company_links={"Acme": "https://acme.com"},
        resume_text="full résumé text",
    )
    async_session.add(row)
    await async_session.commit()

    profile = SenderProfile.from_row(row)
    assert profile.name == "Liyu Xiao"
    assert profile.first_name == "Liyu"
    assert profile.github == "https://github.com/liyuxiao2"
    assert profile.experience_pool == ["Acme: shipped a thing"]
    assert profile.company_links == {"Acme": "https://acme.com"}
    assert profile.effective_resume_text == "full résumé text"


@pytest.mark.asyncio
async def test_effective_resume_text_falls_back_to_the_pool(async_session, admin_user_id):
    """A user who fills the form manually without uploading a PDF still needs
    résumé text for the drafting prompt."""
    from cold_email.database import Profile

    row = Profile(
        user_id=admin_user_id,
        name="A B",
        intro="I am A.",
        experience_pool=["Acme: did a thing"],
        resume_text=None,
    )
    async_session.add(row)
    await async_session.commit()

    text = SenderProfile.from_row(row).effective_resume_text
    assert "I am A." in text
    assert "Acme: did a thing" in text


def test_from_row_tolerates_null_json_columns():
    class _Row:
        name, intro = "A B", "i"
        linkedin = github = website = resume_text = None
        experience_pool = company_links = None

    profile = SenderProfile.from_row(_Row())
    assert profile.experience_pool == []
    assert profile.company_links == {}
