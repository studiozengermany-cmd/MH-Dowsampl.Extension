# Scope giao diện — Nơi lưu và kết quả tải sample

Tài liệu này là nguồn thống nhất để phần giao diện và backend làm song song.
Người thiết kế có thể dựng đầy đủ trạng thái giao diện theo contract bên dưới;
không cần chờ thuật toán phân tích âm thanh hoàn tất.

## 1. Mục tiêu người dùng

Người dùng phải làm được ba việc rõ ràng:

1. Chọn nơi lưu sample như cơ chế tải xuống của Chrome/Cốc Cốc.
2. Nhìn thấy một lượt quét/tải đang làm tới đâu và có bao nhiêu lỗi.
3. Sau khi tải xong, biết từng file thuộc Loop, One-Shot, FX hay Chưa xác định.

## 2. Trạng thái backend hiện có trên `main`

Các phần này đã có và giao diện có thể nối thật ngay:

- `GET /health`: trạng thái server và nơi lưu hiện tại.
- `GET /settings`: nơi lưu, đã cấu hình hay chưa, và `ask_each_time`.
- `POST /settings/download-root`: chọn/đổi/xóa nơi lưu mặc định hoặc bật/tắt hỏi mỗi lượt.
- `POST /jobs`: bắt đầu lượt tải; có thể gửi nơi lưu riêng cho lượt hiện tại.
- `GET /jobs/{job_id}`: trạng thái, số tìm thấy, tải thành công và tải lỗi.
- `POST /open-folder`: mở thư mục kết quả của một lượt tải.

Các trường tiến độ đã có:

```text
status
discovered
downloaded
failed
current
failures
output_dir
error
```

## 3. Scope màn hình cài đặt nơi lưu

### Thành phần bắt buộc

- Dòng `Vị trí` hiển thị đường dẫn đang dùng.
- Nút `Thay đổi` để mở trình chọn thư mục native của Windows.
- Công tắc `Hỏi vị trí lưu trước mỗi lượt tải`.
- Trạng thái rõ khi người dùng chưa chọn thư mục.

### Hành vi

- Máy mới bắt buộc chọn thư mục trước khi server chạy.
- Hủy ở lần cài/mở đầu thì dừng, không tự chọn `J:` hoặc `Downloads`.
- Khi tải, lựa chọn riêng của lượt hiện tại luôn ưu tiên hơn mặc định.
- Chỉ ghi nhớ khi người dùng chọn lưu làm mặc định.
- Đổi mặc định chỉ ảnh hưởng các lượt tải mới.

## 4. Scope màn hình tiến độ tải

Hiển thị bốn số chính ở vị trí dễ đọc:

- `Đã tìm thấy`: `discovered`.
- `Đã tải`: `downloaded`.
- `Lỗi tải`: `failed`.
- `Lỗi âm thanh`: trường backend dự kiến `audio_errors`.

Hiển thị trạng thái theo thứ tự:

```text
Đang chờ → Đang quét → Đang tải → Đang phân tích → Hoàn tất / Thất bại
```

Trong lúc backend phân tích chưa có, giao diện phải coi `audio_errors` và trạng
thái `Đang phân tích` là dữ liệu tùy chọn; không được tự tạo số giả.

## 5. Scope kết quả phân loại

### Bốn nhóm hiển thị

- Loop
- One-Shot
- FX
- Chưa xác định

Mỗi nhóm hiển thị số lượng. Tổng bốn nhóm sau khi hoàn tất phải bằng số file đã
được phân tích.

### Danh sách từng sample

Mỗi dòng gồm:

- tên file;
- nhóm phân loại;
- trạng thái `Đạt`, `Có vấn đề`, `Không phân tích được`;
- thời lượng;
- BPM nếu có;
- key nếu có;
- mô tả lỗi ngắn nếu có;
- nút mở vị trí file.

Cần có bộ lọc theo nhóm và theo trạng thái lỗi. Không đổ toàn bộ metadata kỹ
thuật lên giao diện chính; chi tiết có thể nằm trong phần mở rộng của từng dòng.

## 6. Contract backend dự kiến cho phần phân tích

Phần này **chưa có trên `main`**. Giao diện được phép dựng trước nhưng phải xử lý
trường hợp các trường chưa xuất hiện.

`GET /jobs/{job_id}` sẽ bổ sung:

```json
{
  "analyzed": 120,
  "loops": 40,
  "one_shots": 60,
  "fx": 15,
  "unknown": 5,
  "audio_errors": 3,
  "analysis_failed": 2,
  "rejected": 1,
  "sample_results_total": 120
}
```

API chi tiết dự kiến:

```text
GET /jobs/{job_id}/samples?offset=0&limit=100
```

Một item dự kiến:

```json
{
  "file": "Kick.wav",
  "status": "passed",
  "content_type": "one-shot",
  "category": "One-Shots",
  "output": "G:\\Samples\\job\\One-Shots\\Kick.wav",
  "analysis": {
    "duration_sec": 0.42,
    "bpm": 0,
    "key": "Unknown",
    "issues": []
  }
}
```

## 7. Cấu trúc thư mục đầu ra dự kiến

```text
<thư mục của lượt tải>/
├─ Loops/
├─ One-Shots/
├─ FX/
├─ Unsorted/
└─ sample-report.json
```

Không hiển thị như thể cấu trúc này đã tồn tại trước khi backend xác nhận.

## 8. Trạng thái giao diện bắt buộc

- Server chưa chạy.
- Chưa chọn nơi lưu.
- Đang mở hộp chọn thư mục.
- Người dùng hủy chọn.
- Chưa tìm thấy sample.
- Đang quét.
- Đang tải.
- Đang phân tích.
- Hoàn tất có kết quả.
- Hoàn tất nhưng có lỗi một phần.
- Thất bại toàn bộ.

## 9. Ngoài scope của người thiết kế giao diện

- Thuật toán nhận diện Loop/One-Shot/FX.
- Giải mã hoặc kiểm tra chất lượng file âm thanh.
- Di chuyển file vào thư mục phân loại.
- Cơ chế PyInstaller/EXE/installer.
- Tự đặt số liệu giả để che backend chưa có.

## 10. Tiêu chí nghiệm thu giao diện

- Người dùng luôn biết file sẽ được lưu ở đâu trước khi tải.
- Bốn số quét/tải/lỗi tải/lỗi âm thanh không bị nhập nhằng.
- Người dùng thấy rõ số Loop/One-Shot/FX/Chưa xác định.
- Mỗi sample có trạng thái và lỗi riêng.
- Loading, empty, partial error và fatal error là các trạng thái thật.
- Giao diện vẫn chạy được với backend `main` hiện tại và tự hiện thêm phần phân
  loại khi contract mới có dữ liệu.
