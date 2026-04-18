import pytest
from cold_email.workers.discovery import discovery_task


def test_discovery_task_not_yet_implemented():
    """Placeholder — replace with real tests when discovery worker is implemented."""
    with pytest.raises(NotImplementedError):
        discovery_task.apply().get(propagate=True)
