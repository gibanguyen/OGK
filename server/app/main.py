from contextlib import asynccontextmanager
import asyncio
import base64
import binascii
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import ntpath
import secrets
import threading
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from .db import Base, SessionLocal, engine
from .models import ChildProfile, ChildRequest, Device, Event, PairingCode, Parent, Policy
from .schemas import (ChildRequestCreate, ChildRequestReview, Credentials, EnrollRequest,
                      EventRequest, HeartbeatRequest, PairingCodeRequest, PolicyRequest)
from shared.domain_catalog import CATEGORY_COUNTS, DOMAIN_CATALOG

SESSION_SECRET = ""
SERVER_SECRETS_PATH = Path(os.getenv("LOCALAPPDATA") or Path.home()) / "OGK" / "server-secrets.json"
_SERVER_SECRET_LOCK = threading.Lock()
_RATE_LOCK = threading.Lock()
_RATE_ATTEMPTS: dict[tuple[str, str], list[float]] = {}
DEFAULT_POLICY = {
    "weekday_quota_min": 90,
    "weekend_quota_min": 90,
    "timezone": "Asia/Bangkok",
    "weekly_schedule": [[True] * 48 for _ in range(7)],
    "blocked_domains": [],
    "allow_domains": ["111.vn", "example.org", "example.edu"],
    "blocked_categories": ["social", "games", "age_inappropriate"],
    "blocked_apps": [],
}


def canonical_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ensure_server_secrets() -> dict[str, str]:
    """Create persistent server keys on first dashboard/auth use and populate process env."""
    global SESSION_SECRET
    session_from_env = os.getenv("OGK_SESSION_KEY")
    policy_from_env = os.getenv("OGK_POLICY_HMAC_KEY")
    if SESSION_SECRET and os.getenv("OGK_POLICY_HMAC_KEY"):
        return {"session_key": SESSION_SECRET, "policy_hmac_key": os.environ["OGK_POLICY_HMAC_KEY"]}
    with _SERVER_SECRET_LOCK:
        try:
            stored = json.loads(SERVER_SECRETS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stored = {}
        session_key = session_from_env or stored.get("OGK_SESSION_KEY") or secrets.token_hex(32)
        policy_key = policy_from_env or stored.get("OGK_POLICY_HMAC_KEY") or secrets.token_hex(32)
        SERVER_SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = SERVER_SECRETS_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps({"OGK_SESSION_KEY": session_key,
                                         "OGK_POLICY_HMAC_KEY": policy_key}, indent=2), encoding="utf-8")
        os.replace(temporary, SERVER_SECRETS_PATH)
        os.environ["OGK_SESSION_KEY"] = session_key
        os.environ["OGK_POLICY_HMAC_KEY"] = policy_key
        SESSION_SECRET = session_key
        return {"session_key": session_key, "policy_hmac_key": policy_key}


def policy_signature(version: int, payload: dict) -> str:
    secret = ensure_server_secrets()["policy_hmac_key"]
    signed_body = canonical_json({"version": version, "payload": payload})
    return hmac.new(secret.encode("utf-8"), signed_body.encode("utf-8"), hashlib.sha256).hexdigest()


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    # Add the device credential column to databases created by earlier prototype versions.
    with engine.begin() as connection:
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(devices)"))}
        if "token_hash" not in columns:
            connection.execute(text("ALTER TABLE devices ADD COLUMN token_hash VARCHAR(64)"))
    yield


app = FastAPI(title="OGK family support prototype", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent / "static"), name="static")


class LiveConnections:
    def __init__(self):
        self.parents: dict[int, list[WebSocket]] = {}
        self.devices: dict[str, WebSocket] = {}
        self.send_locks: dict[int, asyncio.Lock] = {}

    async def send(self, socket: WebSocket, payload: dict) -> None:
        lock = self.send_locks.setdefault(id(socket), asyncio.Lock())
        async with lock:
            await socket.send_json(payload)

    async def notify_parent(self, parent_id: int, payload: dict) -> None:
        for socket in list(self.parents.get(parent_id, [])):
            try:
                await self.send(socket, payload)
            except Exception:
                self.remove_parent(parent_id, socket)

    async def send_device(self, device_id: str, payload: dict) -> bool:
        socket = self.devices.get(device_id)
        if socket is None:
            return False
        try:
            await self.send(socket, payload)
            return True
        except Exception:
            self.remove_device(device_id, socket)
            return False

    def remove_parent(self, parent_id: int, socket: WebSocket) -> None:
        sockets = self.parents.get(parent_id, [])
        if socket in sockets:
            sockets.remove(socket)
        if not sockets:
            self.parents.pop(parent_id, None)
        self.send_locks.pop(id(socket), None)

    def remove_device(self, device_id: str, socket: WebSocket) -> None:
        if self.devices.get(device_id) is socket:
            self.devices.pop(device_id, None)
        self.send_locks.pop(id(socket), None)


LIVE = LiveConnections()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _session_token(parent_id: int) -> str:
    ensure_server_secrets()
    expires = int((datetime.now(timezone.utc) + timedelta(days=7)).timestamp())
    body = f"{parent_id}:{expires}".encode()
    signature = hmac.new(SESSION_SECRET.encode(), body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(body + b":" + signature).decode().rstrip("=")


def _session_parent_id(token: str | None) -> int | None:
    ensure_server_secrets()
    if not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        parent_text, expiry_text, signature = raw.split(b":", 2)
        body = parent_text + b":" + expiry_text
        expected = hmac.new(SESSION_SECRET.encode(), body, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected) or int(expiry_text) < int(datetime.now(timezone.utc).timestamp()):
            return None
        return int(parent_text)
    except (ValueError, TypeError, binascii.Error):
        return None


def current_parent(request: Request, db: Session = Depends(get_db)) -> Parent:
    parent_id = _session_parent_id(request.cookies.get("ogk_session"))
    parent = db.get(Parent, parent_id) if parent_id is not None else None
    if parent is None:
        raise HTTPException(status_code=401, detail="Vui lòng đăng nhập phụ huynh")
    return parent


def require_child_owner(child_id: str, parent: Parent, db: Session) -> None:
    profile = db.get(ChildProfile, child_id)
    if profile is None or profile.parent_id != parent.id:
        raise HTTPException(status_code=404, detail="Không tìm thấy hồ sơ trẻ thuộc tài khoản này")


@app.websocket("/ws/parent")
async def parent_live_socket(websocket: WebSocket):
    db = SessionLocal()
    parent_id = _session_parent_id(websocket.cookies.get("ogk_session"))
    parent = db.get(Parent, parent_id) if parent_id is not None else None
    db.close()
    if parent is None:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    LIVE.parents.setdefault(parent.id, []).append(websocket)
    await LIVE.send(websocket, {"type": "connected"})
    try:
        while True:
            message = await websocket.receive_json()
            action = message.get("type") if isinstance(message, dict) else None
            db = SessionLocal()
            try:
                if action == "review_child_request":
                    request_id = int(message.get("request_id", 0))
                    child_request = db.get(ChildRequest, request_id)
                    if child_request is None:
                        raise ValueError("Không tìm thấy yêu cầu")
                    require_child_owner(child_request.child_id, db.get(Parent, parent.id), db)
                    if child_request.status != "pending":
                        raise ValueError("Yêu cầu không còn ở trạng thái chờ")
                    response = message.get("response")
                    if response not in {"approved", "declined"}:
                        raise ValueError("Phản hồi không hợp lệ")
                    minutes = int(message.get("override_minutes", 15)) if response == "approved" and child_request.kind == "extra_time" else 0
                    if minutes < 0 or minutes > 120:
                        raise ValueError("Số phút phải từ 0 đến 120")
                    child_request.status = "reviewed"
                    child_request.response = response
                    child_request.override_minutes = minutes
                    child_request.reviewed_at = datetime.now(timezone.utc)
                    db.commit()
                    delivered = False
                    if response == "approved" and minutes:
                        delivered = await LIVE.send_device(child_request.device_id, {
                            "type": "grant_extra_time", "request_id": child_request.id,
                            "minutes": minutes,
                        })
                    result = {"type": "action_result", "action": action, "ok": True,
                              "request_id": child_request.id, "response": response,
                              "delivered": delivered}
                    await LIVE.notify_parent(parent.id, {"type": "request_reviewed",
                        "request_id": child_request.id, "response": response})
                    await LIVE.send(websocket, result)
                elif action == "lock_device":
                    device_id = str(message.get("device_id", ""))
                    device = db.get(Device, device_id)
                    if device is None:
                        raise ValueError("Không tìm thấy thiết bị đã ghép đôi")
                    require_child_owner(device.child_id, db.get(Parent, parent.id), db)
                    command_id = secrets.token_hex(12)
                    delivered = await LIVE.send_device(device_id, {
                        "type": "lock_now", "command_id": command_id,
                    })
                    await LIVE.send(websocket, {"type": "action_result", "action": action,
                        "ok": delivered, "device_id": device_id, "command_id": command_id,
                        "message": "Đã gửi lệnh khóa máy." if delivered else "Máy trẻ đang ngoại tuyến; chưa gửi được lệnh khóa."})
                else:
                    await LIVE.send(websocket, {"type": "action_result", "ok": False,
                                                "message": "Lệnh không được hỗ trợ."})
            except (ValueError, TypeError, HTTPException) as exc:
                detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
                await LIVE.send(websocket, {"type": "action_result", "action": action,
                                            "ok": False, "message": detail})
            finally:
                db.close()
    except WebSocketDisconnect:
        pass
    finally:
        LIVE.remove_parent(parent.id, websocket)


@app.websocket("/ws/device/{device_id}")
async def device_live_socket(websocket: WebSocket, device_id: str):
    await websocket.accept()
    try:
        auth = await asyncio.wait_for(websocket.receive_json(), timeout=8)
    except (asyncio.TimeoutError, WebSocketDisconnect):
        await websocket.close(code=1008)
        return
    token = auth.get("token") if isinstance(auth, dict) and auth.get("type") == "auth" else None
    db = SessionLocal()
    device = db.get(Device, device_id)
    try:
        verify_device(device, token)
        child_id = device.child_id
        profile = db.get(ChildProfile, child_id)
        parent_id = profile.parent_id if profile else None
    except HTTPException:
        db.close()
        await websocket.send_json({"type": "auth_error", "message": "Thiết bị chưa được ghép đôi"})
        await websocket.close(code=1008)
        return
    db.close()
    LIVE.devices[device_id] = websocket
    await LIVE.send(websocket, {"type": "auth_ok"})
    try:
        while True:
            message = await websocket.receive_json()
            action = message.get("type") if isinstance(message, dict) else None
            if action == "child_request":
                db = SessionLocal()
                try:
                    device = db.get(Device, device_id)
                    verify_device(device, token)
                    if message.get("child_id") != device.child_id:
                        raise ValueError("Hồ sơ không khớp với thiết bị")
                    kind = message.get("kind")
                    subject = "screen_time" if kind == "extra_time" else str(message.get("subject", "")).strip()
                    if kind not in {"extra_time", "false_block"} or not subject or len(subject) > 253:
                        raise ValueError("Nội dung yêu cầu không hợp lệ")
                    if kind == "false_block" and any(x in subject for x in ("://", "/", "?")):
                        raise ValueError("Chỉ nhập tên ứng dụng hoặc tên miền")
                    child_request = ChildRequest(device_id=device_id, child_id=device.child_id,
                                                 kind=kind, subject=subject)
                    db.add(child_request)
                    if kind == "extra_time":
                        db.add(Event(device_id=device_id, child_id=device.child_id,
                            type="unlock_request", subject="screen_time", duration_sec=0,
                            policy_id="parent-review"))
                    db.commit()
                    db.refresh(child_request)
                    response = {"id": child_request.id, "status": child_request.status,
                                "kind": child_request.kind, "subject": child_request.subject,
                                "created_at": child_request.created_at.isoformat()}
                    await LIVE.send(websocket, {"type": "action_result", "action": action,
                                                "ok": True, "request": response,
                                                "correlation_id": message.get("correlation_id")})
                    if parent_id is not None:
                        await LIVE.notify_parent(parent_id, {"type": "child_request", "request": response})
                except (ValueError, HTTPException) as exc:
                    detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
                    await LIVE.send(websocket, {"type": "action_result", "action": action,
                        "ok": False, "message": detail,
                        "correlation_id": message.get("correlation_id")})
                finally:
                    db.close()
            elif action == "command_result":
                if parent_id is not None:
                    await LIVE.notify_parent(parent_id, {**message,
                        "type": "device_command_result", "device_id": device_id})
    except WebSocketDisconnect:
        pass
    finally:
        LIVE.remove_device(device_id, websocket)


def require_child_access(child_id: str, request: Request, device_token: str | None, db: Session) -> None:
    profile = db.get(ChildProfile, child_id)
    parent_id = _session_parent_id(request.cookies.get("ogk_session"))
    if profile and parent_id == profile.parent_id:
        return
    if device_token:
        token_hash = hashlib.sha256(device_token.encode()).hexdigest()
        device = db.scalar(select(Device).where(Device.child_id == child_id, Device.token_hash == token_hash))
        if device is not None:
            return
    raise HTTPException(status_code=401, detail="Đăng nhập phụ huynh hoặc mở báo cáo từ OGK Tray trên thiết bị đã ghép đôi")


def verify_device(device: Device | None, token: str | None) -> None:
    if device is None or not device.token_hash or not token:
        raise HTTPException(status_code=401, detail="Thiết bị chưa được ghép đôi hoặc thiếu thông tin xác thực")
    actual = hashlib.sha256(token.encode()).hexdigest()
    if not hmac.compare_digest(device.token_hash, actual):
        raise HTTPException(status_code=403, detail="Thông tin xác thực thiết bị không hợp lệ")


def check_rate_limit(scope: str, request: Request, limit: int = 6, window_seconds: int = 60) -> None:
    client = request.client.host if request.client else "unknown"
    key = (scope, client)
    now = datetime.now(timezone.utc).timestamp()
    with _RATE_LOCK:
        attempts = [stamp for stamp in _RATE_ATTEMPTS.get(key, []) if now - stamp < window_seconds]
        if len(attempts) >= limit:
            _RATE_ATTEMPTS[key] = attempts
            raise HTTPException(status_code=429, detail="Thử quá nhiều lần; vui lòng đợi một phút")
        attempts.append(now)
        _RATE_ATTEMPTS[key] = attempts


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    ensure_server_secrets()
    parent_id = _session_parent_id(request.cookies.get("ogk_session"))
    if parent_id is None or db.get(Parent, parent_id) is None:
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse("""<!doctype html><html lang='vi'><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>OGK</title>
    <style>body{font:16px system-ui;max-width:1000px;margin:2rem auto;padding:0 1rem;color:#18324b}h1{color:#086f75}.card{padding:1rem;border:1px solid #cbd8df;border-radius:12px;margin:1rem 0}.schedule-hint{font-size:13px;color:#526777;margin:8px 0}.grid{overflow-x:auto;padding:8px 4px;border:1px solid #dce7e7;border-radius:10px;background:#fbfdfc}.time-ruler,.day{display:grid;grid-template-columns:7rem repeat(48,minmax(20px,1fr));gap:3px;min-width:1180px;align-items:center;margin:6px 0}.time-ruler{position:sticky;top:0;z-index:2;padding:5px 0;background:#fbfdfc;border-bottom:1px solid #cbd8df}.hour-mark{grid-column:span 2;border-left:2px solid #82999b;padding-left:4px;color:#395c60;font-size:12px;font-weight:700;height:18px;white-space:nowrap}.day-label{position:sticky;left:0;z-index:1;background:#fbfdfc;padding:4px 3px}.slot{height:30px;min-width:20px;border:1px solid #b8cccd;border-radius:4px;background:#e8eeee;padding:0;cursor:pointer;transition:background .12s,border-color .12s}.slot.hour-start{border-left:2px solid #82999b}.slot.half-hour{border-left:1px dashed #a9babb}.slot.on{background:#29a394;border-color:#17877d}.slot.hour-start.on{border-left:2px solid #126b65}.slot:focus{outline:2px solid #18324b;outline-offset:1px}.bar{display:inline-block;min-width:2px;height:12px;background:#29a394;border-radius:4px}button, input, textarea{font:inherit;padding:.45rem}small{color:#526777}</style><link rel='stylesheet' href='/static/ogk.css'>
    <h1>OGK · Cùng con sử dụng máy tính lành mạnh</h1><p>Prototype minh bạch. Bảng thời gian dùng độ phân giải 30 phút; ô xanh là thời gian được phép. Lưu policy sẽ gửi thay đổi tới agent qua heartbeat.</p><p>Đăng nhập: <b id='parent-email'></b> <button type='button' onclick='logout()'>Đăng xuất</button></p><p class='notice'>Khóa cấu hình server được tự sinh ở lần mở dashboard đầu tiên và lưu riêng trên máy server. Khi ghép đôi, khóa cần để xác minh policy được chuyển cho agent.</p>
    <section class='card'><h2>Ghép đôi máy của trẻ</h2><p>Tạo mã dùng một lần có hiệu lực 10 phút, sau đó nhập mã trong cửa sổ OGK ở máy trẻ. Điền cả mã hồ sơ (chỉ chữ, số, dấu gạch ngang/gạch dưới) và tên hiển thị.</p><label>Mã hồ sơ (vd: laptop-cua-be) <input id='pair-child' placeholder='be-an' pattern='[A-Za-z0-9_-]+'></label> <label>Tên hiển thị <input id='pair-name' placeholder='Bé An'></label> <button id='pair-button' type='button' onclick='createPairCode()'>Tạo mã ghép đôi</button><p id='pair-result' role='status' aria-live='polite'></p></section>
    <section class='card'><h2>Thiết bị</h2><div id='devices'>Đang tải…</div></section>
    <section class='card'><h2>Chính sách thời gian</h2><label>Hồ sơ trẻ <input id='child' list='child-list' placeholder='Chọn hoặc nhập mã hồ sơ'><datalist id='child-list'></datalist></label>
    <p><label>Quota ngày thường (phút) <input id='weekday' type='number' min='1' max='1440' value='90'></label> <label>Cuối tuần (phút) <input id='weekend' type='number' min='1' max='1440' value='90'></label></p>
    <p><button type='button' onclick='setAll(true)'>Cho phép cả tuần</button> <button type='button' onclick='setAll(false)'>Tắt cả tuần</button> <button type='button' onclick='setWorkdayPreset()'>Lịch mẫu 07:00–21:00 mỗi ngày</button></p>
    <p class='schedule-hint'>Mỗi ô là một mốc 30 phút. Các vạch đậm đánh dấu đầu giờ; rê chuột lên ô để xem chính xác giờ bắt đầu và trạng thái cho phép / khóa. Kéo ngang để xem hết trục thời gian.</p>
    <div class='grid' id='schedule' aria-label='Lịch tuần 7 ngày, mỗi ô tương ứng 30 phút'></div>
    <p><label>Domain chặn (mỗi dòng một domain)<br><textarea id='blocked-domains' rows='5' cols='60' placeholder='social-fixture-001.test'></textarea></label></p>
    <small>Proxy DNS chỉ nhận truy vấn UDP gửi tới 127.0.0.1:5353; agent không tự đổi DNS của Windows. Xem README để thử chặn và kiểm tra sự kiện.</small>
    <p><label>Domain luôn cho phép (mỗi dòng một domain)<br><textarea id='allow-domains' rows='3' cols='60'>111.vn&#10;example.org&#10;example.edu</textarea></label></p>
    <fieldset><legend>Nhóm domain bị chặn</legend><label><input type='checkbox' class='category' value='education'>Giáo dục</label> <label><input type='checkbox' class='category' value='entertainment'>Giải trí</label> <label><input type='checkbox' class='category' value='social' checked>Mạng xã hội</label> <label><input type='checkbox' class='category' value='games' checked>Trò chơi</label> <label><input type='checkbox' class='category' value='age_inappropriate' checked>Không phù hợp lứa tuổi (fixture trung tính)</label> <label><input type='checkbox' class='category' value='unclassified'>Chưa phân loại</label></fieldset>
    <p><label>Tên ứng dụng (mỗi dòng một tên; nhập tên tiến trình như chrome hoặc msedge, không cần .exe)<br><textarea id='blocked-apps' rows='4' cols='60' placeholder='chrome&#10;msedge'></textarea></label></p>
    <p><button type='button' onclick='loadPolicy()'>Tải policy</button> <button type='button' onclick='savePolicy()'>Lưu và phát hành policy</button> <span id='message' role='status'></span></p><small>Thay đổi được ký HMAC phía server. OGK_POLICY_HMAC_KEY phải được giữ giống nhau giữa server và agent. Chặn tiến trình cần bật OGK_ENABLE_PROCESS_BLOCKING=1 trên máy trẻ.</small></section>
    <section class='card'><h2>Báo cáo 7 ngày</h2><button type='button' onclick='loadReport()'>Tải báo cáo</button> <a id='child-link'>Mở trang minh bạch cho trẻ</a><div id='report'>Chưa tải báo cáo.</div></section>
    <section class='card'><h2>Yêu cầu của trẻ</h2><button type='button' onclick='loadRequests()'>Tải yêu cầu</button> <span id='live-status' role='status'>Đang kết nối realtime…</span><div id='requests'>Chưa tải yêu cầu.</div></section>
    <p><a href='/docs'>Tài liệu API</a></p>
    <script>
    const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const days=['Thứ 2','Thứ 3','Thứ 4','Thứ 5','Thứ 6','Thứ 7','Chủ nhật'];
    const grid=document.querySelector('#schedule');
    const scheduleColumns='7rem repeat(48,minmax(20px,1fr))';
    const ruler=document.createElement('div');ruler.className='time-ruler';ruler.style.gridTemplateColumns=scheduleColumns;
    const rulerLabel=document.createElement('strong');rulerLabel.className='day-label';rulerLabel.textContent='Mốc giờ';ruler.append(rulerLabel);
    for(let hour=0;hour<24;hour++){const mark=document.createElement('span');mark.className='hour-mark';mark.textContent=String(hour).padStart(2,'0')+':00';ruler.append(mark)}
    grid.append(ruler);
    days.forEach((d,di)=>{const row=document.createElement('div');row.className='day';row.style.gridTemplateColumns=scheduleColumns;const label=document.createElement('strong');label.className='day-label';label.textContent=d;row.append(label);for(let i=0;i<48;i++){const b=document.createElement('button');b.type='button';b.className='slot on '+(i%2===0?'hour-start':'half-hour');b.dataset.day=di;b.dataset.slot=i;b.title=d+' '+String(Math.floor(i/2)).padStart(2,'0')+':'+(i%2?'30':'00')+'–'+String(Math.floor((i+1)/2)).padStart(2,'0')+':'+(i%2===0?'30':'00')+' · được phép';b.setAttribute('aria-label',b.title);b.onclick=()=>{b.classList.toggle('on');const start=String(Math.floor(i/2)).padStart(2,'0')+':'+(i%2?'30':'00');b.title=d+' '+start+'–'+String(Math.floor((i+1)/2)).padStart(2,'0')+':'+(i%2===0?'30':'00')+' · '+(b.classList.contains('on')?'được phép':'bị khóa');b.setAttribute('aria-label',b.title)};row.append(b)}grid.append(row)});
    function setSlotState(b,on){const d=days[Number(b.dataset.day)],i=Number(b.dataset.slot),start=String(Math.floor(i/2)).padStart(2,'0')+':'+(i%2?'30':'00'),end=String(Math.floor((i+1)/2)).padStart(2,'0')+':'+(i%2===0?'30':'00');b.classList.toggle('on',on);b.title=d+' '+start+'–'+end+' · '+(on?'được phép':'bị khóa');b.setAttribute('aria-label',b.title)}
    function setAll(value){document.querySelectorAll('.slot').forEach(b=>setSlotState(b,value))}
    function setWorkdayPreset(){document.querySelectorAll('.slot').forEach(b=>{const i=Number(b.dataset.slot);setSlotState(b,i>=14&&i<42)})}
    async function api(path,options={}){const r=await fetch(path,options);let data;try{data=await r.json()}catch{data={}}if(r.status===401){location.href='/login';throw Error('Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.')}if(!r.ok){const detail=Array.isArray(data.detail)?data.detail.map(x=>{const field=x.loc?.slice(-1)[0];return (field?field+': ':'')+x.msg}).join(' · '):data.detail;throw Error(detail||'Yêu cầu thất bại (HTTP '+r.status+')')}return data}
    async function logout(){await fetch('/api/auth/logout',{method:'POST'});location.href='/login'}
    async function createPairCode(){const result=document.querySelector('#pair-result'),button=document.querySelector('#pair-button');const childId=document.querySelector('#pair-child').value.trim(),childName=document.querySelector('#pair-name').value.trim();if(!childId||!childName){result.textContent='Hãy nhập cả mã hồ sơ trẻ và tên hiển thị trước khi tạo mã.';return}if(!/^[A-Za-z0-9_-]+$/.test(childId)){result.textContent='Mã hồ sơ chỉ được gồm chữ cái, chữ số, dấu gạch ngang hoặc gạch dưới.';return}button.disabled=true;button.textContent='Đang tạo mã…';result.textContent='Đang gửi yêu cầu tới server…';try{const p=await api('/api/pairing-codes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({child_id:childId,child_name:childName})});document.querySelector('#child').value=p.child_id;result.textContent='Mã '+p.code+' · hồ sơ '+p.child_name+' · hết hạn sau 10 phút. Nhập mã trong OGK Tray ở máy trẻ.';await loadChildren()}catch(e){result.textContent='Không tạo được mã: '+e.message}finally{button.disabled=false;button.textContent='Tạo mã ghép đôi'}}
    async function loadChildren(){const xs=await api('/api/children');document.querySelector('#child-list').innerHTML=xs.map(x=>'<option value="'+esc(x.child_id)+'">'+esc(x.name)+'</option>').join('');if(!document.querySelector('#child').value&&xs.length)document.querySelector('#child').value=xs[0].child_id}
    const lines=id=>document.querySelector(id).value.split(/\\r?\\n/).map(x=>x.trim()).filter(Boolean);
    async function loadPolicy(){try{const p=await api('/api/policies/'+encodeURIComponent(document.querySelector('#child').value));const x=p.payload;document.querySelector('#weekday').value=x.weekday_quota_min;document.querySelector('#weekend').value=x.weekend_quota_min;x.weekly_schedule.forEach((day,d)=>day.forEach((on,i)=>{const b=document.querySelector('.slot[data-day="'+d+'"][data-slot="'+i+'"]');setSlotState(b,on)}));document.querySelector('#blocked-domains').value=(x.blocked_domains||[]).join('\\n');document.querySelector('#allow-domains').value=(x.allow_domains||[]).join('\\n');document.querySelector('#blocked-apps').value=(x.blocked_apps||[]).map(a=>{const n=typeof a==='string'?a:a.name||'';return n.toLowerCase().replace(/\\.exe$/,'')}).join('\\n');document.querySelectorAll('.category').forEach(c=>c.checked=(x.blocked_categories||[]).includes(c.value));document.querySelector('#message').textContent='Đã tải policy v'+p.version}catch(e){document.querySelector('#message').textContent=e.message}}
    async function savePolicy(){const schedule=days.map((_,d)=>Array.from({length:48},(_,i)=>document.querySelector('.slot[data-day="'+d+'"][data-slot="'+i+'"]').classList.contains('on')));const blockedDomains=lines('#blocked-domains'),allowDomains=lines('#allow-domains');const conflicts=blockedDomains.filter(d=>allowDomains.some(a=>{const b=d.toLowerCase(),allowed=a.toLowerCase();return b===allowed||b.endsWith('.'+allowed)}));if(conflicts.length){document.querySelector('#message').textContent='Domain đang nằm trong cả danh sách chặn và luôn cho phép: '+conflicts.join(', ')+'. Hãy xóa domain đó khỏi danh sách luôn cho phép rồi lưu lại.';return}const payload={weekday_quota_min:Number(document.querySelector('#weekday').value),weekend_quota_min:Number(document.querySelector('#weekend').value),timezone:'Asia/Bangkok',weekly_schedule:schedule,blocked_domains:blockedDomains,allow_domains:allowDomains,blocked_categories:Array.from(document.querySelectorAll('.category:checked')).map(c=>c.value),blocked_apps:lines('#blocked-apps')};try{const p=await api('/api/policies/'+encodeURIComponent(document.querySelector('#child').value),{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});document.querySelector('#message').textContent='Đã phát hành policy v'+p.version+'; agent nhận ở heartbeat tiếp theo.'}catch(e){document.querySelector('#message').textContent=e.message}}
    async function loadReport(){try{const id=document.querySelector('#child').value;document.querySelector('#child-link').href='/child/'+encodeURIComponent(id);const r=await fetch('/api/reports/'+encodeURIComponent(id));const d=await r.json();const fmt=n=>Math.round(n/60)+' phút',max=Math.max(1,...d.daily.map(x=>x.duration_sec));document.querySelector('#report').innerHTML='<p>Tổng thời gian ghi nhận: <b>'+fmt(d.total_duration_sec)+'</b> · Số lần bị chặn: <b>'+d.blocked_count+'</b></p><h3>Theo ngày</h3>'+d.daily.map(x=>'<p>'+esc(x.day)+' <span class="bar" style="width:'+Math.max(2,100*x.duration_sec/max)+'%"></span> '+fmt(x.duration_sec)+'</p>').join('')+'<h3>Ứng dụng thường dùng</h3>'+d.top_apps.map(x=>'<p>'+esc(x.subject)+': '+fmt(x.duration_sec)+'</p>').join('')+'<h3>Domain được tra cứu nhiều</h3>'+d.top_domains.map(x=>'<p>'+esc(x.subject)+': '+x.count+' lần</p>').join('')}catch(e){document.querySelector('#report').textContent=e.message}}
    async function loadRequests(){try{const id=document.querySelector('#child').value;const xs=await api('/api/child-requests/'+encodeURIComponent(id));document.querySelector('#requests').innerHTML=xs.length?xs.map(x=>'<p>#'+x.id+' · '+esc(x.kind==='extra_time'?'Xin thêm thời gian':'Báo chặn nhầm')+' · '+esc(x.kind==='extra_time'?'15 phút':x.subject)+' · '+esc(x.status)+' '+esc(x.response||'')+(x.response==='approved'&&x.override_minutes?' ('+x.override_minutes+' phút)':'')+(x.status==='pending'?' <button onclick="reviewRequest('+x.id+',\\'approved\\')">Duyệt</button> <button onclick="reviewRequest('+x.id+',\\'declined\\')">Từ chối</button>':'')+'</p>').join(''):'Chưa có yêu cầu.'}catch(e){document.querySelector('#requests').textContent=e.message}}
    window.loadRequests=loadRequests;
    async function reviewRequest(id,response){const socket=window.ogkParentSocket;if(!socket||socket.readyState!==WebSocket.OPEN){document.querySelector('#live-status').textContent='Mất kết nối realtime; hãy đợi kết nối lại rồi thử duyệt.';return}socket.send(JSON.stringify({type:'review_child_request',request_id:id,response:response,override_minutes:response==='approved'?15:0}))}
    api('/api/auth/me').then(x=>{document.querySelector('#parent-email').textContent=x.email;return loadChildren()}).then(()=>api('/api/devices')).then(xs=>{document.querySelector('#devices').innerHTML=xs.length?xs.map(x=>'<p><b>'+esc(x.device_id)+'</b> · '+esc(x.child_id)+' · '+esc(x.status)+' · policy v'+x.policy_version+' · lần cuối: '+esc(x.last_seen||'chưa có')+'</p>').join(''):'Chưa có thiết bị ghép đôi.'}).catch(e=>{document.querySelector('#devices').textContent='Không tải được danh sách thiết bị: '+e.message;if(e.message.includes('hết hạn'))location.href='/login'});
    </script><script>
    // Keep pairing independent from the dashboard's larger script so a separate
    // dashboard feature cannot silently disable this button.
    (()=>{
      const button=document.querySelector('#pair-button');
      if(!button)return;
      button.onclick=async()=>{
        const result=document.querySelector('#pair-result');
        const childId=document.querySelector('#pair-child').value.trim();
        const childName=document.querySelector('#pair-name').value.trim();
        if(!childId||!childName){result.textContent='Hãy nhập cả mã hồ sơ trẻ và tên hiển thị.';return;}
        if(!/^[A-Za-z0-9_-]+$/.test(childId)){result.textContent='Mã hồ sơ chỉ được gồm chữ cái, chữ số, dấu gạch ngang hoặc gạch dưới.';return;}
        button.disabled=true;
        button.textContent='Đang tạo mã…';
        result.textContent='Đang gửi yêu cầu tới server…';
        try{
          const response=await fetch('/api/pairing-codes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({child_id:childId,child_name:childName})});
          const data=await response.json().catch(()=>({}));
          if(!response.ok){
            const detail=Array.isArray(data.detail)?data.detail.map(x=>x.msg).join(' · '):data.detail;
            throw new Error(detail||`Server trả về lỗi HTTP ${response.status}`);
          }
          result.textContent=`Mã ghép đôi: ${data.code} · hồ sơ ${data.child_name} · hết hạn sau 10 phút. Nhập mã trong OGK Tray ở máy trẻ.`;
          const child=document.querySelector('#child');
          if(child)child.value=data.child_id;
          const list=document.querySelector('#child-list');
          if(list&&!Array.from(list.options).some(o=>o.value===data.child_id)){
            const option=document.createElement('option');option.value=data.child_id;option.textContent=data.child_name;list.append(option);
          }
        }catch(error){result.textContent='Không tạo được mã: '+error.message;}
        finally{button.disabled=false;button.textContent='Tạo mã ghép đôi';}
      };
      const deviceContainer=document.querySelector('#devices');
      const liveStatus=document.querySelector('#live-status');
      let parentSocket;
      const openParentSocket=()=>{
        const protocol=location.protocol==='https:'?'wss:':'ws:';
        parentSocket=new WebSocket(protocol+'//'+location.host+'/ws/parent');
        window.ogkParentSocket=parentSocket;
        parentSocket.onopen=()=>{if(liveStatus)liveStatus.textContent='Kết nối realtime đã sẵn sàng.';};
        parentSocket.onclose=()=>{if(liveStatus)liveStatus.textContent='Đang kết nối lại realtime…';window.setTimeout(openParentSocket,3000);};
        parentSocket.onerror=()=>{if(liveStatus)liveStatus.textContent='Kết nối realtime gặp lỗi.';};
        parentSocket.onmessage=event=>{
          let data;try{data=JSON.parse(event.data)}catch{return;}
          if(data.type==='child_request'){
            if(liveStatus)liveStatus.textContent='Có yêu cầu mới từ máy trẻ.';
            if(typeof window.loadRequests==='function')window.loadRequests();
          }else if(data.type==='request_reviewed'){
            if(typeof window.loadRequests==='function')window.loadRequests();
          }else if(data.type==='action_result'){
            if(liveStatus)liveStatus.textContent=data.message||(data.ok?'Đã cập nhật yêu cầu.':'Không thực hiện được thao tác.');
            if(data.action==='review_child_request'&&data.ok&&data.response==='approved'&&!data.delivered&&liveStatus)
              liveStatus.textContent='Đã duyệt; agent sẽ nhận thêm giờ ở lần heartbeat kế tiếp.';
            if(data.action==='review_child_request'&&typeof window.loadRequests==='function')window.loadRequests();
          }else if(data.type==='device_command_result'){
            if(liveStatus)liveStatus.textContent=data.message||(data.success?'Máy trẻ đã khóa.':'Không khóa được máy trẻ.');
          }
        };
      };
      openParentSocket();
      const refreshDevices=async()=>{
        try{
          const response=await fetch('/api/devices');
          const devices=await response.json().catch(()=>[]);
          if(!response.ok)throw new Error(Array.isArray(devices.detail)?devices.detail.map(x=>x.msg).join(' · '):(devices.detail||`HTTP ${response.status}`));
          deviceContainer.replaceChildren();
          if(!devices.length){deviceContainer.textContent='Chưa có thiết bị ghép đôi.';return;}
          for(const device of devices){
            const row=document.createElement('p');
            const id=document.createElement('b');id.textContent=device.device_id;
            row.append(id,document.createTextNode(` · ${device.child_id} · ${device.status} · policy v${device.policy_version} · lần cuối: ${device.last_seen||'chưa có'}`));
            const lock=document.createElement('button');
            lock.type='button';lock.textContent='Khóa máy ngay';lock.style.marginLeft='0.5rem';
            lock.addEventListener('click',()=>{
              if(!window.confirm(`Khóa máy ${device.device_id} ngay? Trẻ sẽ cần đăng nhập lại để tiếp tục sử dụng.`))return;
              if(!parentSocket||parentSocket.readyState!==WebSocket.OPEN){if(liveStatus)liveStatus.textContent='Mất kết nối realtime; chưa gửi được lệnh khóa.';return;}
              parentSocket.send(JSON.stringify({type:'lock_device',device_id:device.device_id}));
              if(liveStatus)liveStatus.textContent='Đang gửi lệnh khóa máy…';
            });
            row.append(lock);
            const unpair=document.createElement('button');
            unpair.type='button';unpair.textContent='Hủy ghép đôi';unpair.style.marginLeft='0.5rem';
            unpair.addEventListener('click',async()=>{
              if(!window.confirm(`Hủy ghép đôi thiết bị ${device.device_id}? Máy này sẽ cần mã mới để ghép lại.`))return;
              unpair.disabled=true;
              try{
                const response=await fetch('/api/devices/'+encodeURIComponent(device.device_id),{method:'DELETE'});
                const data=await response.json().catch(()=>({}));
                if(!response.ok)throw new Error(data.detail||`HTTP ${response.status}`);
                await refreshDevices();
              }catch(error){window.alert('Không hủy được ghép đôi: '+error.message);unpair.disabled=false;}
            });
            row.append(unpair);
            deviceContainer.append(row);
          }
        }catch(error){deviceContainer.textContent='Không tải được danh sách thiết bị: '+error.message;}
      };
      refreshDevices();
      window.setInterval(refreshDevices,5000);
    })();
    </script></html>""")


@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return HTMLResponse("""<!doctype html><html lang='vi'><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>Đăng nhập phụ huynh · OGK</title><link rel='stylesheet' href='/static/ogk.css'><main class='auth-wrap'><p class='eyebrow'>OGK · GIA ĐÌNH</p><h1>Không gian phụ huynh</h1><p>Đăng nhập để quản lý hồ sơ trẻ, ghép đôi thiết bị và xem báo cáo.</p><section class='card'><label>Email<br><input id='email' type='email' autocomplete='email' required></label><p><label>Mật khẩu<br><input id='password' type='password' autocomplete='current-password' minlength='10' required></label></p><button onclick='submitAuth("login")'>Đăng nhập</button> <button class='secondary' onclick='submitAuth("register")'>Tạo tài khoản đầu tiên</button><p id='message' role='status'></p></section></main><script>
async function submitAuth(action){const message=document.querySelector('#message');const email=document.querySelector('#email').value.trim();const password=document.querySelector('#password').value;if(email.length<5){message.textContent='Email cần có ít nhất 5 ký tự.';return}if(password.length<10){message.textContent='Mật khẩu cần có ít nhất 10 ký tự.';return}try{const r=await fetch('/api/auth/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,password})});const d=await r.json();if(!r.ok){const detail=Array.isArray(d.detail)?d.detail.map(x=>x.msg+(x.loc?' ('+x.loc.slice(-1)[0]+')':'')).join(' '):d.detail;throw Error(detail||'Không thực hiện được')}location.href='/'}catch(e){message.textContent=e.message}}
fetch('/api/auth/me').then(r=>{if(r.ok)location.href='/'})
</script></html>""")


def _password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()


def _password_matches(password: str, stored: str) -> bool:
    try:
        salt_text, digest_text = stored.split("$", 1)
        salt = base64.urlsafe_b64decode(salt_text)
        expected = base64.urlsafe_b64decode(digest_text)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, binascii.Error):
        return False


def _set_parent_cookie(response: Response, parent: Parent) -> None:
    response.set_cookie("ogk_session", _session_token(parent.id), httponly=True,
                        samesite="lax", secure=os.getenv("OGK_COOKIE_SECURE", "0") == "1",
                        max_age=7 * 24 * 60 * 60, path="/")


@app.post("/api/auth/register")
def register_parent(payload: Credentials, response: Response, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    if db.scalar(select(Parent.id).limit(1)) is not None:
        raise HTTPException(status_code=403, detail="Đã có tài khoản phụ huynh; đăng ký mới đã đóng")
    parent = Parent(email=email, password_hash=_password_hash(payload.password))
    db.add(parent)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email này đã được sử dụng")
    db.refresh(parent)
    _set_parent_cookie(response, parent)
    return {"id": parent.id, "email": parent.email}


@app.post("/api/auth/login")
def login_parent(payload: Credentials, request: Request, response: Response, db: Session = Depends(get_db)):
    check_rate_limit("login", request)
    parent = db.scalar(select(Parent).where(Parent.email == payload.email.strip().lower()))
    if parent is None or not _password_matches(payload.password, parent.password_hash):
        raise HTTPException(status_code=401, detail="Email hoặc mật khẩu không đúng")
    _set_parent_cookie(response, parent)
    return {"id": parent.id, "email": parent.email}


@app.get("/api/auth/me")
def who_am_i(parent: Parent = Depends(current_parent)):
    return {"id": parent.id, "email": parent.email}


@app.post("/api/auth/logout")
def logout_parent(response: Response):
    response.delete_cookie("ogk_session", path="/")
    return {"logged_out": True}


@app.post("/api/pairing-codes")
def create_pairing_code(payload: PairingCodeRequest, parent: Parent = Depends(current_parent),
                        db: Session = Depends(get_db)):
    child_id = payload.child_id.strip()
    profile = db.get(ChildProfile, child_id)
    if profile is not None and profile.parent_id != parent.id:
        raise HTTPException(status_code=409, detail="Mã hồ sơ trẻ đã được tài khoản khác sử dụng")
    if profile is None:
        profile = ChildProfile(child_id=child_id, parent_id=parent.id, name=payload.child_name.strip())
        db.add(profile)
    elif payload.child_name.strip():
        profile.name = payload.child_name.strip()
    now = datetime.now(timezone.utc)
    db.query(PairingCode).filter(PairingCode.parent_id == parent.id,
                                PairingCode.child_id == child_id,
                                PairingCode.used_at.is_(None)).update(
        {PairingCode.used_at: now})
    code = secrets.token_hex(4).upper()
    row = PairingCode(code_hash=hashlib.sha256(code.encode()).hexdigest(), child_id=child_id,
                      parent_id=parent.id, expires_at=now + timedelta(minutes=10))
    db.add(row)
    db.commit()
    return {"code": code, "child_id": child_id, "child_name": profile.name,
            "expires_at": row.expires_at.isoformat(), "expires_in_seconds": 600}


@app.get("/api/children")
def list_children(parent: Parent = Depends(current_parent), db: Session = Depends(get_db)):
    profiles = db.scalars(select(ChildProfile).where(ChildProfile.parent_id == parent.id).order_by(ChildProfile.name)).all()
    return [{"child_id": p.child_id, "name": p.name} for p in profiles]


@app.post("/api/enroll")
def enroll(payload: EnrollRequest, request: Request, db: Session = Depends(get_db)):
    check_rate_limit("enroll", request)
    code_hash = hashlib.sha256(payload.code.upper().encode()).hexdigest()
    pairing = db.scalar(select(PairingCode).where(PairingCode.code_hash == code_hash))
    now = datetime.now(timezone.utc)
    expiry = pairing.expires_at.replace(tzinfo=timezone.utc) if pairing and pairing.expires_at.tzinfo is None else (pairing.expires_at if pairing else now)
    if pairing is None or pairing.used_at is not None or expiry < now:
        raise HTTPException(status_code=403, detail="Mã ghép đôi không hợp lệ")
    child_id = pairing.child_id
    device = db.get(Device, payload.device_id)
    if device is None:
        device_token = secrets.token_urlsafe(32)
        device = Device(id=payload.device_id, child_id=child_id,
                        token_hash=hashlib.sha256(device_token.encode()).hexdigest())
        db.add(device)
    elif device.child_id != child_id:
        raise HTTPException(status_code=409, detail="Thiết bị đã được ghép với hồ sơ khác")
    else:
        raise HTTPException(status_code=409, detail="Thiết bị này đã ghép đôi; hãy tạo ID thiết bị mới nếu cần ghép lại")
    pairing.used_at = now
    db.commit()
    secrets_config = ensure_server_secrets()
    return {"device_id": device.id, "child_id": device.child_id, "device_token": device_token,
            "policy_hmac_key": secrets_config["policy_hmac_key"],
            "policy_version": device.policy_version}


@app.get("/api/device-config/{device_id}")
def device_config(device_id: str, x_ogk_device_token: str | None = Header(default=None),
                  db: Session = Depends(get_db)):
    device = db.get(Device, device_id)
    verify_device(device, x_ogk_device_token)
    return {"policy_hmac_key": ensure_server_secrets()["policy_hmac_key"]}


@app.post("/api/heartbeat")
def heartbeat(payload: HeartbeatRequest, x_ogk_device_token: str | None = Header(default=None), db: Session = Depends(get_db)):
    device = db.get(Device, payload.device_id)
    if device is None or device.child_id != payload.child_id:
        raise HTTPException(status_code=404, detail="Thiết bị chưa được ghép đôi")
    verify_device(device, x_ogk_device_token)
    policy = db.get(Policy, device.child_id)
    if policy is None:
        body = canonical_json(DEFAULT_POLICY)
        policy = Policy(child_id=device.child_id, version=1, payload_json=body,
                        signature=policy_signature(1, DEFAULT_POLICY))
        db.add(policy)
        db.flush()
    device.last_seen = datetime.now(timezone.utc)
    if payload.policy_version == policy.version:
        device.policy_version = policy.version
    db.commit()
    grants = db.scalars(select(ChildRequest).where(
        ChildRequest.device_id == device.id,
        ChildRequest.kind == "extra_time",
        ChildRequest.status == "reviewed",
        ChildRequest.response == "approved",
        ChildRequest.override_minutes > 0,
    ).order_by(ChildRequest.reviewed_at.desc()).limit(50)).all()
    response = {"server_time": datetime.now(timezone.utc).isoformat(),
                "policy_version": policy.version,
                "commands": [{"type": "grant_extra_time", "request_id": r.id,
                              "minutes": r.override_minutes} for r in grants]}
    if payload.policy_version != policy.version:
        response["policy"] = {"version": policy.version,
                              "payload": json.loads(policy.payload_json),
                              "signature": policy.signature}
    return response


@app.get("/api/policies/{child_id}")
def read_policy(child_id: str, parent: Parent = Depends(current_parent), db: Session = Depends(get_db)):
    require_child_owner(child_id, parent, db)
    policy = db.get(Policy, child_id)
    if policy is None:
        body = canonical_json(DEFAULT_POLICY)
        policy = Policy(child_id=child_id, version=1, payload_json=body,
                        signature=policy_signature(1, DEFAULT_POLICY))
        db.add(policy)
        db.commit()
    return {"child_id": child_id, "version": policy.version,
            "payload": json.loads(policy.payload_json), "signature": policy.signature,
            "updated_at": policy.updated_at.isoformat() if policy.updated_at else None}


@app.put("/api/policies/{child_id}")
def update_policy(child_id: str, payload: PolicyRequest, parent: Parent = Depends(current_parent),
                  db: Session = Depends(get_db)):
    require_child_owner(child_id, parent, db)
    body = canonical_json(payload.model_dump())
    policy = db.get(Policy, child_id)
    if policy is None:
        version = 1
        signature = policy_signature(version, payload.model_dump())
        policy = Policy(child_id=child_id, version=version, payload_json=body, signature=signature)
        db.add(policy)
    else:
        version = policy.version + 1
        signature = policy_signature(version, payload.model_dump())
        policy.version = version
        policy.payload_json = body
        policy.signature = signature
        policy.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"child_id": child_id, "version": policy.version,
            "payload": json.loads(policy.payload_json), "signature": policy.signature}


@app.post("/api/events", status_code=202)
def ingest_event(payload: EventRequest, x_ogk_device_token: str | None = Header(default=None),
                 db: Session = Depends(get_db)):
    device = db.get(Device, payload.device_id)
    if device is None or device.child_id != payload.child_id:
        raise HTTPException(status_code=404, detail="Thiết bị chưa được ghép đôi")
    verify_device(device, x_ogk_device_token)
    values = payload.model_dump()
    if payload.type in {"domain_query", "blocked_domain"}:
        subject = payload.subject.strip().lower().rstrip(".")
        if "://" in subject or "/" in subject or "?" in subject or "#" in subject or " " in subject:
            raise HTTPException(status_code=422, detail="Domain event cannot contain a URL or path")
        labels = subject.split(".")
        if len(labels) < 2:
            raise HTTPException(status_code=422, detail="Domain event requires a domain name")
        # Minimal eTLD+1 approximation; full public-suffix parsing is a future improvement.
        values["subject"] = ".".join(labels[-2:])
    else:
        values["subject"] = ntpath.basename(payload.subject.strip())[:253]
    db.add(Event(**values))
    db.commit()
    return {"accepted": True}


@app.get("/api/devices")
def list_devices(parent: Parent = Depends(current_parent), db: Session = Depends(get_db)):
    child_ids = select(ChildProfile.child_id).where(ChildProfile.parent_id == parent.id)
    rows = db.scalars(select(Device).where(Device.child_id.in_(child_ids)).order_by(Device.id)).all()
    return [{"device_id": d.id, "child_id": d.child_id, "status": d.status,
             "policy_version": d.policy_version,
             "last_seen": d.last_seen.isoformat() if d.last_seen else None} for d in rows]


@app.delete("/api/devices/{device_id}")
async def unpair_device(device_id: str, parent: Parent = Depends(current_parent),
                        db: Session = Depends(get_db)):
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy thiết bị đã ghép đôi")
    require_child_owner(device.child_id, parent, db)
    await LIVE.send_device(device_id, {"type": "pairing_revoked"})
    db.delete(device)
    db.commit()
    await LIVE.notify_parent(parent.id, {"type": "device_unpaired", "device_id": device_id})
    return {"unpaired": True, "device_id": device_id}


@app.get("/api/events")
def list_events(limit: int = 100, parent: Parent = Depends(current_parent), db: Session = Depends(get_db)):
    child_ids = select(ChildProfile.child_id).where(ChildProfile.parent_id == parent.id)
    rows = db.scalars(select(Event).where(Event.child_id.in_(child_ids)).order_by(Event.ts.desc()).limit(max(1, min(limit, 500)))).all()
    return [{"ts": e.ts.isoformat(), "device_id": e.device_id, "child_id": e.child_id,
             "type": e.type, "subject": e.subject, "duration_sec": e.duration_sec,
             "policy_id": e.policy_id} for e in rows]


@app.get("/api/device/events")
def list_device_blocked_events(device_id: str, x_ogk_device_token: str | None = Header(default=None),
                               limit: int = Query(default=10, ge=1, le=50),
                               db: Session = Depends(get_db)):
    device = db.get(Device, device_id)
    verify_device(device, x_ogk_device_token)
    rows = db.scalars(select(Event).where(
        Event.device_id == device_id,
        Event.type.in_(["blocked_app", "app_rule_match", "blocked_domain"]),
    ).order_by(Event.ts.desc()).limit(limit)).all()
    return [{"ts": event.ts.isoformat(), "type": event.type, "subject": event.subject}
            for event in rows]


@app.get("/api/domain-catalog")
def domain_catalog():
    return {"counts": {category: count for category, (count, _) in CATEGORY_COUNTS.items()},
            "total": len(DOMAIN_CATALOG),
            "domains": [{"domain": domain, "category": category}
                        for domain, category in sorted(DOMAIN_CATALOG.items())]}


@app.get("/api/reports/{child_id}")
def child_report(child_id: str, request: Request,
                 x_ogk_device_token: str | None = Header(default=None),
                 days: int = Query(default=7, ge=1, le=90), db: Session = Depends(get_db)):
    require_child_access(child_id, request, x_ogk_device_token, db)
    policy = db.get(Policy, child_id)
    policy_payload = json.loads(policy.payload_json) if policy else DEFAULT_POLICY
    tz = ZoneInfo(policy_payload.get("timezone", "Asia/Bangkok"))
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    events = db.scalars(select(Event).where(Event.child_id == child_id, Event.ts >= start).order_by(Event.ts)).all()

    daily_seconds: dict[str, int] = {}
    app_seconds: dict[str, int] = {}
    domain_counts: dict[str, int] = {}
    blocked_count = 0
    for event in events:
        timestamp = event.ts.replace(tzinfo=timezone.utc) if event.ts.tzinfo is None else event.ts
        local_day = timestamp.astimezone(tz).date().isoformat()
        if event.type == "app_stop":
            daily_seconds[local_day] = daily_seconds.get(local_day, 0) + event.duration_sec
            app_seconds[event.subject] = app_seconds.get(event.subject, 0) + event.duration_sec
        elif event.type == "domain_query":
            domain_counts[event.subject] = domain_counts.get(event.subject, 0) + 1
        elif event.type in {"blocked_app", "blocked_domain"}:
            blocked_count += 1

    local_today = now.astimezone(tz).date()
    daily = []
    for offset in range(days - 1, -1, -1):
        day = (local_today - timedelta(days=offset)).isoformat()
        daily.append({"day": day, "duration_sec": daily_seconds.get(day, 0)})
    return {
        "child_id": child_id,
        "days": days,
        "total_duration_sec": sum(daily_seconds.values()),
        "blocked_count": blocked_count,
        "daily": daily,
        "top_apps": [{"subject": name, "duration_sec": seconds}
                     for name, seconds in sorted(app_seconds.items(), key=lambda x: x[1], reverse=True)[:10]],
        "top_domains": [{"subject": name, "count": count}
                        for name, count in sorted(domain_counts.items(), key=lambda x: x[1], reverse=True)[:10]],
    }


@app.get("/child/{child_id}", response_class=HTMLResponse)
def child_page(child_id: str):
    # Escape the path value before embedding it in HTML; no free-form child text is collected.
    safe_id = (child_id.replace("&", "&amp;").replace("<", "&lt;")
               .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))
    return HTMLResponse(f"""<!doctype html><html lang='vi'><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>Hôm nay em đã dùng gì?</title>
    <style>body{{font:16px system-ui;max-width:760px;margin:2rem auto;padding:0 1rem;color:#18324b}}h1{{color:#086f75}}section{{padding:1rem;border:1px solid #cbd8df;border-radius:12px;margin:1rem 0}}.bar{{display:inline-block;min-width:2px;height:12px;background:#29a394;border-radius:4px}}button{{font:inherit;padding:.6rem;margin:.3rem}}</style><link rel='stylesheet' href='/static/ogk.css'>
    <main data-child="{safe_id}"><h1>Hôm nay em đã dùng gì?</h1><p>Con và phụ huynh xem cùng một báo cáo. OGK chỉ ghi tên ứng dụng/tên miền và thời lượng, không lưu nội dung trang hay tin nhắn.</p>
    <section><h2>Báo cáo 7 ngày</h2><div id='report'>Đang tải…</div></section>
    <section><h2>Con muốn gửi yêu cầu</h2><p>Để bảo vệ kết nối của thiết bị, hãy mở biểu tượng OGK ở khay hệ thống trên máy này và chọn “Xin thêm 15 phút” hoặc “Báo chặn nhầm”.</p></section>
    <section><h2>Trạng thái yêu cầu và phản hồi</h2><div id='requests'>Đang tải…</div></section></main>
    <script>const child=document.querySelector('main').dataset.child;const esc=s=>String(s).replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
    const deviceToken=decodeURIComponent(location.hash.slice(1));const authHeaders=deviceToken?{{'X-OGK-Device-Token':deviceToken}}:{{}};
    async function refresh(){{try{{const r=await fetch('/api/reports/'+encodeURIComponent(child),{{headers:authHeaders}});if(!r.ok)throw Error('Hãy mở báo cáo từ OGK Tray hoặc đăng nhập phụ huynh.');const d=await r.json();const fmt=n=>Math.round(n/60)+' phút',max=Math.max(1,...d.daily.map(x=>x.duration_sec));document.querySelector('#report').innerHTML='<p>Tổng thời gian: <b>'+fmt(d.total_duration_sec)+'</b> · Số lần bị chặn: '+d.blocked_count+'</p>'+d.daily.map(x=>'<p>'+esc(x.day)+' <span class="bar" style="width:'+Math.max(2,100*x.duration_sec/max)+'%"></span> '+fmt(x.duration_sec)+'</p>').join('')+'<h3>Ứng dụng</h3>'+d.top_apps.map(x=>'<p>'+esc(x.subject)+': '+fmt(x.duration_sec)+'</p>').join('')+'<h3>Tên miền</h3>'+d.top_domains.map(x=>'<p>'+esc(x.subject)+': '+x.count+' lần</p>').join('');const q=await fetch('/api/child-requests/'+encodeURIComponent(child),{{headers:authHeaders}});const xs=await q.json();document.querySelector('#requests').innerHTML=xs.length?xs.map(x=>'<p>'+esc(x.kind==='extra_time'?'Xin thêm giờ':'Báo chặn nhầm')+' · '+esc(x.subject)+' · '+esc(x.status==='pending'?'Đang chờ phụ huynh phản hồi':x.response==='approved'?'Phụ huynh đã đồng ý':'Phụ huynh đã từ chối')+(x.override_minutes?' · ghi nhận '+x.override_minutes+' phút trong demo':'')+'</p>').join(''):'Con chưa gửi yêu cầu nào.'}}catch(e){{document.querySelector('#report').textContent=e.message}}}}
    refresh();</script></html>""")


@app.post("/api/child-requests", status_code=201)
async def create_child_request(payload: ChildRequestCreate,
                               x_ogk_device_token: str | None = Header(default=None),
                               db: Session = Depends(get_db)):
    device = db.get(Device, payload.device_id)
    if device is None or device.child_id != payload.child_id:
        raise HTTPException(status_code=404, detail="Thiết bị chưa được ghép với hồ sơ này")
    verify_device(device, x_ogk_device_token)
    subject = "screen_time" if payload.kind == "extra_time" else payload.subject.strip()
    if payload.kind == "false_block" and ("://" in subject or "/" in subject or "?" in subject):
        raise HTTPException(status_code=422, detail="Chỉ nhập tên ứng dụng hoặc tên miền, không nhập URL")
    request = ChildRequest(device_id=payload.device_id, child_id=payload.child_id,
                           kind=payload.kind, subject=subject)
    db.add(request)
    if payload.kind == "extra_time":
        db.add(Event(device_id=payload.device_id, child_id=payload.child_id,
                     type="unlock_request", subject="screen_time", duration_sec=0,
                     policy_id="parent-review"))
    db.commit()
    db.refresh(request)
    result = {"id": request.id, "status": request.status, "kind": request.kind,
              "subject": request.subject, "created_at": request.created_at.isoformat()}
    profile = db.get(ChildProfile, request.child_id)
    if profile:
        await LIVE.notify_parent(profile.parent_id, {"type": "child_request", "request": result})
    return result


@app.get("/api/child-requests/{child_id}")
def list_child_requests(child_id: str, request: Request,
                        x_ogk_device_token: str | None = Header(default=None), db: Session = Depends(get_db)):
    require_child_access(child_id, request, x_ogk_device_token, db)
    rows = db.scalars(select(ChildRequest).where(ChildRequest.child_id == child_id)
                      .order_by(ChildRequest.created_at.desc()).limit(100)).all()
    return [{"id": r.id, "kind": r.kind, "subject": r.subject, "status": r.status,
             "response": r.response, "override_minutes": r.override_minutes,
             "created_at": r.created_at.isoformat(),
             "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None} for r in rows]


@app.patch("/api/child-requests/{request_id}")
async def review_child_request(request_id: int, payload: ChildRequestReview,
                               parent: Parent = Depends(current_parent), db: Session = Depends(get_db)):
    request = db.get(ChildRequest, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu")
    require_child_owner(request.child_id, parent, db)
    if request.status != "pending":
        raise HTTPException(status_code=409, detail="Yêu cầu đã được phản hồi")
    request.status = "reviewed"
    request.response = payload.response
    request.override_minutes = payload.override_minutes if payload.response == "approved" and request.kind == "extra_time" else 0
    request.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    result = {"id": request.id, "status": request.status, "response": request.response,
              "override_minutes": request.override_minutes, "reviewed_at": request.reviewed_at.isoformat()}
    delivered = False
    if request.kind == "extra_time" and payload.response == "approved" and request.override_minutes:
        delivered = await LIVE.send_device(request.device_id, {
            "type": "grant_extra_time", "request_id": request.id,
            "minutes": request.override_minutes,
        })
    await LIVE.notify_parent(parent.id, {"type": "request_reviewed", "request_id": request.id,
                                        "response": request.response})
    result["delivered"] = delivered
    return result
