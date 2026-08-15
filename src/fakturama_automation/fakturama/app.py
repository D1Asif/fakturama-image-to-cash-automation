import time

import win32con
import win32gui
from pywinauto import Application

from fakturama_automation.utils.logging import log
from fakturama_automation.utils.screenshots import take_error_screenshot, take_screenshot
from fakturama_automation.utils.waits import wait_for_control, wait_until_enabled
from fakturama_automation.workflow.errors import AutomationError

FAKTURAMA_TITLE_PREFIX = "Fakturama - "


def _find_fakturama_hwnd(title_prefix: str = FAKTURAMA_TITLE_PREFIX) -> int:
    """Locate the Fakturama main window handle via raw win32 enumeration.

    Deliberately avoids pywinauto's title_re/Application.top_window() search:
    against this app that search walks the entire desktop's UIA tree across
    every process and can hang for minutes. A direct win32 handle lookup
    resolves in milliseconds, and once pywinauto is handed that handle,
    scoped UIA operations on the window's own subtree are fast.
    """
    matches: list[int] = []

    def _enum_handler(hwnd: int, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd).startswith(title_prefix):
            matches.append(hwnd)

    win32gui.EnumWindows(_enum_handler, None)

    if not matches:
        raise AutomationError(f"No visible window found with title starting '{title_prefix}'")
    if len(matches) > 1:
        raise AutomationError(f"Multiple Fakturama windows found: {matches}")
    return matches[0]


def _find_hwnd_by_exact_title(title: str) -> int | None:
    matches: list[int] = []

    def _enum_handler(hwnd: int, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == title:
            matches.append(hwnd)

    win32gui.EnumWindows(_enum_handler, None)
    return matches[0] if matches else None


class FakturamaApp:
    """Central wrapper around the pywinauto/UIA connection to Fakturama.

    All raw pywinauto interaction should go through this class so the rest
    of the workflow never touches screen coordinates or backend details
    directly.
    """

    def __init__(self):
        self.app: Application | None = None
        self.main_window = None
        self.hwnd: int | None = None

    def connect(self, title_prefix: str = FAKTURAMA_TITLE_PREFIX):
        try:
            hwnd = _find_fakturama_hwnd(title_prefix)
            self.app = Application(backend="uia").connect(handle=hwnd)
            self.main_window = self.app.window(handle=hwnd)
            self.hwnd = hwnd
        except AutomationError:
            raise
        except Exception as exc:
            raise AutomationError(f"Could not connect to Fakturama: {exc}") from exc
        log.info("Connected to Fakturama")
        return self.main_window

    def focus(self):
        """Bring the Fakturama window to the foreground.

        click_input() sends a real OS-level click at screen coordinates; if
        Fakturama isn't the foreground window the click silently lands on
        whatever is on top instead (no error raised). Every button click
        must be preceded by this.
        """
        if win32gui.IsIconic(self.hwnd):
            win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(self.hwnd)

    def click(self, parent=None, timeout: float = 10, **criteria):
        control = self.wait_for_control(parent, timeout=timeout, **criteria)
        self.wait_until_enabled(control, timeout=timeout)
        self.focus()
        control.click_input()
        return control

    def wait_for_control(self, parent=None, timeout: float = 10, **criteria):
        parent = parent or self.main_window
        return wait_for_control(parent, timeout=timeout, **criteria)

    def wait_until_enabled(self, control, timeout: float = 10):
        return wait_until_enabled(control, timeout=timeout)

    def find_control(self, parent=None, **criteria):
        parent = parent or self.main_window
        return parent.child_window(**criteria)

    def close_tab(self, tab_title: str, timeout: float = 10):
        """Close a document tab via Ctrl+F4.

        If the tab has unsaved changes, Fakturama prompts with a "Save
        Parts" dialog; its OK button saves the checked items and proceeds
        with the close, while Cancel aborts the close entirely (leaving the
        tab open and still dirty). There is no plain discard option
        exposed here, so this always saves.
        """
        try:
            tab_item = self.main_window.child_window(title=tab_title, control_type="TabItem")
            tab_item.wait("visible", timeout=2)
        except Exception:
            return  # nothing to close

        self.focus()
        tab_item.click_input()
        time.sleep(0.3)
        self.main_window.type_keys("^{F4}")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            hwnd = _find_hwnd_by_exact_title("Save Parts")
            if hwnd:
                dlg_app = Application(backend="uia").connect(handle=hwnd)
                dlg = dlg_app.window(handle=hwnd)
                win32gui.SetForegroundWindow(hwnd)
                ok = dlg.child_window(title="OK", control_type="Button")
                ok.wait("visible enabled", timeout=5)
                ok.click_input()
                return
            time.sleep(0.2)

    def dismiss_dialog_if_present(self, title: str, button: str = "OK", wait: float = 0.5):
        """Click a named button on a transient top-level dialog if it's open.

        Fakturama pops native message boxes for things like "Duplicate
        Contact" as a live-validation warning while filling in a form, not
        just on save. These are separate top-level windows (unlike the
        "address type" role popup), so they're found by exact title via
        win32, not through the main window's UIA tree.
        """
        hwnd = _find_hwnd_by_exact_title(title)
        if not hwnd:
            return False
        dlg_app = Application(backend="uia").connect(handle=hwnd)
        dlg = dlg_app.window(handle=hwnd)
        win32gui.SetForegroundWindow(hwnd)
        btn = dlg.child_window(title=button, control_type="Button")
        btn.wait("visible enabled", timeout=5)
        btn.click_input()
        time.sleep(wait)
        return True

    def take_screenshot(self, name: str):
        return take_screenshot(self.hwnd, name)

    def take_error_screenshot(self, reason: str):
        return take_error_screenshot(self.hwnd, reason)
