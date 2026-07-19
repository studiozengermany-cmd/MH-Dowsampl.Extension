# Chính sách quyền riêng tư — MH-Dowsample Extension

Cập nhật: 17/07/2026

MH-Dowsample Extension không yêu cầu tài khoản riêng và không gửi lịch sử sử dụng tới analytics hoặc quảng cáo. Tiện ích có thể dùng backend local hoặc backend Render riêng do chủ sở hữu cấu hình.

## Dữ liệu được xử lý

- Liên kết do người dùng chủ động dán vào tiện ích.
- Mã tác vụ gần nhất và liên kết gần nhất, được lưu trong `chrome.storage.local` trên thiết bị của người dùng.
- File âm thanh công khai được nguồn bên ngoài trả về theo liên kết người dùng cung cấp.

## Cách dữ liệu được sử dụng

Ở chế độ local, liên kết được gửi tới server tại `127.0.0.1:8765`. Ở chế độ Render, Extension đọc URL file gốc tạm thời ngay trong phiên trang nguồn rồi chỉ gửi URL file và metadata cần thiết lên server; Extension không gửi mật khẩu hoặc cookie đăng nhập. File tạm trên Render được chuyển về bằng trình tải xuống của Chrome/Cốc Cốc.

Studio Minh Hiếu không thu thập, bán hoặc chia sẻ dữ liệu này với bên thứ ba. Khi local server truy cập một website hoặc CDN do người dùng chọn, nhà cung cấp nguồn đó có thể nhận thông tin kết nối thông thường như địa chỉ IP, thời gian truy cập và chuỗi User-Agent theo chính sách riêng của họ.

## Quyền của tiện ích

- `storage`: lưu tác vụ và liên kết gần nhất trên thiết bị để tiếp tục hiển thị khi popup được mở lại.
- `http://127.0.0.1:8765/*`: giao tiếp với local server đi kèm sản phẩm.
- `https://*.onrender.com/*`: giao tiếp với backend Render do chủ sở hữu cấu hình.
- `https://splice.com/*` và `https://*.splice.com/*`: đọc metadata/file gốc bằng phiên trình duyệt hiện tại mà không xuất cookie.
- `downloads`: giao từng file hoàn thành cho Chrome/Cốc Cốc lưu về máy.

## Lưu trữ và xóa dữ liệu

Tiện ích chỉ giữ cấu hình kết nối, tùy chọn tải, mã tác vụ và liên kết gần nhất trong bộ nhớ local của Chrome. Người dùng có thể xóa dữ liệu này bằng cách xóa dữ liệu tiện ích hoặc gỡ tiện ích. Job trên Render dùng thư mục tạm, bị giới hạn số lượng và tự xóa theo thời hạn cấu hình; khi Render khởi động lại, job trong bộ nhớ sẽ mất.

Các file đã tải thuộc quyền kiểm soát của người dùng và có thể được xóa trực tiếp trong hệ thống file.

## Liên hệ

Email: support@studiominhhieu.com
