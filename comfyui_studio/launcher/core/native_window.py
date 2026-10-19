"""Windows desktop window discovery, scoped to an exact executable and PID."""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

import psutil


def find_app_window(executable: str, pids: set[int] | None = None) -> tuple[int, int] | None:
    if sys.platform != "win32":
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    expected = os.path.normcase(os.path.realpath(executable))
    matches = []

    @callback_type
    def visit(hwnd, _data):
        # Owned dialogs and tiny/untitled helper windows are not the desktop UI.
        if user32.GetWindow(hwnd, 4) or not user32.GetWindowTextLengthW(hwnd):  # GW_OWNER
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pids is not None and pid.value not in pids:
            return True
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width < 200 or height < 100:
            return True
        try:
            actual = os.path.normcase(os.path.realpath(psutil.Process(pid.value).exe()))
        except (psutil.Error, OSError):
            return True
        if actual == expected:
            matches.append((bool(user32.IsWindowVisible(hwnd)), width * height, int(hwnd), pid.value))
        return True

    if not user32.EnumWindows(visit, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    if not matches:
        return None
    _visible, _area, hwnd, pid = max(matches)
    return hwnd, pid


def window_exists(hwnd: int) -> bool:
    if sys.platform != "win32":
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    return bool(user32.IsWindow(hwnd))
