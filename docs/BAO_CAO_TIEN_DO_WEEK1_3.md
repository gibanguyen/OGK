# Báo cáo tiến độ tuần 1–3 — OGK

> Đây là báo cáo theo mốc tuần 1–3 trước phần hoàn thiện giao diện/xác thực. Luồng hiện tại có đăng nhập phụ huynh, mã ghép đôi một lần, token thiết bị và Tray UI; xem [README](../README.md) để chạy phiên bản mới.

## 1. Tổng quan

Trong ba tuần đầu, nhóm đã xây dựng một prototype học tập cho hệ thống hỗ trợ quản lý việc sử dụng máy tính của trẻ em. Phạm vi tập trung vào ba phần: nền tảng server và ghép nối thiết bị (tuần 1), cấu hình giới hạn thời gian và đồng bộ policy (tuần 2), rồi lọc domain/app cùng báo cáo hoạt động minh bạch (tuần 3).

Sản phẩm hiện tại minh họa luồng dữ liệu trên máy local/lab. Đây chưa phải sản phẩm sẵn sàng triển khai cho người dùng thật, và chưa có số liệu để kết luận đạt các tiêu chí nghiệm thu về độ chính xác, hiệu năng hay khả năng chống vượt qua kiểm soát.

## 2. Kết quả theo từng tuần

### Tuần 1 — Nền tảng và luồng kết nối

- Xác định kiến trúc gồm `server/` (FastAPI, SQLite), `agent/` (chương trình chạy ở user mode), `shared/` (dữ liệu dùng chung) và `docs/`.
- Tạo các model cơ bản cho thiết bị và sự kiện; thiết lập các API khung cho health check, enrollment, heartbeat và ghi nhận event.
- Tạo luồng ghép nối demo bằng mã local, để agent đăng ký thiết bị và gửi heartbeat về server.
- Tạo dashboard mẫu và tài liệu hướng dẫn chạy, kiến trúc, quyền riêng tư và mô hình đe dọa.

**Kết quả:** có khung end-to-end để khởi chạy server, ghép nối agent demo và lưu dữ liệu nền tảng. Mã ghép nối hiện chỉ dành cho demo, chưa phải xác thực an toàn.

### Tuần 2 — Quota, lịch và đồng bộ policy

- Thêm model `Policy`, lưu version, payload JSON, chữ ký và thời điểm cập nhật.
- Xây dựng cấu hình quota ngày thường/cuối tuần và lịch tuần dạng 7 × 48 ô, mỗi ô tương ứng 30 phút; kiểm tra timezone, giới hạn quota và cấu trúc lịch ở đầu vào.
- Thêm API đọc/cập nhật policy có admin key demo. Server ký HMAC-SHA256 trên version và payload để agent kiểm tra tính toàn vẹn.
- Mở rộng dashboard để tải, sửa và phát hành policy.
- Agent đồng bộ policy qua heartbeat, kiểm tra chữ ký và lưu cache cục bộ; bộ đếm sử dụng monotonic clock, xử lý trạng thái idle/desktop bị khóa, cảnh báo trước khi hết giờ và khoảng ân hạn prototype.
- Thao tác khóa Windows được bảo vệ bằng cờ cấu hình và tắt mặc định.

**Kết quả:** có đường đi cấu hình từ dashboard qua server đến agent, với kiểm tra toàn vẹn policy. Chưa có tài khoản phụ huynh, phân quyền theo gia đình, chống sửa cache toàn diện hoặc xác minh độ chính xác qua các tình huống ngủ/khởi động lại.

### Tuần 3 — Domain/app, sự kiện và báo cáo

- Tạo catalog 200 domain fixture dưới TLD `.test`, chia sáu nhóm để demo phân loại mà không trỏ tới website thật.
- Thêm DNS proxy UDP chỉ bind `127.0.0.1:5353`. Proxy áp dụng category, domain block và allowlist; domain bị chặn trả NXDOMAIN, domain được phép có thể chuyển tiếp đến resolver do người chạy cấu hình.
- Thêm theo dõi metadata ứng dụng foreground và đối chiếu quy tắc theo tên tiến trình cùng SHA-256. Thu thập hoạt động và kết thúc process đều cần bật rõ bằng cấu hình; mặc định không cưỡng chế.
- Bổ sung API báo cáo và trang mẫu cho phụ huynh/trẻ; trẻ có thể xem báo cáo và gửi yêu cầu, phụ huynh có thể phản hồi yêu cầu.
- Hạn chế dữ liệu app ở tên tiến trình/thời lượng và dữ liệu web ở domain; không thu tiêu đề cửa sổ, phím gõ, nội dung tài liệu, URL path hay ảnh màn hình.

**Kết quả:** minh họa được luồng từ policy đến quan sát/chặn trong lab, rồi tổng hợp event thành báo cáo. Catalog là dữ liệu giả lập, không chứng minh chất lượng phân loại web thực tế.

## 3. Luồng hoạt động tổng thể

```text
Dashboard phụ huynh
        │ cập nhật policy
        ▼
FastAPI + SQLite ── heartbeat / policy có chữ ký ──► Agent trên máy demo
        ▲                                             │
        │ event và truy vấn báo cáo                   ├─ quota/lịch
        │                                             ├─ metadata app (opt-in)
        └─────────────────────────────────────────────└─ DNS proxy loopback (opt-in)
                                                      │
                                      Dashboard phụ huynh / trang báo cáo trẻ
```

Sơ đồ mô tả prototype ở mức khái quát. DNS proxy không tự đổi cấu hình DNS của Windows; cần client được cấu hình riêng để gửi truy vấn thử tới cổng loopback.

## 4. Thành phần và điểm vào chính

| Thành phần | Vai trò |
|---|---|
| `server/app/main.py` | API, dashboard mẫu, policy, event, báo cáo và yêu cầu của trẻ |
| `server/app/models.py` | Các bảng SQLAlchemy cho thiết bị, event, policy và yêu cầu |
| `server/app/schemas.py` | Kiểm tra dữ liệu đầu vào/đầu ra của API |
| `agent/main.py` | Khởi chạy agent, heartbeat, quota runtime và worker opt-in |
| `agent/policy_runtime.py` | Kiểm tra policy, đồng bộ cache và tính thời gian sử dụng |
| `agent/dns_proxy.py` | DNS proxy UDP cho demo trên loopback |
| `agent/activity_monitor.py` | Thu thập metadata foreground và so khớp quy tắc app |
| `shared/domain_catalog.py` | Catalog domain fixture `.test` dùng trong lab |
| `docs/` | Hướng dẫn tuần, kiến trúc, quyền riêng tư, threat model và báo cáo |

## 5. Cách trình diễn ngắn

1. Cài dependency server và agent theo hướng dẫn trong [README](../README.md), chạy server local.
2. Dùng mã enrollment demo để ghép agent; đảm bảo server và agent dùng cùng policy HMAC key.
3. Mở dashboard, tải/sửa policy cho `demo-child`, phát hành policy và quan sát agent nhận version mới.
4. Nếu cần minh họa hoạt động, chỉ bật cờ thu thập/DNS trong máy lab riêng. Có thể truy vấn fixture `.test` qua DNS proxy ở `127.0.0.1:5353`.
5. Mở dashboard phụ huynh và `/child/demo-child` để xem báo cáo/yêu cầu mẫu.

Các cờ có thể ảnh hưởng hệ thống như khóa Windows hay kết thúc process không cần cho demo cơ bản và nên để tắt. Không cấu hình DNS toàn hệ thống hoặc dùng máy của người khác/trẻ em khi chưa có sự đồng ý phù hợp.

## 6. Phạm vi đã làm và chưa được chứng minh

| Hạng mục | Trạng thái |
|---|---|
| API, SQLite, dashboard mẫu và enrollment demo | Có khung prototype |
| Quota, lịch 7 × 48, policy version/HMAC | Đã có luồng triển khai; cần kiểm chứng bằng test và tình huống biên |
| Domain catalog | 200 fixture `.test`, không phải 200 website thật đã được phân loại |
| DNS filtering | UDP demo trên loopback; chưa xử lý đầy đủ TCP, DoH/DoT, VPN hay bypass |
| App control | Metadata foreground và quy tắc tên + hash; không tương đương kiểm soát mọi app/process |
| Báo cáo | API/trang mẫu trên event hiện có; chưa chứng minh độ đầy đủ hoặc chính xác trong sử dụng dài hạn |
| Bảo mật triển khai | Chưa có TLS, đăng nhập người dùng và phân quyền đầy đủ; admin/enrollment key chỉ phục vụ demo |
| Tiêu chí nghiệm thu | Chưa có kết quả đo để khẳng định chặn 95%, không false positive trên 20 site giáo dục hay chống bypass |

## 7. Rủi ro và giới hạn cần nêu khi báo cáo

- Domain `.test` là dữ liệu giả lập. Không dùng kết quả demo để suy ra hiệu quả trên website thật.
- DNS proxy chỉ xử lý UDP DNS và không tự áp dụng cho toàn máy. DoH/DoT, VPN, TCP fallback, SafeSearch và nhiều đường bypass chưa được xử lý.
- App monitor chỉ quan sát foreground theo cơ chế hiện tại; không đọc nội dung sử dụng. Hash giúp ràng buộc nhận dạng rule nhưng chưa phải cơ chế bảo vệ agent khỏi người có quyền quản trị máy.
- Chưa hoàn thiện TLS, danh tính thiết bị, xác thực/phân quyền theo gia đình, CSRF, audit log, lưu giữ/xóa dữ liệu theo thời hạn và quy trình consent.
- Chưa có kết quả kiểm thử tự động, kiểm thử bảo mật, đo hiệu năng, đo độ chính xác hoặc chạy thử triển khai thực tế được ghi nhận trong báo cáo tuần 3.
- Yêu cầu tăng thời gian hiện được ghi nhận/phản hồi ở mức prototype; chưa thể hiện đầy đủ cơ chế phê duyệt làm thay đổi quota runtime.

## 8. Đề xuất công việc tiếp theo

1. Bổ sung test unit/integration cho schema policy, chữ ký HMAC, quota/lịch, domain matching, event và API phân quyền.
2. Thiết kế đăng nhập, authorization theo gia đình và thiết bị, TLS, chống CSRF, audit log, quản lý khóa và cơ chế thu hồi enrollment.
3. Chốt chính sách consent, tối thiểu hóa dữ liệu, thời hạn lưu và tự động xóa; cung cấp cách xem/xóa dữ liệu rõ ràng.
4. Chỉ đánh giá domain thật với danh sách công khai, an toàn, có nguồn và tiêu chí chấm rõ; ghi nhận false positive/negative thay vì khẳng định trước kết quả.
5. Đánh giá giới hạn DNS và app control trong môi trường lab được phép; ghi rõ trường hợp bypass và hành vi fail-open/fail-closed.
6. Hoàn thiện trải nghiệm trẻ/phụ huynh, luồng yêu cầu thêm thời gian, khởi động agent, thông báo và cơ chế gỡ cài đặt minh bạch.
7. Sau khi có kết quả kiểm thử/đo đạc, cập nhật riêng báo cáo nghiệm thu; không đồng nhất “đã triển khai prototype” với “đã đạt chỉ tiêu”.

## 9. Tóm tắt trình bày miệng

> Trong tuần 1 đến tuần 3, nhóm đã xây dựng prototype OGK gồm server FastAPI/SQLite, agent demo và dashboard. Tuần 1 hoàn thành khung API, dữ liệu và ghép nối; tuần 2 thêm quota, lịch sử dụng, đồng bộ policy có chữ ký HMAC và bộ đếm cục bộ; tuần 3 nối policy với DNS/app ở mức lab, ghi nhận event và hiển thị báo cáo cho phụ huynh/trẻ. Thiết kế ưu tiên minh bạch và giới hạn dữ liệu thu thập. Tuy nhiên đây mới là prototype: domain là fixture `.test`, DNS chỉ là UDP loopback, bảo mật triển khai và các bài đo nghiệm thu còn thiếu. Vì vậy nhóm chưa kết luận đạt các chỉ tiêu về độ chính xác hoặc chống bypass.

## 10. Tài liệu liên quan

- [Báo cáo tuần 3](BAO_CAO_WEEK3.md)
- [Ghi chú tuần 2](WEEK2.md)
- [Kiến trúc](ARCHITECTURE.md)
- [Quyền riêng tư](PRIVACY.md)
- [Mô hình đe dọa](THREATMODEL.md)
