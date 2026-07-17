# MH Dow Sample Extension — MVP

Mục tiêu duy nhất của bản này: **dán liên kết → tải file âm thanh gốc xuống máy**.

Không Telegram, không ZIP, không chuyển WAV, không phân loại và không đổi tên theo AI. Tên sample lấy từ nguồn được giữ lại; nếu trùng tên thì thêm `(2)`, `(3)` để không ghi đè file cũ.

## Cài lần đầu

1. Bấm đúp `SETUP.cmd` và chờ báo hoàn tất.
2. Bấm đúp `START-SERVER.cmd`; giữ cửa sổ đó mở khi đang tải.
3. Bấm đúp `INSTALL-EXTENSION.cmd` để mở trang Extension và đúng thư mục cần chọn.
4. Bật **Chế độ dành cho nhà phát triển**.
5. Chọn **Tải tiện ích đã giải nén** và chọn thư mục `extension` trong dự án này.

## Sử dụng

1. Bấm biểu tượng **MH Dow Sample** trên Chrome.
2. Dán liên kết Splice hoặc liên kết audio trực tiếp.
3. Bấm **TẢI ÂM THANH**.
4. Có thể đóng popup; tác vụ vẫn tải tiếp. Mở popup lại để xem tiến trình.
5. Bấm **MỞ THƯ MỤC TẢI** khi cần.

Trên máy có ổ `J:`, file mặc định nằm tại:

```text
J:\MH-Audio-Downloads
```

Muốn đổi nơi lưu, đặt biến môi trường `MH_AUDIO_DOWNLOAD_DIR` trước khi mở server.

## Phạm vi đã khóa

- Server chỉ nghe tại `127.0.0.1:8765`, không mở ra mạng ngoài.
- Mỗi nhóm tối đa 200 đường dẫn và tải đồng thời 4 file.
- File được ghi vào `.part` trước; chỉ đổi thành file chính khi tải hoàn tất.
- Không ghi đè file đã tồn tại.
- Bản MVP ưu tiên Splice public sample pages và liên kết audio trực tiếp.

## Chạy kiểm thử

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
