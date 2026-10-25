"""Local policy verification and transparent screen-time accounting."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import ctypes
from ctypes import wintypes
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import time


def canonical_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def verify_policy(policy: dict, key: str) -> bool:
    raw = canonical_json({"version": policy["version"], "payload": policy["payload"]})
    digest = hmac.new(key.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, policy["signature"])


def state_path() -> Path:
    root = Path(os.getenv("LOCALAPPDATA") or Path.home()) / "OGK"
    root.mkdir(parents=True, exist_ok=True)
    return root / "state.json"


def load_state() -> dict:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"day": "", "used_seconds": 0, "policy": None}


def save_state(state: dict) -> None:
    path = state_path()
    fd, temporary = tempfile.mkstemp(prefix="ogk-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class UsageTracker:
    def __init__(self, state: dict):
        self.state = state
        self.last_tick = time.monotonic()
        self.day = str(state.get("day", ""))
        self.used_seconds = int(state.get("used_seconds", 0))
        self.warning_marks = set(state.get("warning_marks", []))
        self.over_quota_since: float | None = None

    def tick(self, policy: dict, now_utc: datetime, is_active: bool) -> dict:
        now_mono = time.monotonic()
        # Include brief HTTP/event waits; a 2-second cap would undercount each
        # periodic heartbeat by several minutes over an hour.
        elapsed = max(0.0, min(now_mono - self.last_tick, 65.0))
        self.last_tick = now_mono
        local_now = now_utc.astimezone(ZoneInfo(policy["timezone"]))
        today = local_now.date().isoformat()

        # A backward wall-clock adjustment never rolls the stored day or quota back.
        if not self.day:
            self.day = today
        elif today > self.day:
            self.day = today
            self.used_seconds = 0
            self.state["quota_bonus_seconds"] = 0
            self.warning_marks.clear()
            self.over_quota_since = None

        weekday_slot = local_now.weekday()
        half_hour_slot = local_now.hour * 2 + (1 if local_now.minute >= 30 else 0)
        in_schedule = bool(policy["weekly_schedule"][weekday_slot][half_hour_slot])
        weekend = weekday_slot >= 5
        quota_seconds = (int(policy["weekend_quota_min" if weekend else "weekday_quota_min"]) * 60
                         + int(self.state.get("quota_bonus_seconds", 0)))

        if in_schedule and is_active and self.used_seconds < quota_seconds:
            self.used_seconds += elapsed
        remaining = max(0, quota_seconds - int(self.used_seconds))
        warning = None
        for mark in (600, 300, 60):
            if 0 < remaining <= mark and mark not in self.warning_marks:
                self.warning_marks.add(mark)
                warning = mark
                break

        grace_started = False
        if self.used_seconds >= quota_seconds:
            if self.over_quota_since is None:
                self.over_quota_since = now_mono
                grace_started = True
        else:
            self.over_quota_since = None
        grace_remaining = (max(0, 60 - int(now_mono - self.over_quota_since))
                           if self.over_quota_since is not None else None)
        result = {
            "day": self.day,
            "used_seconds": int(self.used_seconds),
            "quota_seconds": quota_seconds,
            "remaining_seconds": remaining,
            "in_schedule": in_schedule,
            "active": is_active,
            "warning_seconds": warning,
            "grace_started": grace_started,
            "grace_remaining": grace_remaining,
        }
        self.state.update({"day": self.day, "used_seconds": int(self.used_seconds),
                           "warning_marks": sorted(self.warning_marks)})
        return result


def user_is_active(idle_limit_seconds: int = 300) -> bool:
    """Return false when locked or idle for five minutes; assume active off Windows."""
    if os.name != "nt":
        return True

    # The interactive desktop switches from "Default" to "Winlogon" at lock.
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    open_desktop = user32.OpenInputDesktop
    open_desktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_desktop.restype = wintypes.HANDLE
    desktop = open_desktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
    if not desktop:
        return False
    try:
        name = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        get_info = user32.GetUserObjectInformationW
        get_info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        get_info.restype = wintypes.BOOL
        if not get_info(desktop, 2, name, ctypes.sizeof(name), ctypes.byref(needed)):
            return False
        if name.value != "Default":
            return False
    finally:
        user32.CloseDesktop.argtypes = [wintypes.HANDLE]
        user32.CloseDesktop.restype = wintypes.BOOL
        user32.CloseDesktop(desktop)

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    try:
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return True
        tick = ctypes.windll.kernel32.GetTickCount()
        idle_ms = (tick - info.dwTime) & 0xFFFFFFFF
        return idle_ms < idle_limit_seconds * 1000
    except (AttributeError, OSError):
        return True


def lock_workstation() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.user32.LockWorkStation())
    except (AttributeError, OSError):
        return False
