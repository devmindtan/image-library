# Image Library · AI Organizer

MVP desktop: **Tauri 2 + React/Vite/Tailwind + Python/CLIP**, giao diện tiếng Việt.
File `photo_manager.html` là bản prototype cũ, được giữ nguyên.

## Chức năng

- Chọn thư mục bằng hộp thoại hệ điều hành; quét cả thư mục con, tạo thumbnail cục bộ.
- CLIP zero-shot (`openai/clip-vit-base-patch32`) phân loại thành 7 nhóm: phong cảnh, chân dung, tài liệu, động vật, phương tiện, ẩm thực, kiến trúc.
- Điểm thấp hơn ngưỡng vào `review` (Cần xem lại). Điểm softmax chỉ tương đối giữa các nhãn, **không phải xác suất dự đoán đúng**. Xem lại và chỉnh nhóm của từng ảnh trước khi sao chép.
- Xác nhận rồi sao chép vào thư mục riêng theo nhóm; giữ nguyên nguồn, không ghi đè tên trùng. Có tiến trình và hủy tác vụ.
- Ghi nhật ký JSONL trong thư mục đích. Thao tác hoàn tất vẫn được giữ nếu hủy.
- Không mở HTTP API cho backend. Rust khởi chạy Python và giao tiếp qua stdin/stdout JSON-lines; mỗi lần chỉ một worker. Đóng ứng dụng sẽ dừng worker.
- Giao diện và CSS được build cục bộ, không cần CDN. Không gửi ảnh đến dịch vụ AI.

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

Chỉ xem giao diện: `npm run dev`. Trình duyệt thông thường không chạy worker; nút minh họa dùng dữ liệu giả lập và không thực hiện phân loại AI hay sao chép.

## Sử dụng

1. Chọn thư mục ảnh nguồn, chờ quét xong.
2. Đặt ngưỡng, bấm **Phân loại ảnh**. Lần đầu có thể mất thời gian tải model; có thể hủy.
3. Xem các nhóm, đặc biệt **Cần xem lại**, đổi nhóm bằng menu trên từng ảnh nếu cần.
4. Chọn thư mục đích riêng, không chứa hoặc nằm trong thư mục nguồn.
5. Bấm **Sao chép theo nhóm**, kiểm tra số lượng và xác nhận. Kiểm tra các thông báo lỗi và nhật ký JSONL.

Tác vụ phân loại lại chỉ xử lý ảnh chưa sao chép. Đổi thư mục đích cho phép sao chép lại sang nơi mới. Nếu nguồn đã thay đổi, chọn lại thư mục nguồn để quét và phân loại lại.

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

Test UI mô phỏng IPC của Tauri và kết quả CLIP, nhưng quét/sao chép bằng worker Python thật trong thư mục tạm. Nó kiểm tra đổi nhóm, hộp thoại xác nhận và bảo toàn nguồn; không thay thế kiểm thử cửa sổ native.

Backend tests dùng model giả lập để kiểm tra ngưỡng/luồng lỗi và CLIP nhỏ với trọng số ngẫu nhiên để kiểm tra API tensor; không chứng minh chất lượng CLIP. Muốn kiểm tra inference thật, chạy ứng dụng với model tải thành công và dùng một tập ảnh đại diện.

Tauri bundle kèm mã Python, **chưa kèm Python runtime, PyTorch hay model**. Máy dùng bản build vẫn phải cài các dependency Python và đặt `IMAGE_LIBRARY_PYTHON` tới executable tuyệt đối trước khi mở ứng dụng. Đây là MVP dành cho phát triển; chưa phải bộ cài độc lập cho người dùng cuối. Kết quả build nằm trong `src-tauri/target/release/bundle/`.

## Bảo toàn file và giới hạn

- Không có thao tác di chuyển/xóa ảnh gốc. Chặn đường dẫn ra ngoài nguồn và bỏ qua symlink trong nguồn; không cho nhóm đích là symlink.
- Kiểm tra kích thước/mtime nguồn khi phân loại và sao chép. Đây không phải cơ chế chống tiến trình khác cố tình thay đổi file/thư mục đồng thời; tránh sửa thư mục khi đang chạy.
- Ghi vào file tạm cùng thư mục, flush/fsync, rồi công bố bằng hard link nguyên tử không ghi đè. **Thư mục đích phải nằm trên filesystem hỗ trợ hard link** (ví dụ NTFS, APFS, ext4; FAT/exFAT không phù hợp). Nếu không hỗ trợ, báo lỗi và giữ nguyên nguồn.
- Hủy/đóng đột ngột có thể để lại `.image-library-*.partial`. Nhật ký `pending`/`publishing` giúp tìm file tạm và kết quả dở dang. Kiểm tra nhật ký trước khi xóa file tạm. Chưa có UI hoàn tác tự động.
- Không có cơ sở dữ liệu phiên; đóng ứng dụng sẽ mất kết quả phân loại trong bộ nhớ. Các bản sao và nhật ký vẫn tồn tại. Quét lại để làm việc tiếp.
- MVP xử lý CPU từng ảnh; chưa tối ưu thư viện rất lớn. Hỗ trợ JPG/JPEG/PNG/WEBP/BMP/TIFF, chưa hỗ trợ RAW/HEIC hoặc nhận diện khuôn mặt.

## Kết quả kiểm chứng trong cloud

- `npm run build`: đạt (TypeScript + Vite).
- 14 kiểm thử backend: đạt, gồm CLIP nhỏ với trọng số ngẫu nhiên; không tải model pretrained.
- `tests/ui_smoke.py`: đạt; file ảnh thật được quét và sao chép đúng nhóm, nguồn giữ nguyên. IPC và kết quả phân loại được mô phỏng.
- `cargo check --locked` và `cargo build --locked`: đạt trên Linux bằng thư viện hệ thống được giải nén cục bộ trong workspace.
- Chưa tương tác được cửa sổ Tauri: cloud không có desktop display; chạy binary dừng khi khởi tạo GTK.
- Chưa chạy inference với trọng số CLIP pretrained: truy cập `huggingface.co` bị proxy từ chối. Đã lưu domain cần thiết trong bản nháp cấu hình môi trường; cần áp dụng cấu hình và tải model trước khi kiểm chứng phần này.
