# Image Library · AI Organizer

MVP desktop: **Tauri 2 + React/Vite/Tailwind + Python/CLIP**, giao diện tiếng Việt.
File `photo_manager.html` là bản prototype cũ, được giữ nguyên.

## Chức năng

- Mở **thư mục hiện có** để hiển thị ảnh/video trực tiếp, kể cả thư mục con. Quét chỉ đọc, không yêu cầu sao chép.
- 4 chế độ xem: **Lưới, Gọn, Danh sách, Thư mục**. Có tìm kiếm theo đường dẫn, lọc ảnh/video/nhóm, sắp xếp tên, dung lượng và thời gian. Chế độ xem được nhớ trên máy.
- Dung lượng từng file, tổng thư viện, tổng ảnh/video, tổng kết quả đang lọc và tổng các file đã chọn. Dùng kích thước file logic, đơn vị KB/MB/GB tính theo 1024; không phải dung lượng block thực tế trên đĩa.
- Video được nhận diện theo đuôi file và gán nhóm `videos`; **không giải mã hoặc phân tích nội dung video, không gọi CLIP**. Hiển thị biểu tượng video thay cho thumbnail.
- Ảnh có thumbnail cục bộ. Ảnh không tạo được thumbnail vẫn xuất hiện và được tính dung lượng, kèm thông báo lỗi.
- Mặc định phân nhóm nội dung ảnh bằng **AI**, video luôn vào nhóm Video. Nút **Phân loại ảnh bằng AI** luôn hiển thị; bấm nút cũng chọn lại cách phân nhóm theo nội dung. Tùy chọn **Ảnh / Video** không cần AI hoặc tải mô hình. Phân nhóm nội dung ảnh dùng CLIP zero-shot (`openai/clip-vit-base-patch32`): phong cảnh, chân dung, tài liệu, động vật, phương tiện, ẩm thực, kiến trúc.
- Điểm thấp hơn ngưỡng vào `review` (Cần xem lại). Điểm softmax tương đối giữa nhãn, **không phải xác suất dự đoán đúng**. Có thể chọn nhóm ảnh thủ công mà không chạy AI.
- Hai thao tác: **Sắp xếp tại thư mục đang mở** (di chuyển vào thư mục con), hoặc **Sao chép sang thư mục khác** (giữ nguyên nguồn).
- Luôn tạo bản xem trước đường dẫn trước/sau, số lượng và dung lượng; chỉ thay đổi file sau khi xác nhận. Áp dụng cho các file đã chọn; nếu chưa chọn thì áp dụng cho toàn bộ kết quả đang lọc.
- Không ghi đè khi trùng tên. Kiểm tra lại kế hoạch khi xác nhận, từ chối nếu file hoặc tên đích đã thay đổi. Bỏ qua file đã đúng vị trí để tránh sắp xếp lặp.
- Tự quét lại và cập nhật cây thư mục sau khi di chuyển, cả khi tác vụ bị hủy. Ghi nhật ký JSONL cho từng file.
- Giao tiếp Tauri/Python qua stdin/stdout JSON-lines, không mở HTTP API. Giao diện/CSS được build cục bộ, không cần CDN, không gửi ảnh/video đến dịch vụ AI.

## Chuẩn bị máy desktop

Node.js 22+, npm, Python **3.10–3.12** và Rust stable. Python 3.12 là phiên bản kiểm thử backend.
Cài [prerequisites của Tauri](https://v2.tauri.app/start/prerequisites/) cho hệ điều hành:

- **Windows:** Visual Studio Build Tools (Desktop development with C++), WebView2, Rust MSVC.
- **macOS:** Xcode Command Line Tools.
- **Debian/Ubuntu:** `sudo apt-get install build-essential pkg-config libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev patchelf`.

Tại thư mục gốc repository:

```sh
npm ci
python3 -m venv .venv
# CPU-only trên Linux/Windows; trên macOS cài torch theo hướng dẫn pytorch.org.
.venv/bin/python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv/bin/python -m pip install -r backend/requirements.txt
export IMAGE_LIBRARY_PYTHON="$PWD/.venv/bin/python"
npm run tauri dev
```

Trên Windows PowerShell:

```powershell
npm ci
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
$env:IMAGE_LIBRARY_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
npm run tauri dev
```

Lần phân loại đầu cần tải CLIP (~600 MB) từ Hugging Face. Cho phép `huggingface.co` và các host tải file mà Hugging Face chuyển hướng tới (thường `cas-bridge.xethub.hf.co`, `cas-server.xethub.hf.co`, `transfer.xethub.hf.co`). Không cần token cho model công khai. Có thể đặt `HF_HOME` tới thư mục cache được phép ghi. Sau khi tải đủ model, đặt `HF_HUB_OFFLINE=1` để dùng offline. Không tắt TLS khi gặp lỗi mạng.

Chỉ xem giao diện: `npm run dev`. Trình duyệt thông thường không chạy worker; nút minh họa dùng dữ liệu giả lập và không thực hiện phân loại AI, di chuyển hoặc sao chép.

## Sử dụng

1. Bấm **Mở thư mục**. File có sẵn được hiển thị ngay khi quét, chưa bị thay đổi.
2. Chuyển giữa các chế độ xem, mở thư mục con, lọc hoặc chọn riêng những file muốn sắp xếp.
3. Giữ phân nhóm mặc định **Theo nội dung ảnh + Video**, bấm **Phân loại ảnh bằng AI**, xem kết quả và chỉnh nhóm nếu cần. Video luôn được xếp riêng, không chạy AI.
4. Chọn thao tác **Sắp xếp tại thư mục đang mở**, bấm **Xem trước sắp xếp**. Ví dụ `kỳ nghỉ/biển.jpg → landscape/biển.jpg`, `clip.mp4 → videos/clip.mp4` trong thư mục đang mở. File trùng tên được thêm số.
5. Bấm **Xác nhận di chuyển** khi đúng ý. File đổi vị trí, không tạo thêm bản sao. App tự quét lại để hiển thị vị trí mới. Thư mục cũ rỗng được giữ nguyên.

Nếu chỉ cần tách ảnh/video, chọn **Theo loại: Ảnh / Video** để dùng ngay không tải model. Luồng mặc định vẫn giữ phân loại tự động nội dung ảnh: bấm **Phân loại ảnh bằng AI**, xem kết quả rồi tạo bản xem trước sắp xếp. Nút AI luôn hiển thị kể cả khi chọn phân nhóm chỉ theo loại; bấm nút sẽ quay lại phân nhóm nội dung. Có thể chọn nhóm từng ảnh thủ công. Video luôn vào `videos`. Phân loại chỉ cập nhật nhóm trong bộ nhớ, không tự di chuyển file.

Nếu muốn giữ nguyên nguồn: chọn **Sao chép sang thư mục khác**, chọn thư mục đích riêng, xem trước rồi xác nhận. App giữ đánh dấu file đã sao chép trong phiên hiện tại để không sao chép lại ngoài ý muốn; đổi đích hoặc quét lại để tạo bản sao mới.

Các file đang được chọn có thể nằm ngoài bộ lọc vừa đổi; số lượng/dung lượng đã chọn và toàn bộ đường dẫn sẽ xuất hiện trong bản xem trước. Bấm **Bỏ chọn** để quay về phạm vi bộ lọc. Quét lại làm mất lựa chọn và nhóm tạm trong bộ nhớ; các nhóm được khôi phục từ thư mục `images`, `videos`, `review` hoặc thư mục tên nhãn CLIP.

## Kiểm tra và build

```sh
npm run build
.venv/bin/python -m unittest discover -s backend/tests -v
cd src-tauri
cargo check --locked
cd ..
npm run tauri build
```

Kiểm thử UI qua trình duyệt (một terminal khác đang chạy `npm run dev`):

```sh
.venv/bin/python -m pip install -r backend/requirements-test.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python tests/ui_smoke.py
```

Test UI mô phỏng IPC của Tauri và kết quả CLIP, nhưng quét, xem trước, sao chép và di chuyển bằng worker Python thật trong thư mục tạm. Nó kiểm tra ảnh/video, dung lượng, 4 chế độ xem, responsive 360–1600 px, đổi nhóm, hủy xác nhận, sao chép giữ nguồn và sắp xếp tại chỗ; không thay thế kiểm thử cửa sổ native.

Backend tests dùng model giả lập để kiểm tra ngưỡng/luồng lỗi và CLIP nhỏ với trọng số ngẫu nhiên để kiểm tra API tensor; không chứng minh chất lượng CLIP. Muốn kiểm tra inference thật, chạy ứng dụng với model tải thành công và dùng một tập ảnh đại diện.

Tauri bundle kèm mã Python, **chưa kèm Python runtime, PyTorch hay model**. Máy dùng bản build vẫn phải cài các dependency Python và đặt `IMAGE_LIBRARY_PYTHON` tới executable tuyệt đối trước khi mở ứng dụng. Đây là MVP dành cho phát triển; chưa phải bộ cài độc lập cho người dùng cuối. Kết quả build nằm trong `src-tauri/target/release/bundle/`.

## Bảo toàn file và giới hạn

- Mở/quét/phân loại/xem trước không thay đổi file. **Chế độ tại chỗ có di chuyển nguồn**, chỉ sau xác nhận. Chế độ sao chép giữ nguyên nguồn. Chưa có UI hoàn tác tự động.
- Chặn đường dẫn thoát khỏi nguồn, bỏ qua symlink trong nguồn, từ chối thư mục nhóm đích là symlink. Kiểm tra kích thước/mtime nguồn và kế hoạch trước khi thực thi. Tránh dùng tiến trình khác sửa cùng thư mục khi đang chạy; đây không phải cơ chế chống thay đổi ác ý đồng thời.
- Di chuyển tại chỗ dùng hard link không ghi đè rồi mới bỏ đường dẫn cũ; file không bị mất bản duy nhất nếu bước tạo link thất bại. Chỉ hoạt động cùng filesystem hỗ trợ hard link (NTFS/APFS/ext4; FAT/exFAT hoặc thư mục con gắn filesystem khác sẽ báo lỗi và giữ nguồn).
- Sao chép ghi file tạm, flush/fsync rồi công bố bằng hard link không ghi đè. Đích cũng phải hỗ trợ hard link.
- Hủy/đóng đột ngột có thể giữ các kết quả đã hoàn tất, để lại file tạm `.image-library-*.partial` hoặc hai đường dẫn đến cùng file nếu bị ngắt giữa bước tạo link và bỏ đường dẫn cũ. Kiểm tra nhật ký `pending`/`linked`/`moved`/`copied` và quét lại trước khi xử lý tiếp. Không tự xóa thư mục rỗng.
- Không có cơ sở dữ liệu phiên; nhóm chưa áp dụng trên đĩa không tồn tại qua lần khởi động/quét mới. Các vị trí file và nhật ký vẫn tồn tại, nhóm được đọc lại từ tên thư mục chuẩn.
- Tổng chỉ bao gồm định dạng media hỗ trợ, không tính nhật ký và các file khác. Video nhận diện theo phần mở rộng, chưa có playback/thumbnail hoặc xác minh codec. Hỗ trợ JPG/JPEG/PNG/WEBP/BMP/TIFF; MP4/MOV/MKV/AVI/WEBM/M4V/MPEG/MPG/MTS/M2TS/3GP. Chưa hỗ trợ RAW/HEIC.
- MVP xử lý CPU từng ảnh, lưu thumbnail trong bộ nhớ; chưa tối ưu cho thư viện rất lớn.

## Kết quả kiểm chứng trong cloud

- `npm run build`: đạt (TypeScript + Vite).
- 26 kiểm thử backend: đạt, gồm CLIP nhỏ với trọng số ngẫu nhiên; không tải model pretrained.
- `tests/ui_smoke.py`: đạt; file ảnh/video được quét, xem trước, sao chép giữ nguồn và di chuyển tại chỗ đúng nhóm; app tự cập nhật sau di chuyển. Đã kiểm tra 4 chế độ xem ở chiều rộng 360–1600 px. IPC và kết quả phân loại được mô phỏng.
- `cargo check --locked` và `cargo build --locked`: đạt trên Linux bằng thư viện hệ thống được giải nén cục bộ trong workspace.
- Chưa tương tác được cửa sổ Tauri: cloud không có desktop display; chạy binary dừng khi khởi tạo GTK.
- Chưa chạy inference với trọng số CLIP pretrained: truy cập `huggingface.co` bị proxy từ chối. Đã lưu domain cần thiết trong bản nháp cấu hình môi trường; cần áp dụng cấu hình và tải model trước khi kiểm chứng phần này.
