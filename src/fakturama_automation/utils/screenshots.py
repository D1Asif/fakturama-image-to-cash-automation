import ctypes
from datetime import datetime
from pathlib import Path

import win32con
import win32gui
import win32ui
from PIL import Image

EVIDENCE_DIR = Path("evidence")
PW_RENDERFULLCONTENT = 2


def _capture_hwnd(hwnd: int) -> Image.Image:
    """Capture a window's content via PrintWindow rather than a screen-region grab.

    A plain ImageGrab.grab() over the window's screen rectangle produces a
    solid black image in this environment (no real compositor surface behind
    the coordinates), and a minimized window reports a degenerate/off-screen
    rectangle entirely. PrintWindow(..., PW_RENDERFULLCONTENT) asks the
    window to render its own content into a supplied device context and
    works regardless of occlusion or virtual-desktop placement.
    """
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
    save_dc.SelectObject(bitmap)

    try:
        ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
        info = bitmap.GetInfo()
        bits = bitmap.GetBitmapBits(True)
        return Image.frombuffer(
            "RGB", (info["bmWidth"], info["bmHeight"]), bits, "raw", "BGRX", 0, 1
        )
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)


def take_screenshot(hwnd: int, name: str) -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_DIR / f"{name}.png"
    _capture_hwnd(hwnd).save(path)
    return path


def take_error_screenshot(hwnd: int, reason: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return take_screenshot(hwnd, f"error_{timestamp}_{reason}")
