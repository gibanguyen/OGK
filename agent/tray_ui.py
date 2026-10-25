"""Visible Windows notification-area UI for pairing and child requests."""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk
import time
from typing import Callable
import webbrowser
from queue import Empty, Queue

import pystray
from PIL import Image, ImageDraw


class TrayUI:
    def __init__(self, server_url: str, device_id: str,
                 on_pair: Callable[[str, str, str], dict],
                 on_request: Callable[[str, str], None], token_provider: Callable[[], str],
                 on_exit: Callable[[], None],
                 on_events: Callable[[], list[dict]], child_id: str = ""):
        self.server_url = server_url
        self.device_id = device_id
        self.on_pair = on_pair
        self.on_request = on_request
        self.token_provider = token_provider
        self.on_exit = on_exit
        self.on_events = on_events
        self.status_text = "Chưa ghép đôi"
        self.child_id = child_id
        self.root: tk.Tk | None = None
        self.status_var: tk.StringVar | None = None
        self.pair_frame: ttk.Frame | None = None
        self.policy_frame: ttk.Frame | None = None
        self.request_frame: ttk.Frame | None = None
        self.timer_frame: ttk.Frame | None = None
        self.remaining_var: tk.StringVar | None = None
        self.remaining_seconds: int | None = None
        self.remaining_running = False
        self.remaining_updated_at = time.monotonic()
        self.code_var: tk.StringVar | None = None
        self.message_var: tk.StringVar | None = None
        self.pair_message_var: tk.StringVar | None = None
        self.icon: pystray.Icon | None = None
        self.blocked_list: tk.Listbox | None = None
        self.policy_list: tk.Listbox | None = None
        self.policy_status_var: tk.StringVar | None = None
        self.events_queue: Queue[tuple[list[dict] | None, str | None]] = Queue()
        self.events_thread: threading.Thread | None = None
        self.ui_updates: Queue[tuple[str, object]] = Queue()
        self.local_blocked_events: list[dict] = []

    def start(self) -> None:
        threading.Thread(target=self._run_window, daemon=True, name="ogk-tray-window").start()
        threading.Thread(target=self._run_icon, daemon=True, name="ogk-tray-icon").start()

    def _make_icon(self) -> Image.Image:
        image = Image.new("RGBA", (64, 64), (14, 116, 112, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((7, 7, 57, 57), radius=14, fill=(23, 59, 58, 255))
        draw.text((14, 19), "OGK", fill="white")
        return image

    def _run_icon(self) -> None:
        menu = pystray.Menu(
            pystray.MenuItem("Mở OGK", lambda: self.show(), default=True),
            pystray.MenuItem("Xin thêm 15 phút", lambda: self.request("extra_time", "screen_time")),
            pystray.MenuItem("Thoát OGK", lambda: self.stop()),
        )
        self.icon = pystray.Icon("ogk-agent", self._make_icon(), "OGK · Máy của trẻ", menu)
        self.icon.run()

    def _run_window(self) -> None:
        root = tk.Tk()
        self.root = root
        root.title("OGK · Trợ lý trên máy của trẻ")
        root.geometry("500x900")
        root.minsize(460, 760)
        root.protocol("WM_DELETE_WINDOW", root.withdraw)
        root.configure(background="#f5f8f4")
        style = ttk.Style(root)
        style.configure("Title.TLabel", font=("Segoe UI", 17, "bold"), foreground="#173b3a")
        style.configure("Hint.TLabel", foreground="#526c69", wraplength=340)
        style.configure("Clock.TLabel", font=("Segoe UI", 40, "bold"), foreground="#087f74")
        frame = ttk.Frame(root, padding=22)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="OGK · Máy của trẻ", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Ứng dụng hiển thị rõ khi đang chạy và chỉ ghi nhận metadata theo cấu hình gia đình.",
                  style="Hint.TLabel").pack(anchor="w", pady=(6, 14))
        self.status_var = tk.StringVar(value=self.status_text)
        ttk.Label(frame, textvariable=self.status_var, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 12))

        timer = ttk.LabelFrame(frame, text="Thời gian còn lại hôm nay", padding=12)
        self.timer_frame = timer
        self.remaining_var = tk.StringVar(value="--:--:--")
        ttk.Label(timer, textvariable=self.remaining_var, style="Clock.TLabel").pack(anchor="center")
        ttk.Label(timer, text="GIỜ : PHÚT : GIÂY", style="Hint.TLabel").pack(anchor="center")

        policy = ttk.LabelFrame(frame, text="Danh sách domain / ứng dụng theo policy", padding=10)
        self.policy_frame = policy
        self.policy_status_var = tk.StringVar(value="Đang chờ nhận policy từ server.")
        ttk.Label(policy, textvariable=self.policy_status_var, style="Hint.TLabel").pack(anchor="w", pady=(0, 5))
        policy_rows = ttk.Frame(policy)
        policy_rows.pack(fill="both", expand=True)
        self.policy_list = tk.Listbox(policy_rows, height=6, width=52, activestyle="none", exportselection=False)
        policy_scroll = ttk.Scrollbar(policy_rows, orient="vertical", command=self.policy_list.yview)
        self.policy_list.configure(yscrollcommand=policy_scroll.set)
        self.policy_list.pack(side="left", fill="both", expand=True)
        policy_scroll.pack(side="right", fill="y")
        ttk.Label(policy, text="Danh sách này là rule đã đồng bộ. DNS demo mặc định nghe tại 127.0.0.1:5353 nên trình duyệt không tự gửi truy vấn tới proxy. Rule ứng dụng chỉ đóng tiến trình khi OGK_ENABLE_PROCESS_BLOCKING=1; nhật ký bên dưới ghi nhận các lần khớp/chặn thực tế.",
                  style="Hint.TLabel", wraplength=420).pack(anchor="w", pady=(5, 0))

        pair = ttk.LabelFrame(frame, text="Ghép đôi với phụ huynh", padding=12)
        self.pair_frame = pair
        pair.pack(fill="x")
        ttk.Label(pair, text="Nhập mã 8 ký tự do phụ huynh tạo (hết hạn sau 10 phút).",
                  style="Hint.TLabel").pack(anchor="w", pady=(0, 8))
        self.code_var = tk.StringVar()
        ttk.Entry(pair, textvariable=self.code_var, width=22, font=("Consolas", 15)).pack(anchor="w")
        ttk.Button(pair, text="Ghép đôi thiết bị", command=self._pair).pack(anchor="w", pady=(10, 4))
        self.pair_message_var = tk.StringVar()
        ttk.Label(pair, textvariable=self.pair_message_var, style="Hint.TLabel").pack(anchor="w")

        request = ttk.LabelFrame(frame, text="Yêu cầu / phản hồi", padding=12)
        self.request_frame = request
        self.message_var = tk.StringVar(value="Sau khi ghép đôi, con có thể gửi yêu cầu tại đây.")
        ttk.Label(request, textvariable=self.message_var, style="Hint.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Button(request, text="Xin thêm 15 phút", command=lambda: self.request("extra_time", "screen_time")).pack(anchor="w")
        ttk.Label(request, text="Sự kiện app/domain bị chặn gần đây:").pack(anchor="w", pady=(10, 3))
        self.blocked_list = tk.Listbox(request, height=4, width=52, activestyle="none", exportselection=False)
        self.blocked_list.pack(fill="x")
        self.blocked_list.insert("end", "Chưa ghi nhận mục nào bị chặn.")
        ttk.Label(request, text="App hoặc domain bị chặn nhầm:").pack(anchor="w", pady=(10, 3))
        self.subject = tk.StringVar()
        ttk.Entry(request, textvariable=self.subject, width=38).pack(fill="x")
        ttk.Button(request, text="Báo chặn nhầm", command=self._false_block).pack(anchor="w", pady=(8, 0))
        ttk.Button(request, text="Mở báo cáo hoạt động của con", command=self.open_report).pack(anchor="w", pady=(8, 0))

        ttk.Label(frame, text=f"Mã thiết bị: {self.device_id}", style="Hint.TLabel").pack(anchor="w", pady=(12, 0))
        root.after(100, self._refresh_visibility)
        root.after(50, self._drain_ui_updates)
        root.after(250, self._tick_remaining_clock)
        root.after(500, self._poll_blocked_events)
        root.mainloop()

    def _drain_ui_updates(self) -> None:
        """Apply updates from agent workers only on Tk's owning thread."""
        if not self.root:
            return
        try:
            while True:
                kind, value = self.ui_updates.get_nowait()
                if kind == "status" and self.status_var:
                    self.status_text = str(value)
                    self.status_var.set(self.status_text)
                elif kind == "remaining":
                    self.remaining_seconds = value["seconds"]
                    self.remaining_running = value["running"]
                    self.remaining_updated_at = time.monotonic()
                    self._render_remaining_clock()
                elif kind == "message" and self.message_var:
                    self.message_var.set(str(value))
                elif kind == "policy":
                    self._render_policy(value)
                elif kind == "blocked":
                    event = value
                    key = (event["type"], event["subject"], event["ts"][:19])
                    if not any((item["type"], item["subject"], item["ts"][:19]) == key
                               for item in self.local_blocked_events):
                        self.local_blocked_events.insert(0, event)
                        self.local_blocked_events = self.local_blocked_events[:10]
                elif kind == "pairing_cleared":
                    self.child_id = ""
                    self.set_remaining_seconds(None)
                    self.status_text = "Ch\u01b0a gh\u00e9p \u0111\u00f4i"
                    if self.status_var:
                        self.status_var.set(self.status_text)
                    if self.message_var:
                        self.message_var.set("Gh\u00e9p \u0111\u00f4i v\u1edbi ph\u1ee5 huynh \u0111\u1ec3 xem th\u1eddi gian v\u00e0 g\u1eedi y\u00eau c\u1ea7u.")
                    self._refresh_visibility()
                elif kind == "show":
                    self.root.deiconify()
                    self.root.lift()
                elif kind == "destroy":
                    self.root.destroy()
                    return
        except Empty:
            pass
        self.root.after(50, self._drain_ui_updates)

    def _refresh_visibility(self) -> None:
        if not self.root:
            return
        if self.child_id:
            self.pair_frame.pack_forget()
            self.timer_frame.pack(fill="x", pady=(0, 8))
            self.policy_frame.pack(fill="both", expand=True, pady=(0, 8))
            self.request_frame.pack(fill="x", pady=(12, 0))
        else:
            self.timer_frame.pack_forget()
            self.policy_frame.pack_forget()
            self.pair_frame.pack(fill="x")
            self.request_frame.pack_forget()

    def set_remaining_seconds(self, seconds: int | None, running: bool = False) -> None:
        self.ui_updates.put(("remaining", {
            "seconds": max(0, int(seconds)) if seconds is not None else None,
            "running": bool(running and seconds is not None),
        }))

    def _render_remaining_clock(self) -> None:
        if not self.remaining_var:
            return
        if self.remaining_seconds is None:
            self.remaining_var.set("--:--:--")
            return
        elapsed = int(time.monotonic() - self.remaining_updated_at) if self.remaining_running else 0
        seconds = max(0, self.remaining_seconds - elapsed)
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        self.remaining_var.set(f"{hours:02d}:{minutes:02d}:{secs:02d}")

    def _tick_remaining_clock(self) -> None:
        if not self.root:
            return
        self._render_remaining_clock()
        self.root.after(250, self._tick_remaining_clock)

    def clear_pairing(self) -> None:
        self.ui_updates.put(("pairing_cleared", None))

    def set_policy_summary(self, policy_record: dict | None) -> None:
        """Show configured rules separately from domains actually blocked by DNS."""
        if not policy_record:
            self.ui_updates.put(("policy", None))
            return
        payload = policy_record.get("payload", {})
        domains = payload.get("blocked_domains", [])
        categories = payload.get("blocked_categories", [])
        apps = payload.get("blocked_apps", [])
        normalized_apps = []
        for app in apps:
            name = app if isinstance(app, str) else app.get("name", "") if isinstance(app, dict) else ""
            name = str(name).strip().lower()
            if name.endswith(".exe"):
                name = name[:-4]
            if name and name not in normalized_apps:
                normalized_apps.append(name)
        self.ui_updates.put(("policy", {
            "version": policy_record.get("version", "?"),
            "domains": [str(domain) for domain in domains if isinstance(domain, str)],
            "categories": [str(category) for category in categories if isinstance(category, str)],
            "apps": normalized_apps,
        }))

    def _render_policy(self, summary: object) -> None:
        if not self.policy_list or not self.policy_status_var:
            return
        self.policy_list.delete(0, "end")
        if not summary:
            self.policy_status_var.set("Chưa nhận policy đã xác thực từ server.")
            self.policy_list.insert("end", "Đang chờ ghép đôi hoặc đồng bộ policy.")
            return
        version = summary["version"]
        domains = summary["domains"]
        categories = summary["categories"]
        apps = summary.get("apps", [])
        category_labels = {
            "education": "Giáo dục", "entertainment": "Giải trí", "social": "Mạng xã hội",
            "games": "Trò chơi", "age_inappropriate": "Không phù hợp lứa tuổi",
            "unclassified": "Chưa phân loại",
        }
        self.policy_status_var.set(
            f"Policy v{version} · {len(domains)} domain · {len(categories)} nhóm · {len(apps)} ứng dụng"
        )
        if not domains and not categories and not apps:
            self.policy_list.insert("end", "Policy hiện không có domain, nhóm hoặc ứng dụng bị chặn.")
        for domain in domains:
            self.policy_list.insert("end", f"Domain · {domain}")
        for category in categories:
            self.policy_list.insert("end", f"Nhóm · {category_labels.get(category, category)}")
        for app in apps:
            self.policy_list.insert("end", f"Ứng dụng · {app}.exe")

    def record_blocked_event(self, event_type: str, subject: str, timestamp: str) -> None:
        """Queue an immediate local entry even if the server is unreachable."""
        self.ui_updates.put(("blocked", {"type": event_type, "subject": subject, "ts": timestamp}))

    def _poll_blocked_events(self) -> None:
        if not self.root:
            return
        if self.child_id:
            try:
                events, error = self.events_queue.get_nowait()
            except Empty:
                events, error = None, None
            if events is not None:
                if self.blocked_list:
                    self.blocked_list.delete(0, "end")
                    combined = self.local_blocked_events + events
                    unique = []
                    seen = set()
                    for event in sorted(combined, key=lambda item: item.get("ts", ""), reverse=True):
                        key = (event.get("type"), event.get("subject"), event.get("ts", "")[:19])
                        if key not in seen:
                            seen.add(key)
                            unique.append(event)
                    if not unique:
                        self.blocked_list.insert("end", "Chưa ghi nhận mục nào bị chặn.")
                    for event in unique[:10]:
                        label = self._event_label(event.get("type"))
                        timestamp = str(event.get("ts", ""))[:16].replace("T", " ")
                        self.blocked_list.insert("end", f"{timestamp} · {label}: {event.get('subject', '')}")
            elif error and self.blocked_list and not self.local_blocked_events:
                self.blocked_list.delete(0, "end")
                self.blocked_list.insert("end", f"Chưa tải được lịch sử: {error[:100]}")
            elif error and self.blocked_list and self.local_blocked_events:
                self.blocked_list.delete(0, "end")
                for event in self.local_blocked_events:
                    label = self._event_label(event.get("type"))
                    timestamp = str(event.get("ts", ""))[:16].replace("T", " ")
                    self.blocked_list.insert("end", f"{timestamp} · {label}: {event.get('subject', '')}")
            if self.events_thread is None or not self.events_thread.is_alive():
                self.events_thread = threading.Thread(target=self._load_blocked_events,
                                                      daemon=True, name="ogk-blocked-events")
                self.events_thread.start()
        self.root.after(5000, self._poll_blocked_events)

    @staticmethod
    def _event_label(event_type: str) -> str:
        return {
            "blocked_app": "Ứng dụng bị dừng",
            "app_rule_match": "Ứng dụng khớp rule (chưa bị dừng)",
            "blocked_domain": "Domain bị chặn qua DNS proxy",
        }.get(event_type, "Sự kiện chặn")

    def _load_blocked_events(self) -> None:
        try:
            self.events_queue.put((self.on_events(), None))
        except Exception as exc:
            self.events_queue.put((None, str(exc)))

    def _pair(self) -> None:
        try:
            code = (self.code_var.get() if self.code_var else "").strip().upper()
            result = self.on_pair(self.server_url, code, self.device_id)
            self.child_id = result["child_id"]
            self.code_var.set("")
            self.status_text = f"Đã ghép đôi · hồ sơ {self.child_id}"
            self.status_var.set(self.status_text)
            self.message_var.set("Đã kết nối với hồ sơ gia đình. Yêu cầu sẽ hiển thị ở dashboard phụ huynh.")
            self._refresh_visibility()
        except Exception as exc:
            self.pair_message_var.set(str(exc))

    def set_status(self, text: str) -> None:
        self.ui_updates.put(("status", text))

    def request(self, kind: str, subject: str) -> None:
        if not self.child_id:
            self.show()
            return
        self.ui_updates.put(("message", "Đang gửi yêu cầu qua kết nối realtime…"))

        def send_request() -> None:
            try:
                self.on_request(kind, subject)
                message = ("Đã gửi yêu cầu xin thêm 15 phút." if kind == "extra_time"
                           else "Đã gửi báo cáo chặn nhầm.")
                message += " Phụ huynh có thể phản hồi trên dashboard."
            except Exception as exc:
                message = str(exc)
            self.ui_updates.put(("message", message))

        threading.Thread(target=send_request, daemon=True, name="ogk-child-request").start()

    def open_report(self) -> None:
        if not self.child_id:
            return
        # The one-device token stays in the URL fragment, which browsers never send to the server.
        webbrowser.open(f"{self.server_url}/child/{self.child_id}#{self.token_provider()}")

    def _false_block(self) -> None:
        subject = self.subject.get().strip()
        if not subject or "://" in subject or "/" in subject:
            self.message_var.set("Nhập tên ứng dụng hoặc domain, không nhập URL đầy đủ.")
            return
        self.request("false_block", subject)

    def show(self) -> None:
        self.ui_updates.put(("show", None))

    def stop(self) -> None:
        self.on_exit()
        if self.icon:
            self.icon.stop()
        self.ui_updates.put(("destroy", None))
