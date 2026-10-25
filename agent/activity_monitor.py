"""Visible foreground-app metadata collection with process-name block rules."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import threading
import time

import psutil

from policy_runtime import user_is_active


def foreground_process():
    """Return process handle and executable basename; never read window title or file contents."""
    if os.name != "nt":
        return None
    user32 = ctypes.windll.user32
    get_foreground = user32.GetForegroundWindow
    get_foreground.restype = wintypes.HWND
    hwnd = get_foreground()
    if not hwnd:
        return None
    pid = ctypes.c_ulong()
    get_process_id = user32.GetWindowThreadProcessId
    get_process_id.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    get_process_id.restype = wintypes.DWORD
    get_process_id(hwnd, ctypes.byref(pid))
    if pid.value < 10:
        return None
    try:
        process = psutil.Process(pid.value)
        name = os.path.basename(process.name()).lower()
        return process, name
    except (psutil.Error, OSError, PermissionError):
        return None


def run_activity_monitor(stop: threading.Event, policy_provider, event_callback,
                         terminate_blocked: bool = False, interval: float = 5.0) -> None:
    print("Foreground app names and duration are being collected; window titles and content are not read.")
    if terminate_blocked:
        print("App blocking is enabled. Rules match the foreground process name.")
    current_name: str | None = None
    current_started = time.monotonic()
    current_version = 0
    last_reported_match: tuple[str, int] | None = None

    while not stop.is_set() and policy_provider() is None:
        stop.wait(1.0)

    def stop_current():
        nonlocal current_name
        if current_name:
            elapsed = max(0, int(time.monotonic() - current_started))
            event_callback("app_stop", current_name, elapsed, current_version)
            current_name = None

    while not stop.is_set():
        found = foreground_process() if user_is_active() else None
        policy_record = policy_provider()
        payload = policy_record.get("payload", {}) if policy_record else {}
        version = int(policy_record.get("version", 0)) if policy_record else 0
        matched_rule = None
        if found:
            process, name = found
            process_name = os.path.splitext(name)[0]
            for rule in payload.get("blocked_apps", []):
                # Accept legacy signed policies containing {name, sha256, reason}
                # while new policies store just the normalized process name.
                rule_name = rule if isinstance(rule, str) else rule.get("name", "")
                rule_name = os.path.splitext(os.path.basename(rule_name.strip().lower()))[0]
                if process_name == rule_name:
                    matched_rule = rule
                    break

            if matched_rule and terminate_blocked:
                print(f"Ứng dụng {name} bị chặn theo luật.")
                try:
                    process.terminate()
                    process.wait(timeout=3)
                    event_callback("blocked_app", name, 0, version)
                    found = None
                except (psutil.Error, OSError):
                    print(f"Không thể dừng {name}; cần xem lại quyền tiến trình.")

        next_name = found[1] if found else None
        if matched_rule and not terminate_blocked:
            match_key = (next_name or "", version)
            if match_key != last_reported_match:
                print(f"App rule matched {next_name}; process termination is disabled.")
                event_callback("app_rule_match", next_name or name, 0, version)
            last_reported_match = match_key
        else:
            last_reported_match = None
        if next_name != current_name:
            stop_current()
            if next_name:
                current_name = next_name
                current_started = time.monotonic()
                current_version = version
                event_callback("app_start", current_name, 0, current_version)
        stop.wait(interval)

    stop_current()
