"""Visible user-mode OGK agent: signed policy sync and screen-time prototype."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import threading
import time
import json
import uuid
from queue import Empty, Queue
from urllib.parse import quote

# Allow the agent to import the shared reserved-domain fixtures when launched
# directly as `python agent/main.py` from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from websockets.sync.client import connect as websocket_connect

from activity_monitor import run_activity_monitor
from dns_proxy import serve_dns
from policy_runtime import UsageTracker, load_state, lock_workstation, save_state, user_is_active, verify_policy
from tray_ui import TrayUI

SERVER = os.getenv("OGK_SERVER_URL", "http://127.0.0.1:8000").rstrip("/")
AGENT_CONFIG = Path(os.getenv("LOCALAPPDATA") or Path.home()) / "OGK" / "agent.json"
DEVICE_ID = ""
CHILD_ID = ""
DEVICE_TOKEN = ""
POLICY_KEY = os.getenv("OGK_POLICY_HMAC_KEY", "")
LOCK_ENABLED = os.getenv("OGK_ENABLE_WINDOWS_LOCK", "0") == "1"
ACTIVITY_ENABLED = os.getenv("OGK_ENABLE_ACTIVITY_COLLECTION", "1") == "1"
APP_BLOCK_ENABLED = os.getenv("OGK_ENABLE_PROCESS_BLOCKING", "0") == "1"
DNS_ENABLED = os.getenv("OGK_ENABLE_DNS_PROXY", "1") == "1"
DNS_UPSTREAM = os.getenv("OGK_DNS_UPSTREAM") or None
DNS_PORT = int(os.getenv("OGK_DNS_PORT", "5353"))
HEARTBEAT_SECONDS = 60


def send_event(event_type: str, subject: str, duration: int, version: int, when: datetime) -> None:
    if not DEVICE_TOKEN:
        return
    try:
        response = requests.post(f"{SERVER}/api/events", json={
            "ts": when.isoformat(), "device_id": DEVICE_ID, "child_id": CHILD_ID,
            "type": event_type, "subject": subject, "duration_sec": duration,
            "policy_id": f"policy-v{version}",
        }, headers={"X-OGK-Device-Token": DEVICE_TOKEN}, timeout=3)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"Event not delivered; no content was collected ({exc})")


def main() -> None:
    global DEVICE_ID, CHILD_ID, DEVICE_TOKEN, SERVER, POLICY_KEY
    print("OGK agent runs visibly in the Windows notification area. Choose Exit OGK to stop.")

    AGENT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    try:
        config = json.loads(AGENT_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        config = {}
    SERVER = os.getenv("OGK_SERVER_URL") or config.get("server_url") or SERVER
    DEVICE_ID = os.getenv("OGK_DEVICE_ID") or config.get("device_id") or f"pc-{uuid.uuid4().hex[:12]}"
    CHILD_ID = config.get("child_id", "")
    DEVICE_TOKEN = config.get("device_token", "")
    POLICY_KEY = os.getenv("OGK_POLICY_HMAC_KEY") or config.get("policy_hmac_key", "")
    config["device_id"] = DEVICE_ID

    def persist_config() -> None:
        temp_path = AGENT_CONFIG.with_suffix(".tmp")
        temp_path.write_text(json.dumps(config), encoding="utf-8")
        os.replace(temp_path, AGENT_CONFIG)

    persist_config()

    ws_outgoing: Queue[dict] = Queue()
    ws_incoming: Queue[dict] = Queue()
    ws_connected = threading.Event()
    ws_pending: dict[str, Queue[dict]] = {}
    ws_pending_lock = threading.Lock()

    def pair_device(server_url: str, code: str, device_id: str) -> dict:
        global CHILD_ID, DEVICE_TOKEN, POLICY_KEY
        if len(code) != 8:
            raise ValueError("Mã ghép đôi phải có đúng 8 ký tự.")
        try:
            response = requests.post(f"{server_url.rstrip('/')}/api/enroll",
                                     json={"code": code, "device_id": device_id}, timeout=8)
            data = response.json()
            if not response.ok:
                raise ValueError(data.get("detail", "Không ghép đôi được"))
        except requests.RequestException as exc:
            raise ValueError(f"Không kết nối được server: {exc}") from exc
        CHILD_ID = data["child_id"]
        DEVICE_TOKEN = data["device_token"]
        POLICY_KEY = data["policy_hmac_key"]
        config.update({"server_url": server_url.rstrip("/"), "child_id": CHILD_ID,
                       "device_token": DEVICE_TOKEN, "policy_hmac_key": POLICY_KEY})
        persist_config()
        return data

    if DEVICE_TOKEN and CHILD_ID and not POLICY_KEY:
        try:
            response = requests.get(f"{SERVER}/api/device-config/{DEVICE_ID}",
                                    headers={"X-OGK-Device-Token": DEVICE_TOKEN}, timeout=5)
            response.raise_for_status()
            POLICY_KEY = response.json()["policy_hmac_key"]
            config["policy_hmac_key"] = POLICY_KEY
            persist_config()
        except (requests.RequestException, KeyError, ValueError) as exc:
            print(f"Could not fetch device policy key yet; will retry after server is reachable ({exc})")

    def send_child_request(kind: str, subject: str) -> None:
        if not DEVICE_TOKEN or not CHILD_ID:
            raise RuntimeError("Hãy ghép đôi với phụ huynh trước.")
        if ws_connected.is_set():
            correlation_id = uuid.uuid4().hex
            reply: Queue[dict] = Queue(maxsize=1)
            with ws_pending_lock:
                ws_pending[correlation_id] = reply
            ws_outgoing.put({"type": "child_request", "device_id": DEVICE_ID,
                             "child_id": CHILD_ID, "kind": kind, "subject": subject,
                             "correlation_id": correlation_id})
            try:
                result = reply.get(timeout=5)
            except Empty as exc:
                raise RuntimeError("Chưa nhận được phản hồi realtime từ server; hãy thử lại.") from exc
            finally:
                with ws_pending_lock:
                    ws_pending.pop(correlation_id, None)
            if not result.get("ok"):
                raise RuntimeError(result.get("message", "Không gửi được yêu cầu"))
            return
        response = requests.post(f"{SERVER}/api/child-requests", json={
            "device_id": DEVICE_ID, "child_id": CHILD_ID, "kind": kind, "subject": subject,
        }, headers={"X-OGK-Device-Token": DEVICE_TOKEN}, timeout=5)
        data = response.json()
        if not response.ok:
            raise RuntimeError(data.get("detail", "Không gửi được yêu cầu"))

    def load_blocked_events() -> list[dict]:
        if not DEVICE_TOKEN or not CHILD_ID:
            return []
        response = requests.get(f"{SERVER}/api/device/events",
                                params={"device_id": DEVICE_ID},
                                headers={"X-OGK-Device-Token": DEVICE_TOKEN}, timeout=4)
        data = response.json()
        if not response.ok:
            raise RuntimeError(data.get("detail", "Không tải được lịch sử bị chặn"))
        return data

    def run_device_websocket(stop: threading.Event) -> None:
        while not stop.is_set():
            if not DEVICE_TOKEN or not CHILD_ID:
                ws_connected.clear()
                stop.wait(0.5)
                continue
            active_token = DEVICE_TOKEN
            socket_url = SERVER.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
            socket_url += "/ws/device/" + quote(DEVICE_ID, safe="")
            try:
                with websocket_connect(socket_url, open_timeout=6, close_timeout=2,
                                       ping_interval=20, ping_timeout=20) as ws:
                    ws.send(json.dumps({"type": "auth", "token": active_token}))
                    auth = json.loads(ws.recv(timeout=6))
                    if auth.get("type") != "auth_ok":
                        raise RuntimeError(auth.get("message", "WebSocket device authentication failed"))
                    ws_connected.set()
                    while not stop.is_set() and DEVICE_TOKEN == active_token:
                        try:
                            outgoing = ws_outgoing.get(timeout=0.1)
                            ws.send(json.dumps(outgoing, ensure_ascii=False))
                        except Empty:
                            pass
                        try:
                            message = json.loads(ws.recv(timeout=0.1))
                        except TimeoutError:
                            continue
                        correlation_id = message.get("correlation_id")
                        if message.get("type") == "action_result" and correlation_id:
                            with ws_pending_lock:
                                reply = ws_pending.get(correlation_id)
                            if reply is not None:
                                reply.put(message)
                        else:
                            ws_incoming.put(message)
            except Exception as exc:
                if not stop.is_set() and DEVICE_TOKEN == active_token:
                    print(f"Realtime connection unavailable; reconnecting ({exc})")
            finally:
                ws_connected.clear()
            stop.wait(2)

    agent_stop = threading.Event()
    threading.Thread(target=run_device_websocket, args=(agent_stop,), daemon=True,
                     name="ogk-device-websocket").start()
    tray = TrayUI(SERVER, DEVICE_ID, pair_device, send_child_request,
                  lambda: DEVICE_TOKEN, agent_stop.set, load_blocked_events, CHILD_ID)
    tray.start()

    state = load_state()
    policy = state.get("policy")
    if policy and (not POLICY_KEY or not verify_policy(policy, POLICY_KEY)):
        print("Cached policy signature is invalid; ignoring the cache.")
        policy = None
        state["policy"] = None
    tray.set_policy_summary(policy)
    tracker = UsageTracker(state)
    stop_background = threading.Event()

    def event_callback(event_type: str, subject: str, duration: int, version: int) -> None:
        when = datetime.now(timezone.utc)
        if event_type in {"blocked_domain", "blocked_app", "app_rule_match"}:
            tray.record_blocked_event(event_type, subject, when.isoformat())
        send_event(event_type, subject, duration, version, when)

    if ACTIVITY_ENABLED:
        threading.Thread(target=run_activity_monitor,
                         args=(stop_background, lambda: state.get("policy"), event_callback),
                         kwargs={"terminate_blocked": APP_BLOCK_ENABLED}, daemon=True,
                         name="ogk-activity").start()
    else:
        print("App activity collection is off. Set OGK_ENABLE_ACTIVITY_COLLECTION=1 for the demo.")

    if DNS_ENABLED:
        threading.Thread(target=serve_dns,
                         args=(lambda: state.get("policy"),
                               lambda kind, subject, version: event_callback(kind, subject, 0, version),
                               DNS_UPSTREAM, DNS_PORT),
                         daemon=True, name="ogk-dns-proxy").start()
    else:
        print("DNS proxy is off. Set OGK_ENABLE_DNS_PROXY=1 to start loopback test mode.")

    next_heartbeat = 0.0
    server_anchor: datetime | None = None
    anchor_monotonic = 0.0
    announced_locks: set[str] = set()
    locked_once: set[str] = set()

    while not agent_stop.is_set():
        now_mono = time.monotonic()
        if not DEVICE_TOKEN or not CHILD_ID:
            tray.set_status("Đang chờ ghép đôi với phụ huynh")
            time.sleep(1)
            continue
        while True:
            try:
                command = ws_incoming.get_nowait()
            except Empty:
                break
            command_type = command.get("type")
            if command_type == "pairing_revoked":
                print("Parent cancelled this device pairing.")
                DEVICE_TOKEN = ""
                CHILD_ID = ""
                POLICY_KEY = ""
                config.pop("device_token", None)
                config.pop("child_id", None)
                config.pop("policy_hmac_key", None)
                persist_config()
                policy = None
                state["policy"] = None
                state["quota_bonus_seconds"] = 0
                save_state(state)
                tray.set_policy_summary(None)
                server_anchor = None
                next_heartbeat = 0.0
                tray.clear_pairing()
            elif command_type == "grant_extra_time":
                command_id = str(command.get("request_id", ""))
                applied = set(state.get("applied_commands", []))
                if command_id and command_id not in applied:
                    minutes = max(1, min(120, int(command.get("minutes", 15))))
                    state["quota_bonus_seconds"] = int(state.get("quota_bonus_seconds", 0)) + minutes * 60
                    applied.add(command_id)
                    state["applied_commands"] = sorted(applied)[-200:]
                    save_state(state)
                    send_event("override_granted", "screen_time", minutes * 60,
                               int(policy["version"]) if policy else 0, datetime.now(timezone.utc))
                    tray.set_status(f"Phụ huynh đã duyệt thêm {minutes} phút")
            elif command_type == "lock_now":
                success = lock_workstation()
                if success:
                    send_event("locked", "screen_time", 0,
                               int(policy["version"]) if policy else 0, datetime.now(timezone.utc))
                ws_outgoing.put({"type": "command_result", "command_id": command.get("command_id"),
                                 "success": success,
                                 "message": "Máy trẻ đã khóa." if success else "Windows không thực hiện được lệnh khóa."})
        if not DEVICE_TOKEN or not CHILD_ID:
            tray.set_status("Đang chờ ghép đôi với phụ huynh")
            time.sleep(1)
            continue
        if now_mono >= next_heartbeat:
            try:
                active_version = int(policy["version"]) if policy else 0
                response = requests.post(f"{SERVER}/api/heartbeat", json={
                    "device_id": DEVICE_ID, "child_id": CHILD_ID,
                    "policy_version": active_version,
                }, headers={"X-OGK-Device-Token": DEVICE_TOKEN}, timeout=5)
                response.raise_for_status()
                data = response.json()
                server_anchor = datetime.fromisoformat(data["server_time"].replace("Z", "+00:00")).astimezone(timezone.utc)
                anchor_monotonic = time.monotonic()
                if not POLICY_KEY:
                    key_response = requests.get(f"{SERVER}/api/device-config/{DEVICE_ID}",
                                                 headers={"X-OGK-Device-Token": DEVICE_TOKEN}, timeout=5)
                    key_response.raise_for_status()
                    POLICY_KEY = key_response.json()["policy_hmac_key"]
                    config["policy_hmac_key"] = POLICY_KEY
                    persist_config()

                applied = set(state.get("applied_commands", []))
                for command in data.get("commands", []):
                    command_id = str(command.get("request_id", ""))
                    if command.get("type") == "grant_extra_time" and command_id and command_id not in applied:
                        minutes = max(1, min(120, int(command.get("minutes", 0))))
                        state["quota_bonus_seconds"] = int(state.get("quota_bonus_seconds", 0)) + minutes * 60
                        applied.add(command_id)
                        send_event("override_granted", "screen_time", minutes * 60,
                                   int(policy["version"]) if policy else 0, server_anchor)
                        print(f"Phụ huynh đã duyệt thêm {minutes} phút.")
                state["applied_commands"] = sorted(applied)[-200:]
                save_state(state)

                incoming = data.get("policy")
                if incoming:
                    active_version = int(policy["version"]) if policy else 0
                    if int(incoming["version"]) < active_version:
                        print("Server offered an older policy version; keeping the current policy.")
                    elif not verify_policy(incoming, POLICY_KEY):
                        print("Server policy signature did not match; keeping the prior policy.")
                    else:
                        policy = incoming
                        active_version = int(policy["version"])
                        state["policy"] = policy
                        save_state(state)
                        tracker = UsageTracker(state)
                        announced_locks.clear()
                        tray.set_policy_summary(policy)
                        print(f"Accepted signed policy v{policy['version']}.")
                print(f"Heartbeat OK · policy v{active_version} · next check in {HEARTBEAT_SECONDS}s")
                tray.set_status(f"Đang hoạt động · {CHILD_ID} · policy v{active_version}")
                next_heartbeat = time.monotonic() + HEARTBEAT_SECONDS
            except requests.RequestException as exc:
                print(f"Server unavailable; cached policy remains active ({exc})")
                response = getattr(exc, "response", None)
                if response is not None and response.status_code in {401, 403, 404}:
                    print("Pairing was revoked by the parent; this device must be paired again.")
                    DEVICE_TOKEN = ""
                    CHILD_ID = ""
                    POLICY_KEY = ""
                    config.pop("device_token", None)
                    config.pop("child_id", None)
                    config.pop("policy_hmac_key", None)
                    persist_config()
                    policy = None
                    state["policy"] = None
                    state["quota_bonus_seconds"] = 0
                    save_state(state)
                    tray.set_policy_summary(None)
                    server_anchor = None
                    tray.clear_pairing()
                tray.set_status("Mất kết nối server · đang giữ policy đã đồng bộ")
                next_heartbeat = time.monotonic() + 10

        if policy:
            if server_anchor is not None:
                now_utc = server_anchor + timedelta(seconds=max(0, time.monotonic() - anchor_monotonic))
            else:
                now_utc = datetime.now(timezone.utc)
            status = tracker.tick(policy["payload"], now_utc, user_is_active())
            countdown_running = (status["active"] and status["in_schedule"]
                                 and status["remaining_seconds"] > 0)
            tray.set_remaining_seconds(status["remaining_seconds"], running=countdown_running)
            if not status["in_schedule"]:
                tray.set_status("Ngoài khung giờ cho phép · đồng hồ tạm dừng")
            elif not status["active"]:
                tray.set_status("Máy đang không hoạt động · tạm dừng tính thời gian")
            elif status["remaining_seconds"] > 0:
                tray.set_status("Đã ghép đôi")
            else:
                tray.set_status("Đã hết thời gian hôm nay · có thể gửi yêu cầu từ khay OGK")
            if status["warning_seconds"] is not None:
                mins = status["warning_seconds"] // 60
                print(f"Nhắc thời gian: còn {mins} phút sử dụng theo chính sách gia đình.")
                send_event("quota_warning", "screen_time", status["used_seconds"],
                           int(policy["version"]), now_utc)
            if status["grace_started"]:
                print("Đã hết hạn mức; con có 60 giây để lưu công việc trước khi khóa.")

            reason = None
            if not status["in_schedule"]:
                reason = "schedule"
                message = "Ngoài khung giờ được phép theo lịch gia đình."
            elif status["grace_remaining"] == 0:
                reason = "quota"
                message = "Đã hết thời lượng hôm nay theo hạn mức gia đình."
            else:
                message = ""

            if reason and reason not in announced_locks:
                action = "Khóa máy sẽ được yêu cầu." if LOCK_ENABLED else "Chế độ khóa Windows đang tắt; chỉ hiển thị nhắc nhở."
                print(f"{message} {action}")
                announced_locks.add(reason)
            if reason and LOCK_ENABLED and reason not in locked_once:
                if lock_workstation():
                    send_event("locked", "screen_time", status["used_seconds"],
                               int(policy["version"]), now_utc)
                    locked_once.add(reason)
                else:
                    print("Không thể gọi Windows LockWorkStation trong phiên hiện tại.")

            if not reason:
                announced_locks.clear()
                locked_once.clear()
            state["policy"] = policy
            save_state(state)

        agent_stop.wait(1)
    stop_background.set()
    tray.stop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nOGK agent prototype stopped by user.")
