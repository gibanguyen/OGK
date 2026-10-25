# Quyền riêng tư và tối thiểu dữ liệu

Mục tiêu là hỗ trợ gia đình cùng xây dựng thói quen số, không giám sát bí mật. Quyền riêng tư trẻ em và sự đồng ý của người giám hộ phải được xử lý trước khi ghép thiết bị. Đây là thiết kế học tập, không phải tư vấn pháp lý; nhóm cần tự kiểm chứng văn bản gốc.

| Trường dữ liệu | Mục đích | Thời hạn lưu dự kiến | Ai xem được |
|---|---|---|---|
| `device_id` | Gắn heartbeat/sự kiện với máy đã ghép | Khi thiết bị còn ghép; xóa khi gỡ | Phụ huynh của hồ sơ; trẻ xem trong trang của mình |
| `child_id` | Gom thiết bị và báo cáo cho đúng hồ sơ | Khi hồ sơ còn hoạt động | Phụ huynh sở hữu; trẻ |
| `last_seen` | Hiển thị trạng thái kết nối thiết bị | 90 ngày | Phụ huynh; trẻ xem trạng thái phù hợp |
| `policy_version` | Đồng bộ cấu hình và phát hiện phiên bản lệch | Vòng đời thiết bị | Phụ huynh; trẻ xem lịch sử thay đổi |
| `ts` | Báo cáo theo ngày/tuần, thứ tự sự kiện | 90 ngày | Phụ huynh và trẻ cùng hồ sơ |
| `type` | Thống kê hoạt động và giải thích hành động | 90 ngày | Phụ huynh và trẻ cùng hồ sơ |
| `subject` | Tên ứng dụng hoặc domain cấp hai để báo cáo/lý giải chặn | 90 ngày | Phụ huynh và trẻ cùng hồ sơ |
| `duration_sec` | Tổng hợp thời lượng dùng | 90 ngày | Phụ huynh và trẻ cùng hồ sơ |
| `policy_id` | Giải thích sự kiện theo luật nào | 90 ngày | Phụ huynh và trẻ cùng hồ sơ |
| Tên app foreground | Báo cáo ứng dụng và thời lượng khi theo dõi được bật | 90 ngày (sau khi xóa/purge được làm) | Phụ huynh và trẻ cùng hồ sơ |
| Domain gốc gần đúng | Báo cáo tên miền được tra cứu/chặn, không ghi URL | 90 ngày (sau khi xóa/purge được làm) | Phụ huynh và trẻ cùng hồ sơ |
| Yêu cầu của trẻ | Lưu loại yêu cầu, subject app/domain, trạng thái và phản hồi | 90 ngày (sau khi xóa/purge được làm) | Trẻ và phụ huynh cùng hồ sơ |

Không thu URL đầy đủ, tiêu đề cửa sổ, nội dung gõ, tin nhắn/email/tài liệu, ảnh chụp màn hình, âm thanh, vị trí hay lịch sử clipboard. Agent không gửi hoạt động tới dịch vụ analytics bên thứ ba. Nếu người vận hành bật DNS proxy và cấu hình resolver ngoài máy, resolver đó sẽ nhận tên miền cần phân giải; vì vậy chỉ được chọn resolver lab đã được nhóm chấp thuận. Cần bổ sung tác vụ tự xóa sau 90 ngày và luồng xóa đồng thời server/cache agent trước khi dùng ngoài dữ liệu demo.

Theo cấu hình chạy hiện tại, thu thập app foreground và DNS proxy bật mặc định; có thể tắt bằng `OGK_ENABLE_ACTIVITY_COLLECTION=0` và `OGK_ENABLE_DNS_PROXY=0`. Tray UI hiển thị trạng thái agent. Proxy chỉ lắng nghe loopback và không tự đổi cài đặt DNS của máy; ứng dụng không tự chuyển truy vấn tới proxy. Nếu người vận hành cấu hình upstream, resolver đó có thể nhìn thấy truy vấn được chuyển tiếp; nếu upstream để trống, domain được phép nhận SERVFAIL.

## Phiên bản cho trẻ

Máy chỉ gửi tên ứng dụng hoặc tên miền, thời gian và lý do áp dụng luật để con và cha mẹ cùng xem. Con có thể gửi yêu cầu xin thêm giờ hoặc báo trang bị chặn nhầm; người lớn cần phản hồi và con xem được phản hồi. Con biết khi OGK đang chạy qua biểu tượng/thông báo rõ ràng.

## Đối chiếu pháp lý cần xác minh

Nhóm cần kiểm tra nguyên văn Luật Trẻ em 2016 Điều 21, Luật An ninh mạng 2018 Điều 29, Nghị định 13/2023/NĐ-CP và Quyết định 830/QĐ-TTg từ nguồn chính thức; ghi rõ màn hình hoặc mã hiện thực hóa từng yêu cầu. Không xem bảng này là kết luận pháp lý.
