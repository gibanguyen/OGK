# Báo cáo tiến độ tuần 3 — OGK

> Báo cáo này ghi lại trạng thái tại tuần 3. Sau đó project được bổ sung đăng nhập phụ huynh, ghép đôi một lần, token thiết bị, Tray UI và thực thi phút được duyệt. Trạng thái hiện tại và cách chạy nằm trong [README](../README.md).

## 1. Mục tiêu tuần 3

Tuần 3 tập trung vào F2 (lọc domain và kiểm soát app) cùng F3 (ghi nhận/báo cáo minh bạch). Phần đã làm là prototype để trình diễn luồng dữ liệu trong môi trường lab. Chưa thể tuyên bố đạt các tiêu chí nghiệm thu như chặn 95% domain, không có false positive trên 20 site giáo dục, hay kiểm soát bypass DoH/VPN.

## 2. Những phần đã triển khai

### 2.1. Phân loại domain

- Tạo [shared/domain_catalog.py](../shared/domain_catalog.py) với 200 domain fixture thuộc sáu nhóm: giáo dục, giải trí, mạng xã hội, trò chơi, không phù hợp lứa tuổi và chưa phân loại.
- Tất cả fixture nằm dưới TLD dành cho thử nghiệm `.test`; chúng không trỏ đến nội dung người lớn hoặc website công cộng.
- Policy mặc định cho nhóm tuổi nhỏ chặn nhóm mạng xã hội, trò chơi và không phù hợp lứa tuổi. Nhóm giáo dục trong fixture được ưu tiên cho phép vĩnh viễn. Nhóm chưa phân loại được phép mặc định để hạn chế chặn nhầm; phụ huynh có thể chọn chặn qua dashboard.
- `GET /api/domain-catalog` trả tổng số, số lượng từng nhóm và danh sách domain để xem/đối chiếu.

**Giới hạn:** đây là bộ fixture kiểm thử do nhóm tạo, không phải danh sách phân loại 200 website thật đã được nghiên cứu. Muốn đánh giá sản phẩm với website thực cần chọn danh sách công khai, an toàn, kiểm chứng nguồn và ghi nhận false positive.

### 2.2. DNS proxy loopback

- [agent/dns_proxy.py](../agent/dns_proxy.py) nhận truy vấn DNS UDP, đối chiếu domain chính xác hoặc domain con với `blocked_domains`, `blocked_categories` và allowlist.
- Domain bị chặn nhận `NXDOMAIN`; domain không bị chặn được chuyển tiếp tới resolver IP do người chạy tự cấu hình.
- Proxy chỉ bind `127.0.0.1`, mặc định cổng `5353`. Nó không tự đổi DNS của Windows, không mở cổng ra mạng LAN và không cấu hình firewall.
- Query/event chỉ ghi domain gốc gần đúng (hai nhãn cuối), không ghi URL, path, query string hay tiêu đề trang.
- Có allowlist cố định cho hotline/domain ví dụ cùng fixture giáo dục. Cuộc gọi khẩn cấp của hệ điều hành không bị DNS proxy kiểm soát.

**Giới hạn:** proxy hiện chỉ xử lý UDP DNS, chưa có TCP fallback, DNS-over-HTTPS/DoT detection, SafeSearch rewrite, DNS cache, WFP hay certificate pinning. Domain cha/mẹ đặt tên có public suffix nhiều nhãn có thể bị rút gọn chưa chính xác vì chưa dùng Public Suffix List.

### 2.3. Kiểm soát app theo tên và SHA-256

- [agent/activity_monitor.py](../agent/activity_monitor.py) chỉ quan sát tên tiến trình foreground và thời gian; không đọc tiêu đề cửa sổ, phím gõ, nội dung tài liệu hay ảnh màn hình.
- Policy app rule yêu cầu đủ `name`, `sha256` 64 ký tự hex và `reason`; chỉ khi tên và hash đều khớp mới được coi là rule match.
- Mặc định collector và terminate process đều tắt. Thu thập metadata cần bật `OGK_ENABLE_ACTIVITY_COLLECTION=1`; dừng tiến trình cần bật thêm `OGK_ENABLE_PROCESS_BLOCKING=1`.
- Khi bật terminate, agent in lý do ra terminal trước khi kết thúc tiến trình. Đây là giải thích demo dạng console; chưa có popup/tray UI.

**Giới hạn:** chỉ lấy foreground app theo chu kỳ 5 giây, nên có thể bỏ qua app mở rất ngắn hoặc app nền. Chưa đo hiệu năng và chưa kiểm tra trên nhiều loại tiến trình Windows. Tên/hash cần người quản trị tự lấy, ví dụ `Get-FileHash 'C:\Path\app.exe' -Algorithm SHA256`.

### 2.4. Sự kiện và báo cáo

- Agent gửi `app_start`, `app_stop`, `domain_query`, `blocked_domain`, `blocked_app`, `quota_warning`, `locked` và `unlock_request` theo schema event hiện có.
- Server chuẩn hóa `subject` của domain về hai nhãn cuối và từ chối path/URL. Với app, server lưu basename, không lưu đường dẫn executable.
- `GET /api/reports/{child_id}` tổng hợp 7 ngày: thời lượng theo ngày, top 10 app, top 10 domain và số lần bị chặn.
- Dashboard phụ huynh có biểu đồ cột theo ngày, danh sách app/domain và khu vực duyệt yêu cầu.
- `/child/{child_id}` cho trẻ xem cùng báo cáo, gửi yêu cầu xin thêm 15 phút hoặc báo app/domain bị chặn nhầm, và xem trạng thái phản hồi.
- Yêu cầu được lưu trong bảng `child_requests`. Parent có thể phản hồi duyệt/từ chối; phản hồi số phút hiện chỉ được ghi nhận trong prototype, chưa phát hành lệnh cộng thời gian xuống agent.

**Giới hạn:** báo cáo dùng event `app_stop` để cộng thời gian; tổng có thể chưa chính xác khi agent dừng đột ngột, máy ngủ, hoặc app foreground không được lấy mẫu. Hiện chưa có idempotency key, retention purge 90 ngày, xóa toàn bộ hồ sơ/cache, auth/IDOR protection hay audit log sửa policy. Các endpoint demo chưa nên mở lên Internet.

## 3. Cách chạy demo tuần 3

1. Cài dependency server và agent theo README. Server và agent phải dùng cùng `OGK_POLICY_HMAC_KEY`; dashboard cần `OGK_ADMIN_KEY`.
2. Chạy server ở localhost, ghép `demo-device` với `demo-child`, vào dashboard và nạp policy.
3. Để thu metadata ứng dụng, bật `$env:OGK_ENABLE_ACTIVITY_COLLECTION = '1'` trước khi chạy agent. Terminal sẽ thông báo rõ việc thu tên app và thời lượng.
4. Để thử chặn app, thêm JSON rule gồm tên executable, SHA-256, reason. Chỉ bật `$env:OGK_ENABLE_PROCESS_BLOCKING = '1'` trên máy lab đã chuẩn bị; cờ này có thể kết thúc ứng dụng đang foreground nếu hash trùng.
5. Để bật proxy, đặt `$env:OGK_ENABLE_DNS_PROXY = '1'`; mặc định nghe ở `127.0.0.1:5353`. Không cấu hình adapter Windows sang proxy.
6. Nếu cần phân giải các domain cho phép, tự chỉ định IP resolver lab được phép dùng qua `$env:OGK_DNS_UPSTREAM`. Resolver đó sẽ thấy các domain được chuyển tiếp; đừng dùng resolver không được nhóm chấp thuận.
7. Tra cứu fixture bị chặn bằng DNS client hỗ trợ chỉ định cổng 5353, ví dụ trong `nslookup`: `server 127.0.0.1`, `set port=5353`, `game-fixture-001.test`. Kết quả dự kiến là NXDOMAIN.
8. Mở dashboard phụ huynh và `/child/demo-child` để xem báo cáo/yêu cầu. Nếu không bật activity collection hoặc DNS proxy, phần tương ứng sẽ chưa có dữ liệu.

Không dùng nội dung người lớn để thử. Không chạy với máy của trẻ/người khác nếu chưa có đồng ý rõ ràng. Cờ kết thúc process và cổng 53 không cần thiết cho demo mặc định; không bật cổng 53 hoặc sửa DNS toàn hệ thống.

## 4. File chính đã thay đổi/thêm

- `server/app/models.py`: bảng `ChildRequest`.
- `server/app/schemas.py`: domain list, category selection, app rule và request schemas.
- `server/app/main.py`: catalog, report, child page, request/review APIs và dashboard.
- `agent/dns_proxy.py`: DNS UDP proxy chỉ bind loopback.
- `agent/activity_monitor.py`: collector foreground và hash-based app rule.
- `agent/main.py`: khởi chạy worker opt-in, phát event.
- `shared/domain_catalog.py`: 200 fixture an toàn `.test`.
- `agent/requirements.txt`: thêm `dnslib`, `psutil`.
- `README.md`: hướng dẫn chạy các worker tuần 3.

## 5. Tóm tắt để trình bày miệng

“Tuần 3, nhóm nối policy với hai loại dữ liệu hoạt động tối thiểu: tên app foreground/thời lượng và domain được truy vấn. Bộ domain demo có 200 fixture `.test` để kiểm tra sáu nhóm mà không truy cập nội dung nhạy cảm. DNS proxy chỉ bind loopback, không tự thay DNS của máy. Agent có thể xác minh app bằng tên cộng SHA-256; việc thu thập và kết thúc app đều cần bật rõ bằng cờ môi trường. Server tổng hợp thời lượng, top app/domain, lượt chặn; trẻ xem cùng báo cáo và có thể gửi yêu cầu. Đây mới là prototype: chưa có TLS/auth đầy đủ, DoH/SafeSearch, popup/tray, retention 90 ngày, kiểm thử hiệu năng và chống bypass hoàn chỉnh.”

## 6. Kiểm chứng

Trong lần tạo phần này chưa chạy test tự động hay bật agent trên máy thật. Cần thực hiện checklist demo trong `docs/WEEK2.md` và bổ sung unit/integration tests, các mẫu đo tối thiểu 30 lần cùng đánh giá false positive trước khi nộp kết quả nghiệm thu.
