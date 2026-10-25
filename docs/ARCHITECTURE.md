# Kiến trúc prototype OGK

## Phạm vi hiện có

Agent Windows chạy công khai trong phiên người dùng và có Tray UI. Phụ huynh đăng nhập bằng cookie phiên, tạo hồ sơ trẻ và mã ghép đôi một lần. Agent đổi mã lấy device token; heartbeat/event/request được xác thực bằng token đó. Server lưu tài khoản, hồ sơ, thiết bị, sự kiện và policy trong SQLite/SQLAlchemy; phụ huynh quản lý quota, lịch 7×48, domain/app và yêu cầu trên dashboard.

```text
Parent -- login + create pairing code --> FastAPI -- SQLite
                                          |          parent/child/device
Child Tray -- enroll(code) ------------->| 
Child Tray <-- child_id + device token --|
Agent -- heartbeat/events + token ------>| 
Agent <-- server_time + signed policy/commands --|
Parent -- dashboard: policy/report/review --> FastAPI
```

Mặc định chạy HTTP loopback cho lab. Khi dùng nhiều máy, phải cấu hình HTTPS/TLS và `OGK_COOKIE_SECURE=1`. Chưa có audit log thay đổi, WebSocket hay Windows Service. `OGK_ENABLE_WINDOWS_LOCK` mặc định tắt; khi bật, agent có thể gọi `LockWorkStation` trên máy Windows đang chạy.

## Policy thời gian

Payload policy gồm `weekday_quota_min`, `weekend_quota_min`, `timezone` và `weekly_schedule`. `weekly_schedule` là 7 mảng (Thứ 2 đến Chủ nhật), mỗi mảng 48 Boolean tương ứng từng khoảng 30 phút. Ô `true` cho phép hoạt động trong khoảng đó; `false` khiến agent yêu cầu khóa máy nếu bật chế độ khóa.

Server chuẩn hóa JSON bằng cách sắp xếp key và bỏ khoảng trắng, sau đó ký HMAC-SHA256 với `OGK_POLICY_HMAC_KEY`. Agent tính lại chữ ký bằng khóa cùng giá trị, so sánh an toàn và chỉ thay cache nếu hợp lệ. Agent báo version đang dùng ở heartbeat; nếu version lệch, server gửi policy mới. Nhịp heartbeat agent là 60 giây; kiểm tra giờ và quota cục bộ chạy mỗi giây.

## Bộ đếm

Agent chỉ cộng thời gian khi lịch đang cho phép, phiên desktop Windows chưa khóa và Windows báo có thao tác trong 5 phút gần nhất. Phần thời lượng được tính bằng `time.monotonic()` để không bị lùi/tiến đồng hồ tường làm thay đổi số giây trong phiên hiện tại. Mốc ngày và quota được ghi trong `%LOCALAPPDATA%\OGK\state.json`. Agent neo thời gian vào `server_time` ở mỗi heartbeat và tiếp tục trôi bằng monotonic clock giữa các nhịp.

Khi quota còn 10, 5 hoặc 1 phút, agent in thông báo và gửi `quota_warning`. Khi quota hết, agent cho thêm 60 giây rồi yêu cầu khóa. Ngoài lịch, agent yêu cầu khóa ngay trong vòng một nhịp kiểm tra. Nếu `OGK_ENABLE_WINDOWS_LOCK` chưa bật, agent chỉ báo rõ “chế độ khóa Windows đang tắt” và không khóa máy.

## Quyết định và giới hạn

- Mọi dữ liệu hoạt động theo schema tối thiểu; `subject` không phải URL đầy đủ.
- Agent Windows chạy hiển thị trong notification area; chọn “Thoát OGK” để dừng. Chưa đóng gói thành Windows service/installer.
- Đăng nhập phụ huynh dùng mật khẩu PBKDF2 và cookie HMAC `HttpOnly`; database local chỉ cho tạo tài khoản đầu tiên.
- Mã ghép đôi được phụ huynh tạo, dùng một lần, hết hạn sau 10 phút; thiết bị nhận token riêng và gửi token trong heartbeat/event/request.
- Policy, báo cáo, thiết bị và yêu cầu trong dashboard được giới hạn theo hồ sơ thuộc tài khoản phụ huynh.
- HMAC xác minh tính toàn vẹn policy trong mô hình demo, không mã hóa payload và không thay thế TLS/xác thực thiết bị.
- Bộ đếm có thể bị ảnh hưởng nếu người dùng sửa file cache hoặc thay đổi ngày khi offline; cần bảo vệ ACL và thiết kế đồng bộ tin cậy trước triển khai.
- Khi quota đã hết mà agent khởi động lại, khoảng ân hạn 60 giây bắt đầu lại. Đây là giới hạn cần xử lý trước khi đánh giá nghiệm thu.
- Yêu cầu xin thêm giờ/báo chặn nhầm được lưu và phản hồi qua dashboard. Phút duyệt được chuyển ở heartbeat và cộng quota trong ngày; agent chống áp dụng lặp bằng danh sách request ID lưu cục bộ.
- HTTP chỉ dùng cho loopback/local lab. Khi chạy nhiều máy cần HTTPS/TLS, đặt `OGK_COOKIE_SECURE=1`; chưa có xác minh email, khôi phục tài khoản, rate limit toàn diện, CSRF/audit log hay thu hồi thiết bị.

## Các bước tiếp theo

1. Thêm kiểm thử cho quota, lịch biên 30 phút, HMAC đúng/sai, lệch version và thay đổi đồng hồ.
2. Bổ sung kiểm soát thử sai, đổi/khôi phục mật khẩu, CSRF/audit log và thu hồi thiết bị.
3. Hoàn thiện installer/startup Windows có lựa chọn rõ ràng, xử lý mạng gián đoạn và chống áp dụng lệnh trùng bền vững.
4. Hoàn thiện bộ đếm ngoại tuyến và chống sửa cache; đo sai số so với đồng hồ tham chiếu.
5. Chỉ sau đó mới thử DNS/app control trong máy lab có đồng ý; không dùng nội dung nguy hiểm để thử.

## Bổ sung tuần 3

- Policy thêm allow/block domain, category bị chặn và tên tiến trình ứng dụng bị chặn. Tên được so khớp không phân biệt hoa thường; có thể ghi hoặc bỏ `.exe`.
- [shared/domain_catalog.py](../shared/domain_catalog.py) tạo 200 domain fixture `.test`, chia thành sáu nhóm; đây là dữ liệu demo, không phải phân loại website thật.
- DNS proxy UDP tùy chọn chỉ bind `127.0.0.1:5353`, không đổi cấu hình DNS hệ điều hành. Domain bị block trả NXDOMAIN; domain được phép được chuyển tiếp đến IP upstream do người vận hành chỉ định. Khi upstream không được cấu hình, proxy trả SERVFAIL cho domain được phép thay vì tự gửi dữ liệu đi nơi khác.
- Theo dõi app foreground bật mặc định trong cấu hình hiện tại (tắt bằng `OGK_ENABLE_ACTIVITY_COLLECTION=0`), chỉ giữ tên executable và thời lượng. Rule ứng dụng so khớp tên tiến trình; agent không đọc file executable để tính hash.
- DNS proxy bật mặc định nhưng chỉ lắng nghe loopback, không tự đổi DNS hệ điều hành; có thể tắt bằng `OGK_ENABLE_DNS_PROXY=0`. Nếu không đặt upstream thì domain được phép nhận SERVFAIL.
- API report tổng hợp event app/domain; trang `/child/{child_id}` hiển thị dữ liệu cùng báo cáo và nhận yêu cầu của trẻ.
- DNS/app worker đều hiện trạng thái ở terminal; kết thúc process chỉ khi đặt thêm `OGK_ENABLE_PROCESS_BLOCKING=1`.

Các route report/child request vẫn là prototype, chưa có authentication và chống IDOR; chỉ dùng loopback/lab cho tới khi phân quyền tuần 4 được làm xong.
