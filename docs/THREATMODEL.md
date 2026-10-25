# Threat model khởi đầu

Phạm vi hiện tại là prototype local, chưa phù hợp cho triển khai trên trẻ hoặc thiết bị không thuộc nhóm. Bảng dưới là các mối đe dọa cần kiểm chứng và giảm thiểu trước bản dùng thật.

| STRIDE | Tình huống | Biện pháp / việc cần làm |
|---|---|---|
| Spoofing | Thiết bị giả mạo heartbeat | Token gắn device_id, access token ngắn hạn, refresh token thu hồi được |
| Spoofing | Kẻ khác dùng mã ghép đôi | Mã ngẫu nhiên một lần, hết hạn 10 phút, yêu cầu xác thực phụ huynh |
| Tampering | Policy bị sửa trên đường truyền | TLS 1.2+, ký/HMAC policy, từ chối version/chữ ký sai |
| Tampering | Trẻ sửa cache cục bộ | ACL đúng trên Windows, kiểm tra toàn vẹn; không cài quyền SYSTEM ở prototype |
| Repudiation | Phụ huynh phủ nhận đã đổi lịch | Audit log bất biến tương đối: ai, lúc nào, IP, giá trị cũ/mới; trẻ được xem |
| Information disclosure | Lộ dữ liệu hoạt động của trẻ | Tối thiểu hóa trường, phân quyền theo quyền sở hữu, xóa sau 90 ngày, mã hóa |
| Denial of service | Server/mạng mất kết nối | Cache policy có hạn, hàng đợi có idempotency key, hành vi hết hạn được công khai |
| Information disclosure | DNS resolver nhận truy vấn domain khi proxy forward | Upstream phải là IP được nhóm phê duyệt; proxy không tự chọn/ẩn resolver; nêu rõ truy vấn được gửi đi |
| Tampering | Domain catalog fixture bị hiểu nhầm là phân loại thật | Ghi rõ `.test` là dữ liệu giả lập; kiểm chứng mọi danh sách thật trước khi dùng |
| Elevation of privilege | IDOR cho phép đọc hồ sơ gia đình khác | Kiểm tra quyền sở hữu tại truy vấn API, kiểm thử riêng từng endpoint |
| Lạm dụng / stalkerware | Người lớn cài để bí mật theo dõi người khác | Hiển thị trạng thái, không ẩn/anti-uninstall kiểu mã độc, giới hạn phạm vi hợp pháp, quy trình gỡ rõ |
| Tấn công từ chính trẻ | Trẻ đổi DNS, DoH, VPN, safe mode hoặc kết thúc tiến trình | Nêu rõ giới hạn; ưu tiên trao đổi và cấu hình Windows phù hợp, không dùng kỹ thuật bí mật/độc hại |

## OWASP ASVS v4 Level 1 — checklist khởi đầu

- [ ] TLS 1.2+ và từ chối chứng chỉ không hợp lệ.
- [ ] Mật khẩu tối thiểu 10 ký tự, băm Argon2id/scrypt.
- [ ] Khóa tạm 15 phút sau 5 lần đăng nhập sai.
- [ ] Giới hạn tần suất đăng nhập theo IP.
- [ ] Cookie HttpOnly, Secure, SameSite=Lax.
- [ ] Phiên tối đa 12 giờ; logout thu hồi phía server.
- [ ] CSRF cho mọi thao tác ghi.
- [ ] Kiểm tra quyền sở hữu ở từng truy vấn (chống IDOR).
- [ ] Phân vai parent, child-view, admin.
- [ ] Kiểm tra đầu vào bằng schema và giới hạn kích thước.
- [ ] Truy vấn SQL tham số hóa/ORM.
- [ ] Không lưu URL đầy đủ hay nội dung riêng tư.
- [ ] Không ghi token/mật khẩu vào log.
- [ ] Access token thiết bị hết hạn ngắn và có thu hồi.
- [ ] Refresh token lưu dạng băm.
- [ ] Enrollment code dùng một lần và hết hạn.
- [ ] Policy có version và chữ ký HMAC/chữ ký số.
- [ ] Khóa mã hóa lấy từ env/DPAPI, không hardcode.
- [ ] Mã hóa trường nhạy cảm bằng AES-GCM khi có dữ liệu thật.
- [ ] Xóa tự động dữ liệu quá 90 ngày.
- [ ] Xóa dữ liệu của trẻ trên server và cache agent.
- [ ] Audit log có actor, thời điểm, IP, old/new value.
- [ ] Trẻ xem được báo cáo và log đổi luật.
- [ ] DNS proxy chỉ bind loopback, không tự cấu hình adapter; upstream IP được chỉ định rõ.
- [ ] Chặn app hiện chỉ so khớp theo tên tiến trình; có thể nhầm ứng dụng nếu dùng chung tên và không chống được việc đổi tên tiến trình.
- [ ] Hoạt động app/domain không lưu path, URL, tiêu đề cửa sổ hoặc nội dung.
- [ ] Tắt debug/reload và docs công khai trong cấu hình triển khai.
- [ ] CORS, headers, cấu hình secret và backup được rà soát.

Checklist là việc cần xác minh trong các bước sau, không phải tuyên bố đã đạt ASVS.
