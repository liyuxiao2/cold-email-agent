import pytest

from cold_email.workers.logistics import logistics_task

FAKE_UUID = "00000000-0000-0000-0000-000000000000"


def test_logistics_task_not_yet_implemented():
    """Placeholder — replace with real tests when logistics worker is implemented."""
    with pytest.raises(NotImplementedError):
        logistics_task.apply(args=[FAKE_UUID]).get(propagate=True)
