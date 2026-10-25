# Tuần 2 — quota, lịch và đồng bộ policy

> Ghi chú tuần 2 là ảnh chụp giai đoạn phát triển ban đầu. Hướng dẫn chạy và xác thực hiện tại nằm trong [README](../README.md); phiên bản mới dùng đăng nhập phụ huynh thay cho `OGK_ADMIN_KEY`.

## Mục tiêu

Tuần này hoàn thiện đường đi cấu hình thời gian từ dashboard đến agent: phụ huynh đặt hạn mức ngày thường/cuối tuần và lịch 7×48; server tăng version, ký HMAC; agent nhận ở heartbeat, xác minh và cập nhật bộ đếm cục bộ. Agent cảnh báo ở mốc 10, 5, 1 phút và cho 60 giây sau khi hết quota.

## Những phần đã thêm

1. `server/app/models.py`: bảng `Policy` lưu hồ sơ trẻ, version, JSON policy, chữ ký và thời điểm cập nhật.
2. `server/app/schemas.py`: kiểm tra quota 1–1440 phút, timezone IANA hợp lệ và đúng kích thước 7×48 Boolean.
3. `server/app/main.py`: `GET/PUT /api/policies/{child_id}` có admin key; heartbeat gửi policy khi version agent lệch. HMAC-SHA256 ký cả version lẫn payload để tránh sửa version mà chữ ký vẫn hợp lệ.
4. Dashboard `/`: nhập mã hồ sơ và admin key, chỉnh quota, bật/tắt từng ô 30 phút, tải policy và phát hành policy mới.
5. `agent/policy_runtime.py`: xác minh HMAC, ghi cache vào `%LOCALAPPDATA%\OGK\state.json`, tính thời lượng theo monotonic clock, nhận biết desktop bị khóa và idle 5 phút trên Windows.
6. `agent/main.py`: heartbeat 60 giây, chạy enforcement loop mỗi giây, hiển thị cảnh báo và gửi sự kiện quota/locked. Khóa Windows chỉ gọi khi biến môi trường bật.

## Cách chạy

Trong PowerShell số 1, tại `D:\Uni\CD1`:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r server\requirements.txt
$env:OGK_ADMIN_KEY = (python -c "import secrets; print(secrets.token_urlsafe(32))")
$env:OGK_POLICY_HMAC_KEY = (python -c "import secrets; print(secrets.token_hex(32))")
Write-Host "Copy policy key for the agent: $env:OGK_POLICY_HMAC_KEY"
uvicorn app.main:app --app-dir server --reload
```

Mở dashboard tại `http://127.0.0.1:8000/`. Nhập `demo-child`, admin key vừa sinh ở PowerShell 1, tải policy. Chỉnh quota/lịch rồi nhấn **Lưu và phát hành policy**. API sẽ tăng version policy.

Trong PowerShell số 2, cài dependency agent và đặt cùng policy key đã sinh ở PowerShell 1:

```powershell
pip install -r agent\requirements.txt
$env:OGK_SERVER_URL = 'http://127.0.0.1:8000'
$env:OGK_POLICY_HMAC_KEY = 'paste-the-same-policy-key-here'
python agent\main.py
```

Agent gửi heartbeat lần đầu, nhận policy có version/chữ ký và in trạng thái khi chấp nhận. Sau khi sửa policy, heartbeat sau đó (tối đa khoảng 60 giây) đồng bộ version mới.

Để chỉ quan sát thông báo, không cần cấu hình thêm. Trên **máy lab riêng đã lưu công việc và sẵn sàng bị khóa**, có thể bật thao tác khóa bằng `$env:OGK_ENABLE_WINDOWS_LOCK = '1'` trước khi khởi chạy agent. Không bật trên máy đang dùng làm việc thường ngày; cờ này chỉ điều khiển API khóa máy Windows, không phải cơ chế khóa phụ huynh.

## Cách kiểm tra thủ công

- Đặt quota nhỏ để quan sát cảnh báo/đếm giờ; lịch mặc định ban đầu cho phép cả tuần.
- Tắt một vài ô 30 phút, phát hành policy và quan sát agent nhận version mới.
- Thay một ký tự trong policy signature khi kiểm tra ở môi trường phát triển; agent phải từ chối policy đó và giữ cache cũ.
- Cấu hình sai admin key; API đọc/ghi policy phải trả lỗi 403.
- Thử `weekly_schedule` thiếu ngày/ô hoặc quota ngoài khoảng; API phải từ chối đầu vào.
- Kiểm tra Windows Lock API chỉ trên máy lab. Không yêu cầu agent chạy bằng SYSTEM.

Các bước trên là gợi ý kiểm tra; hiện chưa có test tự động và chưa có kết quả đo được ghi nhận.

## Giới hạn còn lại

- Admin key là khóa chia sẻ demo, không thay thế tài khoản phụ huynh, xác thực, phân quyền theo gia đình, CSRF hay audit log.
- Dùng HTTP loopback, chưa có TLS và xác thực thiết bị.
- Cache JSON của agent chưa có ACL riêng hoặc chống sửa; chỉ policy được kiểm tra HMAC.
- Khi quota đã hết, nếu agent khởi động lại thì khoảng ân hạn 60 giây bắt đầu lại.
- Agent xử lý giờ lịch/idle, nhưng còn cần đo sai số, đặc biệt khi máy ngủ, mạng chậm hoặc thay đổi timezone.
- Chưa có scheduler/Windows service để tự khởi chạy sau reboot; chưa có tray UI hiển thị cho trẻ.
- Chưa có audit lịch sử phiên bản, undo policy, lệnh khẩn, policy hết hạn, fail-open/closed ngoại tuyến hay ngoại lệ khẩn cấp.
- Không triển khai cho trẻ hoặc người dùng thật trước khi hoàn thiện consent, TLS, authentication, access control, UI minh bạch và kiểm thử.
