# OGK — Hỗ trợ gia đình quản lý việc sử dụng máy tính

Prototype học tập cho đồ án theo yêu cầu trong `QuanLyMayTinhTreEm.docx`. Project có dashboard phụ huynh, agent Windows chạy ở khay hệ thống (system tray), đăng nhập, ghép đôi mã dùng một lần, đồng bộ policy có chữ ký, báo cáo hoạt động và yêu cầu xin thêm thời gian.

OGK hiển thị rõ khi chạy. Agent chỉ gửi tên ứng dụng/domain, thời lượng và sự kiện cần thiết; không ghi nội dung trang, tin nhắn, phím bấm hay ảnh màn hình. Đây vẫn là prototype lab: chưa có HTTPS tự cấu hình, xác minh email/khôi phục tài khoản, giới hạn thử sai toàn diện, cài đặt dịch vụ Windows hoặc biện pháp chống gỡ. Đăng nhập và ghép đôi có giới hạn thử đơn giản theo địa chỉ IP trong tiến trình server. Không dùng để giám sát hay khóa thiết bị ngoài lab.

## Tính năng

- **Đăng nhập phụ huynh:** tài khoản đầu tiên đăng ký một lần; mật khẩu được băm PBKDF2. Phiên dùng cookie `HttpOnly`, hết hạn sau 7 ngày.
- **Ghép đôi:** phụ huynh tạo mã 8 ký tự cho hồ sơ trẻ. Mã chỉ dùng một lần, hết hạn sau 10 phút. Agent nhận token riêng của thiết bị và lưu tại `%LOCALAPPDATA%\OGK\agent.json`.
- **Tray UI phía trẻ:** mở OGK từ notification area để xem trạng thái, gửi yêu cầu thêm 15 phút/báo chặn nhầm hoặc mở báo cáo minh bạch. Đóng cửa sổ sẽ thu nhỏ về tray; chọn “Thoát OGK” để dừng.
- **Giới hạn và lọc:** policy có quota, lịch tuần, nhóm/domain/app; agent nhận policy qua heartbeat và xác minh HMAC-SHA256. DNS proxy là tùy chọn, chỉ nghe loopback và không tự đổi DNS của Windows.
- **Báo cáo:** dashboard phụ huynh hiển thị thời lượng, top app/domain và sự kiện bị chặn. Trẻ mở báo cáo bằng tray; token thiết bị nằm trong URL fragment và không được gửi trong HTTP request.
- **Xin thêm thời gian:** phụ huynh duyệt yêu cầu trên dashboard; heartbeat tiếp theo chuyển lệnh tới agent và cộng số phút đã duyệt vào quota của ngày đó.

## Chạy trên Windows

Mở PowerShell tại thư mục `CD1` và cài dependencies:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r server\requirements.txt
pip install -r agent\requirements.txt
```

Khởi động server trong cửa sổ PowerShell thứ nhất:

```powershell
uvicorn app.main:app --app-dir server --reload
```

Mở `http://127.0.0.1:8000/`. Server tự sinh `OGK_SESSION_KEY` và `OGK_POLICY_HMAC_KEY` khi dashboard/auth được truy cập lần đầu, đặt chúng vào môi trường của tiến trình server và lưu ở `%LOCALAPPDATA%\OGK\server-secrets.json`. Giữ nguyên file này khi khởi động lại; xóa file sẽ làm mất phiên đăng nhập và agent cần đồng bộ lại khóa. Chọn **Tạo tài khoản đầu tiên** để đăng ký phụ huynh. Tạo hồ sơ và mã ghép đôi trong phần **Ghép đôi máy của trẻ**. Dashboard chỉ cho phép đăng ký tài khoản đầu tiên trong database local.

Khởi động agent trong cửa sổ PowerShell thứ hai:

```powershell
.\.venv\Scripts\Activate.ps1
python agent\main.py
```

Các giá trị agent có mặc định: server `http://127.0.0.1:8000`, DNS proxy port `5353`, thu thập metadata app bật và DNS proxy bật. Cổng `5353` chỉ dành cho truy vấn demo gửi trực tiếp; Windows và trình duyệt thường gửi DNS qua cổng `53`, nên mặc định proxy không chặn truy cập web. Đặt `OGK_ENABLE_ACTIVITY_COLLECTION=0` hoặc `OGK_ENABLE_DNS_PROXY=0` để tắt từng tính năng. Domain được phép nhận SERVFAIL nếu chưa cấu hình upstream. `OGK_POLICY_HMAC_KEY` tự nhận qua mã ghép đôi và được lưu trong cấu hình agent. Nhấp biểu tượng OGK ở khay hệ thống, nhập mã do phụ huynh vừa tạo. Khi ghép đôi xong, agent tự đồng bộ policy. Nếu phụ huynh và trẻ chạy trên hai máy khác nhau, chỉ dùng qua HTTPS (ví dụ reverse proxy đã cấu hình TLS), đặt `OGK_SERVER_URL` thành địa chỉ `https://...` và bật `OGK_COOKIE_SECURE=1` ở server. Không gửi mật khẩu, mã ghép đôi hay token qua HTTP không mã hóa trên LAN. Để báo cáo trên máy trẻ mở được server, agent sẽ mở địa chỉ server đã cấu hình.

### Tùy chọn demo

```powershell
$env:OGK_DNS_UPSTREAM = 'IP_OF_APPROVED_LAB_RESOLVER'
$env:OGK_ENABLE_PROCESS_BLOCKING = '1'
$env:OGK_ENABLE_WINDOWS_LOCK = '1'
```

DNS forwarding gửi tên miền được phép tới resolver bên ngoài; resolver đó có thể thấy tên miền truy vấn. Các tùy chọn trên chỉ dành cho máy lab. Mặc định process blocking và khóa Windows tắt. DNS proxy không can thiệp DNS-over-HTTPS, VPN hoặc thay đổi resolver hệ thống.

### Kiểm tra rule DNS

1. Trong dashboard, nhập domain vào **Domain chặn**. Nếu domain cũng xuất hiện trong **Domain luôn cho phép**, hãy xóa nó khỏi danh sách cho phép. Lưu policy và chờ agent nhận policy mới (tối đa khoảng 60 giây).
2. Trên máy trẻ, xác nhận Tray hiển thị policy version mới và terminal agent có dòng `DNS proxy listening only on 127.0.0.1:5353`.
3. Để kiểm tra proxy trực tiếp mà không đổi DNS của Windows, chạy trong PowerShell trên máy trẻ:

   ```powershell
   python -c "import socket; from dnslib import DNSRecord, RCODE; q=DNSRecord.question('example.com','A').pack(); s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(3); s.sendto(q,('127.0.0.1',5353)); r=DNSRecord.parse(s.recvfrom(2048)[0]); print('NXDOMAIN (blocked)' if r.header.rcode==RCODE.NXDOMAIN else RCODE[r.header.rcode])"
   ```

   Dùng domain bạn đã chặn thay `example.com`. Kết quả `NXDOMAIN (blocked)` chứng minh rule được áp dụng khi truy vấn gửi trực tiếp tới proxy. Kết quả `SERVFAIL` thường có nghĩa proxy chưa nhận policy chặn domain đó hoặc domain đang được allowlist. Sau heartbeat kế tiếp (tối đa khoảng 60 giây), Tray hiển thị các domain/nhóm đã đồng bộ; danh sách này không khẳng định trình duyệt đang dùng proxy. Khi proxy trả `NXDOMAIN`, Tray/server ghi sự kiện `blocked_domain`.

### Chặn truy cập từ trình duyệt trong máy lab (tùy chọn)

Chỉ làm bước này trên máy thử nghiệm của bạn. Việc đổi DNS có thể làm mất phân giải tên miền nếu agent dừng; lưu DNS hiện tại và khôi phục trước khi thoát agent. Cần một resolver upstream IPv4 được phép dùng, ví dụ DNS router trong lab.

1. Mở PowerShell **Run as administrator** tại thư mục `CD1`. Trong cửa sổ đầu tiên, cấu hình proxy dùng cổng DNS chuẩn và upstream rồi chạy agent:

   ```powershell
   $env:OGK_DNS_PORT = '53'
   $env:OGK_DNS_UPSTREAM = '192.168.1.1' # thay bằng IPv4 DNS router/resolver trong lab
   python agent\main.py
   ```

   Xác nhận terminal báo `DNS proxy listening only on 127.0.0.1:53`.
2. Trong PowerShell admin thứ hai, xem tên adapter đang dùng, lưu DNS hiện tại vào biến và trỏ DNS IPv4 adapter đó về proxy:

   ```powershell
   Get-NetIPConfiguration | Where-Object { $_.NetAdapter.Status -eq 'Up' } | Select-Object InterfaceAlias, @{Name='Dns';Expression={$_.DNSServer.ServerAddresses -join ', '}}
   $interface = 'Wi-Fi' # thay đúng tên adapter ở kết quả trên
   $oldDns = (Get-DnsClientServerAddress -InterfaceAlias $interface -AddressFamily IPv4).ServerAddresses
   Set-DnsClientServerAddress -InterfaceAlias $interface -ServerAddresses 127.0.0.1
   ipconfig /flushdns
   ```

3. Tắt Secure DNS/DoH trong trình duyệt, đóng rồi mở lại. Thử truy vấn trực tiếp:

   ```powershell
   Resolve-DnsName -Name example.com -Server 127.0.0.1 -DnsOnly
   ```

   Domain đã chặn cần trả NXDOMAIN. Nếu truy vấn trực tiếp bị NXDOMAIN nhưng trình duyệt vẫn mở được trang, trình duyệt đang dùng cache hoặc DNS khác (ví dụ DoH/VPN/adapter khác).
4. Khôi phục DNS **trước khi dừng agent** trong PowerShell admin thứ hai:

   ```powershell
   if ($oldDns.Count -gt 0) { Set-DnsClientServerAddress -InterfaceAlias $interface -ServerAddresses $oldDns } else { Set-DnsClientServerAddress -InterfaceAlias $interface -ResetServerAddresses }
   ipconfig /flushdns
   ```

   Proxy hiện chỉ hỗ trợ UDP/IPv4 DNS trong phạm vi demo; chưa xử lý DoH/DoT, VPN hay mọi adapter/IPv6. Không xem cấu hình lab này là biện pháp chống vượt qua hoàn chỉnh.

### Quy tắc chặn ứng dụng

Trong ô **Tên ứng dụng**, nhập mỗi dòng một tên tiến trình, ví dụ `notepad`, `chrome` hoặc `msedge`; có thể thêm đuôi `.exe`, không nhập đường dẫn hay hash. Tên cần khớp tên tiến trình Windows. Việc kết thúc ứng dụng chỉ chạy khi bật `OGK_ENABLE_PROCESS_BLOCKING=1` trên agent. Khi chế độ này tắt, Tray chỉ ghi “ứng dụng khớp rule (chưa bị dừng)”, không đóng ứng dụng.

Sau khi agent nhận policy đã ký, Tray hiển thị domain, nhóm domain và tên ứng dụng trong mục **Danh sách domain / ứng dụng theo policy**. Đây là các rule đã đồng bộ; mục sự kiện gần đây bên dưới cho biết ứng dụng nào thực sự khớp hoặc bị dừng.

#### Demo chặn Notepad

1. Trên dashboard, chọn hồ sơ trẻ, nhập `notepad` (hoặc `notepad.exe`) vào ô **Tên ứng dụng** rồi lưu policy.
2. Đóng agent đang chạy. Mở PowerShell thứ hai tại thư mục `CD1`, bật chế độ chặn và khởi động lại agent:

   ```powershell
   $env:OGK_ENABLE_PROCESS_BLOCKING = '1'
   python agent\main.py
   ```

3. Chờ terminal báo đã nhận policy mới (hoặc tối đa khoảng 60 giây), rồi mở Notepad và để cửa sổ ở trước. Agent kiểm tra ứng dụng đang dùng khoảng mỗi 5 giây; Notepad sẽ bị dừng và sự kiện hiện trong Tray.

Chế độ này kết thúc tiến trình ngay. Chỉ thử với Notepad trống, không có nội dung chưa lưu. Tắt lại bằng cách đóng agent và chạy `python agent\main.py` trong PowerShell mới, không đặt biến `OGK_ENABLE_PROCESS_BLOCKING`.

## API chính

- `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/logout` — phiên phụ huynh.
- `POST /api/pairing-codes`, `GET /api/children` — tạo mã ghép đôi và liệt kê hồ sơ thuộc phụ huynh.
- `POST /api/enroll`, `GET /api/device-config/{device_id}` — đổi mã ghép đôi lấy token thiết bị và khóa xác minh policy; route config hỗ trợ agent đã ghép đôi đồng bộ khóa sau này.
- `POST /api/heartbeat`, `POST /api/events` — agent xác thực bằng `X-OGK-Device-Token`.
- `GET/PUT /api/policies/{child_id}` — quản lý policy phụ huynh đã đăng nhập.
- `GET /api/reports/{child_id}` — dashboard cần phiên phụ huynh; thiết bị đã ghép có thể xem bằng device token.
- `POST /api/child-requests`, `GET /api/child-requests/{child_id}`, `PATCH /api/child-requests/{id}` — yêu cầu trẻ và phản hồi phụ huynh.
- `GET /api/domain-catalog` — 200 domain fixture `.test` để minh họa phân loại.
- `GET /docs` — tài liệu OpenAPI.

Chi tiết kiến trúc, giới hạn và quyền riêng tư xem trong `docs/ARCHITECTURE.md`, `docs/PRIVACY.md`, `docs/THREATMODEL.md`, `docs/PIPELINE_CHAN_DOMAIN_UNG_DUNG.md` và các báo cáo tuần.
