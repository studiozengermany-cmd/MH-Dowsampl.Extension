# Scope giao diện — MH-Dowsample 1.3.0

Tài liệu này mô tả contract thật giữa popup và backend của bản release candidate `1.3.0`.
Mục tiêu là giữ giao diện hiện tại, chỉ bổ sung thành phần tối thiểu để người dùng
biết server đang làm gì, file được lưu ở đâu và kết quả ra sao.

## 1. Nguyên tắc

- Không thiết kế dashboard mới.
- Không tách thành app hoặc sản phẩm khác.
- Không thay đổi nhận diện hoặc bố cục tổng thể của popup.
- Dữ liệu hiển thị phải lấy từ backend thật.
- Trường nào backend chưa cung cấp thì giao diện không tự tạo số giả.

## 2. Luồng hiện tại

```text
Dán nhiều liên kết
→ POST /jobs
→ quét nguồn theo nhóm nội bộ 1.000 liên kết
→ xác minh audio
→ tải và lưu
→ phân loại nhẹ
→ GET /jobs/{job_id} hiển thị kết quả
```

Popup lưu `lastJobId` và tiếp tục polling khi mở lại. Mất kết nối local tạm thời
không hủy job; popup thử kết nối lại với thời gian chờ tăng dần.

## 3. Cài đặt nơi lưu

Popup có đúng hai điều khiển bổ sung:

- **Hỏi nơi lưu từng file**.
- **Đổi thư mục**.

### Khi công tắc tắt

- File tự động lưu vào thư mục job bên trong thư mục mặc định.
- Backend phân loại file vào `Loop`, `One-Shot`, `FX` hoặc `Chưa xác định`.
- File đáng ngờ vẫn được giữ trong `Cần kiểm tra chất lượng`.

### Khi công tắc bật

- Backend xác minh header và định dạng trước.
- Save As mở riêng cho từng file trước khi toàn bộ file được ghi xuống đĩa.
- Hủy file nào thì file đó không được lưu.
- File được stream vào file tạm cạnh đường dẫn đã chọn.
- File đích chỉ được thay sau khi stream hoàn tất.
- Nếu stream lỗi, file cũ không bị xóa hoặc ghi dở.
- Đường dẫn người dùng chọn được ưu tiên; backend không tự chuyển file sang thư mục phân loại.

### Nút Đổi thư mục

- Chỉ đổi thư mục mặc định.
- Không tự bật chế độ hỏi từng file.
- Không thay đổi file đang stream.

## 4. API đang dùng

```text
GET  /health
GET  /settings
POST /settings/download-root
POST /jobs
GET  /jobs/{job_id}
POST /open-folder
```

`POST /jobs` nhận:

```json
{
  "url": "https://example.test/source",
  "urls": [
    "https://example.test/source-1",
    "https://example.test/source-2"
  ]
}
```

Không còn giới hạn sản phẩm 200 liên kết. Backend vẫn giữ giới hạn kích thước
request và tổng asset để bảo vệ local server.

## 5. Trường trạng thái job

```text
id
status
source_total
source_processed
source_failed
source_batch_index
source_batch_total
discovered
downloaded
failed
cancelled
quality_review
classified_loop
classified_one_shot
classified_fx
classified_unknown
current
output_dir
failures
error
finished_at
```

Các trạng thái:

```text
queued → discovering → downloading → completed / failed
```

## 6. Tiến độ popup

### Đang quét

- Phần trăm dựa trên `source_processed / source_total`.
- Hiển thị URL đang quét và nhóm nguồn hiện tại.

### Đang tải

- Phần trăm dựa trên `(downloaded + failed + cancelled) / discovered`.
- Hiển thị file hiện tại.

### Mất kết nối tạm thời

- Không đổi job thành thất bại.
- Khóa nút bắt đầu job mới.
- Hiển thị `ĐANG KẾT NỐI LẠI`.
- Tự polling lại tối đa mỗi 10 giây.

## 7. Kết quả hoàn tất

Popup hiển thị:

- Đã tìm thấy.
- Đã tải.
- Lỗi tải và lỗi nguồn.
- Số file cần kiểm tra chất lượng.
- Số Loop.
- Số One-Shot.
- Số FX.
- Số Chưa xác định.
- Thư mục kết quả hoặc thư mục của file lưu gần nhất.

Tổng bốn nhóm phân loại phải bằng `downloaded`.

## 8. Phân loại hiện tại

Đây là phân loại nhẹ dựa trên tên file:

- tên có `loop` hoặc BPM → Loop;
- kick, snare, clap, hat, stab, pluck… → One-Shot;
- riser, impact, sweep, whoosh, ambience, foley… → FX;
- không đủ tín hiệu → Chưa xác định.

Giao diện không được mô tả cơ chế này như phân tích waveform hoặc AI nhận diện âm học.

## 9. Kiểm tra chất lượng hiện tại

Backend kiểm tra nhẹ:

- dung lượng tối thiểu;
- chữ ký định dạng;
- đuôi file có khớp dữ liệu;
- cấu trúc cơ bản `fmt` và `data` đối với WAV.

File không chắc chắn vẫn được giữ. `quality_review` không đồng nghĩa file hỏng.

## 10. Ngoài phạm vi 1.3.0

- Danh sách metadata chi tiết của từng sample trong popup.
- BPM/key/duration phân tích sâu.
- Waveform analysis hoặc mô hình phân loại âm học.
- Bypass đăng nhập, paywall, DRM hoặc Cloudflare.
- Chrome Web Store release.
- Installer/EXE hoàn chỉnh.

## 11. Nghiệm thu bắt buộc trước khi gọi là hoàn tất

1. Chạy `START-SERVER.cmd` trên Windows thật.
2. Reload extension `1.3.0` trên Chrome hoặc Cốc Cốc.
3. Thử một link audio trực tiếp có đuôi.
4. Thử một signed URL không có đuôi.
5. Thử một trang có nhiều sample.
6. Thử danh sách trên 1.000 liên kết hoặc bộ test tương đương.
7. Bật hỏi từng file, thử lưu, đổi tên và hủy một file.
8. Thử ghi đè một file có sẵn rồi mô phỏng lỗi mạng.
9. Xác nhận file cũ còn nguyên khi stream lỗi.
10. Kiểm tra các thư mục phân loại và `Cần kiểm tra chất lượng`.
11. Tắt/mở popup trong lúc job chạy và kiểm tra polling tiếp tục.

Chỉ sau khi các bước trên đạt mới chuyển trạng thái từ release candidate sang hoàn tất.
