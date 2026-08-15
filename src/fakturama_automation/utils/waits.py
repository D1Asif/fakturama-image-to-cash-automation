import time

from fakturama_automation.workflow.errors import AutomationError

DEFAULT_TIMEOUT = 10
POLL_INTERVAL = 0.25


def wait_for_control(parent, timeout: float = DEFAULT_TIMEOUT, retry_interval: float = POLL_INTERVAL, **criteria):
    """Wait until a child control matching criteria exists and is visible, then return it."""
    child = parent.child_window(**criteria)
    try:
        child.wait("visible", timeout=timeout, retry_interval=retry_interval)
    except Exception as exc:
        raise AutomationError(f"Control not found: {criteria}") from exc
    return child


def wait_until_enabled(control, timeout: float = DEFAULT_TIMEOUT, retry_interval: float = POLL_INTERVAL):
    try:
        control.wait("enabled", timeout=timeout, retry_interval=retry_interval)
    except Exception as exc:
        raise AutomationError(f"Control never became enabled: {control}") from exc
    return control


def wait_for_results_stable(
    get_row_count,
    timeout: float = DEFAULT_TIMEOUT,
    stable_for: float = 0.5,
    poll_interval: float = POLL_INTERVAL,
) -> int:
    """Poll get_row_count() until it returns the same value for `stable_for` seconds."""
    deadline = time.monotonic() + timeout
    last_count = get_row_count()
    stable_since = time.monotonic()

    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        count = get_row_count()
        if count != last_count:
            last_count = count
            stable_since = time.monotonic()
        elif time.monotonic() - stable_since >= stable_for:
            return count

    raise AutomationError("Search results did not stabilize before timeout")
