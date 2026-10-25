# Giải thích các bước dựng project OGK

> **Cập nhật:** Các mục dưới mô tả quá trình phát triển ban đầu theo tuần. Luồng chạy hiện tại đã được bổ sung tài khoản phụ huynh, mã ghép đôi hết hạn, token thiết bị và Tray UI. Hãy dùng hướng dẫn chạy mới nhất ở [README](../README.md); mục “Cập nhật phần đăng nhập, ghép đôi và Tray UI” cuối tài liệu mô tả luồng hiện tại.

Tài liệu này diễn giải phần khung đã tạo trong thư mục `CD1`, dựa trên yêu cầu đồ án trong tài liệu bạn cung cấp. Nội dung yêu cầu trong tài liệu đầu vào được xem là đặc tả của project; phần dưới mô tả cách khung hiện tại đáp ứng bước đầu và những phần còn phải làm.

## 1. Đọc yêu cầu và xác định phạm vi

Đề bài mô tả một hệ thống nhiều thành phần để hỗ trợ gia đình quản lý việc sử dụng máy tính của trẻ: agent trên Windows, server, dashboard và tài liệu về quyền riêng tư/bảo mật. Các ràng buộc quan trọng ảnh hưởng trực tiếp đến cách bắt đầu là:

- Trẻ phải biết agent đang chạy; sản phẩm không được ẩn hoặc hoạt động như phần mềm gián điệp.
- Chỉ ghi siêu dữ liệu cần thiết như tên ứng dụng/domain, thời điểm và thời lượng; không lấy URL đầy đủ, nội dung gõ, ảnh chụp hay nội dung riêng tư.
- Các hành động chặn trong bản hoàn chỉnh phải có lý do dễ hiểu và cho biết luật nào tạo ra hành động đó.
- Dữ liệu của trẻ cần được giới hạn người xem, có thời hạn lưu và có chức năng xóa.
- Kiến trúc phải có client/server, đồng bộ policy và khả năng làm việc khi mất mạng.

Vì vậy, bước đầu được giới hạn ở một prototype local có thể trình bày luồng cơ bản. Chưa triển khai cơ chế can thiệp sâu lên Windows, chặn website hay khóa máy. Cách chia nhỏ này giúp hoàn thiện đường đi dữ liệu và giao diện trước khi thêm các quyền hệ thống có rủi ro cao hơn.

## 2. Dựng cấu trúc thư mục

Project được chia theo trách nhiệm để dễ làm dần theo kế hoạch bốn tuần:

```text
CD1/
├── README.md
├── agent/
│   ├── main.py
│   └── requirements.txt
├── server/
│   ├── requirements.txt
│   ├── ogk.sqlite3          # được tạo khi server khởi động lần đầu
│   └── app/
│       ├── __init__.py
│       ├── db.py
│       ├── main.py
│       ├── models.py
│       └── schemas.py
├── dashboard/               # sẽ tách giao diện riêng ở bước sau
├── docs/
│   ├── AI_USAGE.md
│   ├── ARCHITECTURE.md
│   ├── GIAI_THICH_CAC_BUOC.md
│   ├── PRIVACY.md
│   └── THREATMODEL.md
└── tests/                   # sẽ bổ sung ở bước kiểm thử
```

Hiện trang dashboard được phục vụ trực tiếp bởi FastAPI trong `server/app/main.py`, nên thư mục `dashboard/` và `tests/` chưa có mã nguồn. Chúng được đặt trong bản đồ cấu trúc để chỉ ra vị trí dự kiến khi project lớn hơn; các thư mục này sẽ xuất hiện khi có file được thêm vào.

## 3. Tạo server và cơ sở dữ liệu

### `server/requirements.txt`

Khai báo các thư viện nền: FastAPI để viết API, Uvicorn để chạy ứng dụng, SQLAlchemy để thao tác cơ sở dữ liệu và Pydantic để kiểm tra dữ liệu đầu vào.

### `server/app/db.py`

File này tạo kết nối SQLite và `SessionLocal` để mỗi API có phiên truy cập database riêng. File database đặt tại `server/ogk.sqlite3`. SQLite phù hợp cho bản demo trên một máy; khi cần chạy nhiều người dùng thật, cần đánh giá lại cách triển khai, backup, đồng thời và bảo vệ dữ liệu.

### `server/app/models.py`

Định nghĩa hai bảng:

- `Device`: mã thiết bị, mã hồ sơ trẻ, trạng thái, version policy và thời điểm heartbeat gần nhất.
- `Event`: thời điểm, thiết bị, hồ sơ trẻ, loại sự kiện, subject, thời lượng và mã policy.

`subject` được giới hạn độ dài và được chú thích là tên tiến trình hoặc tên miền, không phải URL. Đây là cách đưa nguyên tắc tối thiểu dữ liệu vào mô hình ban đầu.

### `server/app/schemas.py`

Định nghĩa các cấu trúc request và ràng buộc dữ liệu đầu vào bằng Pydantic. Ví dụ mã ghép có đúng 8 ký tự; version không âm; duration không âm; loại sự kiện phải thuộc danh sách được đề bài cho phép. Kiểm tra đầu vào ở biên API giúp từ chối dữ liệu sai định dạng trước khi ghi xuống database. 

### `server/app/main.py`

Đây là điểm khởi chạy ứng dụng. Khi server bắt đầu, `lifespan` tạo bảng database nếu chưa có. File này chứa các route API, trang dashboard HTML đơn giản và phần kiểm tra ghép thiết bị/sự kiện.

## 4. Tạo API và luồng dữ liệu

Các endpoint hiện có:

| Endpoint | Chức năng |
|---|---|
| `GET /api/health` | Trả trạng thái server và thời điểm hiện tại. |
| `POST /api/enroll` | Nhận mã demo, `device_id`, `child_id`; tạo hoặc kiểm tra thiết bị đã ghép. |
| `POST /api/heartbeat` | Cập nhật thời điểm thiết bị liên lạc gần nhất và trả version policy hiện tại. |
| `POST /api/events` | Kiểm tra thiết bị đã ghép rồi ghi một sự kiện có schema giới hạn. |
| `GET /api/devices` | Trả danh sách thiết bị để dashboard hiển thị trạng thái. |
| `GET /api/events` | Trả các sự kiện gần đây, giới hạn số dòng tối đa. |
| `GET /docs` | Giao diện tài liệu API do FastAPI tạo. |

Luồng demo diễn ra như sau:

1. Agent gửi `POST /api/enroll` bằng mã demo và hai định danh demo.
2. Server xác nhận mã, tạo bản ghi `Device` nếu chưa có và trả về version policy.
3. Agent định kỳ gửi `POST /api/heartbeat` kèm version mà nó biết.
4. Server cập nhật `last_seen`, trả giờ server, version hiện hành và danh sách lệnh rỗng.
5. Dashboard gọi `GET /api/devices` để đọc trạng thái thiết bị.

Luồng hiện tại chứng minh các thành phần có thể trao đổi dữ liệu. Chưa có cơ chế đăng nhập phụ huynh hoặc token thiết bị, nên các API này chỉ dành cho demo local và chưa được xem là đã phân quyền an toàn.

## 5. Tạo agent mẫu

`agent/main.py` là một tiến trình Python chạy trong terminal của người dùng. Nó ghép đôi một lần, sau đó gửi heartbeat định kỳ. Thông tin server và định danh có thể thay đổi bằng biến môi trường `OGK_SERVER_URL`, `OGK_DEVICE_ID`, `OGK_CHILD_ID` và `OGK_ENROLLMENT_CODE`.

Agent in rõ khi bắt đầu, khi ghép đôi thành công, khi gửi heartbeat và khi server mất kết nối. Người dùng có thể dừng bằng `Ctrl+C`. Nó không cài Windows Service, không chạy ẩn, không đọc tiến trình đang chạy và không chặn hoạt động nào. Cách này cho phép demo luồng mạng mà chưa cần quyền Administrator.

## 6. Tạo dashboard demo

Trang `/` trong `server/app/main.py` là HTML nhỏ bằng tiếng Việt. JavaScript tải danh sách từ `/api/devices`, hiển thị mã thiết bị, trạng thái, version policy và thời điểm liên lạc gần nhất. Nếu chưa ghép thiết bị, trang báo chưa có thiết bị.

Đây chưa phải dashboard phụ huynh hoàn chỉnh: chưa có đăng nhập, biểu đồ, yêu cầu xin thêm giờ, trang riêng cho trẻ hay trang xem audit log. Các chức năng này cần gắn với mô hình quyền và phản hồi hai chiều ở những bước tiếp theo.

## 7. Viết tài liệu thiết kế

- `docs/ARCHITECTURE.md`: sơ đồ các thành phần, giao thức đang có và lộ trình kỹ thuật.
- `docs/PRIVACY.md`: bảng trường dữ liệu, mục đích, thời hạn dự kiến và người có thể xem; đồng thời nêu dữ liệu bị loại trừ.
- `docs/THREATMODEL.md`: các rủi ro STRIDE, trong đó có trẻ tự tìm cách vượt kiểm soát và nguy cơ bị lạm dụng theo dõi người lớn; kèm checklist ASVS để hoàn thiện.
- `docs/AI_USAGE.md`: ghi nhận phần AI đã hỗ trợ và trách nhiệm kiểm tra của nhóm.

Các tài liệu này là bản nháp thiết kế. Đặc biệt, các trích dẫn pháp luật phải được nhóm tự đối chiếu với văn bản gốc chính thức trước khi đưa vào báo cáo cuối.

## 8. Cài đặt và chạy prototype

Mở PowerShell trong `D:\Uni\CD1`:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r server\requirements.txt
uvicorn app.main:app --app-dir server --reload
```

Server mở tại `http://127.0.0.1:8000`. Trang dashboard ở `/`, tài liệu API ở `/docs`. Trong một terminal khác:

```powershell
pip install -r agent\requirements.txt
$env:OGK_SERVER_URL = 'http://127.0.0.1:8000'
python agent\main.py
```

Mã ghép đôi demo là `DEMO2026`. Khi agent gửi heartbeat thành công, tải lại dashboard để xem thiết bị. Tệp database SQLite được tạo tự động sau lần chạy server đầu tiên.

## 9. Những giới hạn cần hiểu rõ

- Mã ghép đôi đang được viết cố định trong mã nguồn; chưa hết hạn, dùng một lần hay được phát hành sau khi xác thực cha mẹ.
- Demo dùng HTTP và chưa có TLS, certificate pinning, access/refresh token, mã hóa trường nhạy cảm hoặc rate limiting.
- Chưa có kiểm tra quyền sở hữu theo tài khoản; các route đọc dữ liệu không yêu cầu đăng nhập.
- Chưa có migration, tác vụ xóa dữ liệu 90 ngày, xóa đồng thời cache agent, audit log thay đổi policy, hoặc chống gửi trùng bằng idempotency key.
- Heartbeat demo chạy mỗi 10 giây để dễ quan sát; mục tiêu đặc tả là 60 giây.
- Dashboard còn tối giản; chưa có UI trẻ, lời giải thích chặn, biểu đồ hay xử lý yêu cầu của trẻ.
- Chưa có quota, lịch tuần, đếm thao tác bằng monotonic clock, DNS proxy, lọc ứng dụng, SafeSearch hay cơ chế Windows service.
- Chưa có bộ kiểm thử. Không chạy agent trên máy trẻ thật hoặc máy người khác khi chưa có sự đồng ý rõ ràng.

## 10. Những gì đã được làm thêm ở tuần 2

Sau khung tuần 1, project được mở rộng để quản lý quota và lịch thời gian:

1. Bảng `Policy` lưu cấu hình theo hồ sơ trẻ cùng số phiên bản và chữ ký.
2. Schema xác nhận quota trong khoảng hợp lệ, timezone tồn tại và ma trận lịch đúng 7 ngày × 48 khoảng nửa giờ.
3. Dashboard cho phép sửa quota ngày thường/cuối tuần và bật/tắt từng khoảng.
4. Khi lưu, server tăng version policy rồi ký HMAC-SHA256 trên cả version và nội dung. Điều này giúp agent phát hiện nội dung hoặc số version bị chỉnh.
5. Agent gửi version đang dùng mỗi heartbeat 60 giây. Nếu server có version mới, agent kiểm tra chữ ký trước khi ghi cache; chữ ký sai hoặc version cũ sẽ bị bỏ qua.
6. Vòng lặp agent dùng `time.monotonic()` để đếm, bỏ qua phiên desktop đang khóa hoặc không có thao tác quá 5 phút, nhắc ở mốc 10/5/1 phút và bắt đầu ân hạn 60 giây sau khi quota cạn.
7. Cache demo nằm trong `%LOCALAPPDATA%\OGK\state.json`. Tác vụ khóa Windows là opt-in qua `OGK_ENABLE_WINDOWS_LOCK=1`; mặc định agent chỉ hiển thị trạng thái.

Hướng dẫn chạy và các giới hạn của phần tuần 2 nằm trong [WEEK2.md](WEEK2.md). Server cần `OGK_ADMIN_KEY` và `OGK_POLICY_HMAC_KEY`; agent dùng cùng giá trị HMAC để xác minh.

## 11. Những giới hạn hiện tại

- Mã ghép đôi demo vẫn cố định, không hết hạn và không xác minh sự đồng ý của phụ huynh.
- HTTP chỉ phù hợp local; chưa có TLS, certificate pinning, access/refresh token hoặc xác thực thiết bị.
- Admin key là khóa chia sẻ cho prototype, chưa có tài khoản, phân quyền, CSRF, rate limit hay audit log.
- Chưa có migrations, tác vụ xóa 90 ngày, xóa cache server/agent theo yêu cầu hoặc event idempotency.
- Agent chưa có tray UI; thông báo hiện hiển thị trong terminal.
- Cờ khóa máy mặc định tắt. Chưa có tự khởi chạy Windows service hoặc xử lý đầy đủ việc người dùng mở khóa lại.
- Cache state chưa được ACL riêng bảo vệ; khoảng ân hạn 60 giây có thể bắt đầu lại sau khi agent khởi động lại.
- Báo cáo, trang trẻ, DNS proxy và app rule hiện là prototype; chưa có tray UI, lệnh khẩn có hiệu lực, lọc DoH/SafeSearch, retention purge hoặc các phép đo M1–M6.

## Cập nhật phần đăng nhập, ghép đôi và Tray UI

Phần này thay cho các bước chạy/demo ở trên khi sử dụng phiên bản hiện tại.

1. **Phụ huynh đăng nhập:** lần chạy đầu, phụ huynh tạo tài khoản đầu tiên trên database local; mật khẩu được băm PBKDF2. Khi đăng nhập thành công, server đặt cookie phiên `HttpOnly` có thời hạn 7 ngày.
2. **Tạo mã:** phụ huynh nhập mã hồ sơ và tên trẻ trên dashboard. Server lưu hồ sơ thuộc tài khoản đó và phát mã ngẫu nhiên 8 ký tự, dùng một lần, hết hạn sau 10 phút.
3. **Ghép đôi thiết bị:** trẻ mở OGK từ khay hệ thống, nhập mã. Agent gửi mã cùng ID thiết bị tới `POST /api/enroll`; server đánh dấu mã đã dùng và trả `child_id` cùng device token. Agent lưu cấu hình ở `%LOCALAPPDATA%\OGK\agent.json`.
4. **Xác thực mỗi thiết bị:** heartbeat, event và yêu cầu của trẻ gửi header `X-OGK-Device-Token`. Server chỉ lưu SHA-256 của token và kiểm tra thiết bị thuộc đúng hồ sơ.
5. **Quản lý theo tài khoản:** dashboard dùng cookie phiên để tải thiết bị, policy, báo cáo, yêu cầu; server kiểm tra quyền sở hữu hồ sơ trước khi trả/ghi dữ liệu.
6. **Tray UI:** trẻ xem trạng thái agent, xin thêm 15 phút, báo chặn nhầm hoặc mở trang báo cáo. Trang báo cáo dùng token đọc theo thiết bị đặt trong URL fragment; trình duyệt không gửi fragment lên server.
7. **Duyệt thêm thời gian:** phụ huynh duyệt yêu cầu ở dashboard. Heartbeat trả command kèm request ID, agent chỉ cộng số phút đó một lần và thêm vào quota ngày hiện tại.

Các giới hạn bảo mật còn lại gồm thiếu HTTPS tự cấu hình, khôi phục tài khoản, rate limit toàn diện, thu hồi thiết bị và cơ chế triển khai/service Windows. Chỉ chạy local; nếu kết nối nhiều máy phải dùng HTTPS và bật `OGK_COOKIE_SECURE=1`.

## 12. Trình tự phát triển đề xuất tiếp theo

1. **Hoàn tất tuần 2:** chạy demo local và nhóm viết test/đo lường cho quota, lịch, HMAC và đồng bộ policy.
2. **Làm nền bảo mật:** tài khoản phụ huynh, băm mật khẩu, phiên an toàn, CSRF, rate limit, phân vai và kiểm tra quyền sở hữu.
3. **Làm UI minh bạch:** tray/trang của trẻ, giải thích luật, nút xin thêm giờ/báo chặn nhầm và phản hồi của cha mẹ.
4. **Củng cố bộ đếm:** bảo vệ cache, xử lý quota hết khi agent khởi động lại, ngủ/đánh thức máy và thay đổi thời gian.
5. **Thử nghiệm kiểm soát mạng/app trong lab:** chỉ dùng miền trung tính và thiết bị được phép; ghi rõ giới hạn bypass.
6. **Bổ sung kiểm thử và báo cáo:** unit/integration tests, đo hiệu năng, kiểm chứng các mục M1–M6 và hoàn thiện tài liệu nộp.

Khi chuyển sang bước kế tiếp, nên chọn một tính năng nhỏ, bổ sung mã và tiêu chí nghiệm thu riêng, rồi mới tiến đến thành phần tiếp theo.

## 13. Tiến độ tuần 3

Tuần 3 đã bổ sung bộ phân loại domain an toàn `.test`, DNS proxy loopback tùy chọn, thu thập app foreground có opt-in, báo cáo theo ngày/top app/top domain và trang minh bạch cho trẻ. Trẻ gửi được yêu cầu xin giờ/báo chặn nhầm; phụ huynh có thể ghi nhận phản hồi. Chi tiết triển khai, lệnh demo và giới hạn nằm trong [BAO_CAO_WEEK3.md](BAO_CAO_WEEK3.md).
