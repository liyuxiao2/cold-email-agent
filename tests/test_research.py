import pytest

from cold_email.workers.research import research_task

FAKE_UUID = "00000000-0000-0000-0000-000000000000"


def test_research_task_not_yet_implemented():
    """Placeholder — replace with real tests when research worker is implemented."""
    with pytest.raises(NotImplementedError):
        research_task.apply(args=[FAKE_UUID]).get(propagate=True)
