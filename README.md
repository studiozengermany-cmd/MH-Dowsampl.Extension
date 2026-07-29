<div align="center">

# MH-Dowsample Extension

### Thu thập và tải sample công khai về máy theo quy trình local-first

![Version](https://img.shields.io/badge/Version-1.3.0-2563EB)
![Status](https://img.shields.io/badge/Status-Release%20Candidate-F59E0B)
![Platform](https://img.shields.io/badge/Platform-Windows-6B7280)
![Backend](https://img.shields.io/badge/Backend-Local%20Python-16A34A)

[Website](https://studiominhhieu.com/) · [GitHub](https://github.com/studiozengermany-cmd) · [Liên hệ](mailto:support@studiominhhieu.com)

</div>

> [!IMPORTANT]
> Bản `1.3.0` đã có cơ chế tải, kiểm tra định dạng, xử lý danh sách lớn, hỏi nơi lưu từng file và phân loại đầu ra. Bản này vẫn là **release candidate** cho tới khi được nghiệm thu trực tiếp trên Windows với Chrome/Cốc Cốc và các liên kết thật của người dùng.

## Khả năng hiện tại

| Nhóm | Cơ chế |
|---|---|
| Nguồn nhập | Nhận nhiều liên kết HTTP/HTTPS, loại bỏ liên kết trùng |
| Danh sách lớn | Chia nguồn thành nhóm nội bộ 1.000 liên kết để xử lý |
| Tìm audio | Hỗ trợ trang Splice công khai, thẻ audio, metadata, JSON audio và link audio trực tiếp |
| Signed URL | Nhận URL audio không có đuôi khi có tín hiệu audio đủ mạnh |
| Xác minh file | Kiểm tra MIME, URL, tên phản hồi và chữ ký nhị phân của file |
| Chặn file giả | Từ chối HTML, JSON, XML và MP4 có luồng video giả dạng audio |
| Định dạng | WAV, FLAC, AIFF, MP3, M4A, AAC, OGG và Opus |
| Tải an toàn | Ghi vào file tạm `.part`, chỉ thay file đích sau khi tải hoàn chỉnh |
| Fallback | Thử nguồn audio dự phòng khi nguồn chính lỗi trước bước lưu |
| Nơi lưu | Dùng thư mục mặc định hoặc mở Save As riêng cho từng file |
| Phân loại nhẹ | Loop, One-Shot, FX và Chưa xác định dựa trên tên file |
| Kiểm tra nhẹ | Giữ file đáng ngờ trong `Cần kiểm tra chất lượng`, không tự xóa |
| Theo dõi | Popup hiển thị quét, tải, lỗi, phân loại và tự kết nối lại khi mất mạng local tạm thời |

## Cách hoạt động

```text
Dán liên kết
    ↓
Chrome/Cốc Cốc Extension
    ↓
Local API 127.0.0.1:8765
    ↓
Crawler tìm đường dẫn audio công khai
    ↓
Xác minh định dạng bằng header và chữ ký file
    ↓
Tải an toàn → phân loại → lưu trên máy
```

Backend chỉ lắng nghe trên loopback `127.0.0.1`. Extension không mở server ra Internet hoặc mạng LAN.

## Cài đặt trên Windows

### Yêu cầu

- Windows 10 hoặc Windows 11.
- Python 3.12 trở lên.
- Chrome, Cốc Cốc hoặc trình duyệt Chromium hỗ trợ Manifest V3.

### Thiết lập

1. Clone hoặc tải repository này về máy.
2. Chạy `SETUP.cmd` nếu cần chuẩn bị môi trường Python.
3. Chạy `START-SERVER.cmd`.
4. Mở `chrome://extensions/`.
5. Bật **Chế độ dành cho nhà phát triển**.
6. Chọn **Tải tiện ích đã giải nén**.
7. Chọn thư mục `extension` trong repository.

Sau khi cập nhật source, khởi động lại server và bấm **Tải lại** trên thẻ extension.

## Sử dụng

1. Mở `START-SERVER.cmd` và giữ cửa sổ server hoạt động.
2. Mở popup MH-Dowsample.
3. Chọn **Đổi thư mục** để đặt thư mục lưu mặc định khi cần.
4. Bật hoặc tắt **Hỏi nơi lưu từng file**.
5. Dán một hoặc nhiều liên kết, mỗi liên kết một dòng.
6. Bấm **Quét và tải âm thanh**.
7. Theo dõi tiến độ và kết quả trong popup.
8. Sau khi hoàn tất, mở thư mục kết quả từ popup.

Có thể đóng popup trong lúc backend làm việc. Khi mở lại, extension tiếp tục theo dõi job gần nhất. Nếu kết nối local gián đoạn tạm thời, popup tự thử kết nối lại thay vì hủy job.

## Hai chế độ lưu

### Dùng thư mục mặc định

Khi công tắc **Hỏi nơi lưu từng file** tắt:

- server tạo một thư mục riêng cho job;
- tải song song tối đa bốn file;
- file được xếp vào các thư mục phân loại;
- file đáng ngờ vẫn được giữ để kiểm tra thủ công.

Cấu trúc kết quả:

```text
<thư mục job>/
├─ Loop/
├─ One-Shot/
├─ FX/
├─ Chưa xác định/
└─ Cần kiểm tra chất lượng/
   ├─ Loop/
   ├─ One-Shot/
   ├─ FX/
   └─ Chưa xác định/
```

### Hỏi nơi lưu từng file

Khi công tắc bật:

- crawler đọc phần đầu để xác minh loại audio;
- Save As mở trước khi toàn bộ file được ghi xuống đĩa;
- hủy file nào thì file đó bị bỏ qua;
- dữ liệu còn lại được stream vào file tạm cạnh vị trí đã chọn;
- file đích chỉ được thay sau khi stream hoàn tất;
- nếu stream lỗi, file cũ tại vị trí đích vẫn được giữ.

Ở chế độ này, đường dẫn do người dùng chọn được ưu tiên nên hệ thống không tự di chuyển file sang thư mục phân loại.

## Phân loại và chất lượng

Phân loại hiện tại là bộ luật nhẹ dựa trên tên file:

- `loop`, thông tin BPM → **Loop**;
- kick, snare, clap, hat, stab, pluck… → **One-Shot**;
- riser, impact, sweep, whoosh, ambience, foley… → **FX**;
- không đủ tín hiệu → **Chưa xác định**.

Kiểm tra chất lượng hiện tại xác minh dung lượng tối thiểu, chữ ký định dạng và cấu trúc cơ bản của WAV. Đây không phải phân tích âm học sâu, không tự kết luận sample hay/dở và không xóa file chỉ vì chưa chắc chắn.

## An toàn

- Chỉ nhận URL `http://` hoặc `https://`.
- Local API chỉ chấp nhận yêu cầu từ localhost và extension hợp lệ.
- Không vượt đăng nhập, paywall, DRM hoặc cơ chế bảo vệ nguồn.
- Không yêu cầu cloud, API key hoặc tài khoản trung gian cho workflow cốt lõi.
- File đang tải dùng tên tạm duy nhất và được dọn khi thất bại.
- Tên file được làm sạch để tương thích Windows.
- Người dùng chịu trách nhiệm kiểm tra quyền sử dụng và license của audio.

## Giới hạn đã biết

- Không bảo đảm tìm được audio trên mọi website.
- Trang chỉ dựng player bằng JavaScript hoặc API riêng có thể cần adapter nguồn riêng.
- Không tải nội dung cần đăng nhập hoặc nội dung bị bảo vệ.
- Phân loại hiện tại dựa trên tên file, chưa phải mô hình phân tích waveform.
- Kiểm tra chất lượng là lớp lọc nhẹ, không thay thế nghe thử hoặc công cụ kiểm định chuyên sâu.
- Chưa phát hành trên Chrome Web Store.
- Chưa có security audit độc lập.
- Chưa nghiệm thu vật lý trên máy Windows của người dùng cho bản `1.3.0`.

## Kiểm thử

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check extension\popup-core.js
node --check extension\popup-settings.js
```

Bộ test bao phủ crawler, signed URL, nhận dạng định dạng, chặn video/HTML, danh sách 1.501 nguồn, phân loại, lưu file đáng ngờ, Save As từng file, hủy lưu và bảo vệ file cũ khi stream lỗi.

Test tự động không thay thế nghiệm thu bằng Chrome/Cốc Cốc và nguồn thật trên Windows.

## Cấu trúc chính

```text
MH-Dowsampl.Extension/
├─ extension/
│  ├─ manifest.json
│  ├─ popup.html
│  ├─ popup.css
│  ├─ popup-settings.css
│  ├─ popup-core.js
│  └─ popup-settings.js
├─ backend/
│  ├─ core_engine.py
│  ├─ crawler.py
│  ├─ server_engine.py
│  ├─ server_hardening.py
│  ├─ server_streaming.py
│  └─ server.py
├─ tests/
├─ SETUP.cmd
├─ START-SERVER.cmd
└─ INSTALL-EXTENSION.cmd
```

## Nguyên tắc phát triển

1. Ưu tiên sửa cơ chế tải bên trong trước giao diện.
2. Không thiết kế dashboard hoặc tách thành sản phẩm khác.
3. Chỉ bổ sung UI tối thiểu để phản ánh trạng thái thật từ backend.
4. Không dùng số liệu giả để che chức năng chưa có.
5. Không tuyên bố hoàn tất khi chưa có nghiệm thu thực tế.
6. Không xóa file âm thanh chỉ vì chất lượng chưa chắc chắn.

## Liên hệ

- Website: https://studiominhhieu.com/
- Email: support@studiominhhieu.com
- GitHub: https://github.com/studiozengermany-cmd

---

<div align="center">

**Ý tưởng, mục tiêu và quyết định sản phẩm: Minh Hiếu**  
**Thu thập có kiểm soát → tổ chức an toàn → sử dụng hiệu quả.**

</div>
