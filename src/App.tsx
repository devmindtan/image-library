import { useRef, useState } from "react";
import { Channel, invoke, isTauri } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";

type Photo = {
  path: string;
  stamp: string;
  thumbnail: string;
  category?: string;
  score?: number;
  error?: string;
  destination?: string;
};
type Event = {
  type: string;
  photo?: Photo;
  path?: string;
  category?: string;
  score?: number;
  message?: string;
  current?: number;
  total?: number;
  count?: number;
  destination?: string;
  journal?: string;
};
const groups: Record<string, string> = {
  all: "Tất cả ảnh",
  landscape: "Phong cảnh",
  portrait: "Chân dung",
  document: "Tài liệu",
  animals: "Động vật",
  vehicles: "Phương tiện",
  food: "Ẩm thực",
  architecture: "Kiến trúc",
  review: "Cần xem lại",
};
const symbols: Record<string, string> = {
  all: "▦",
  landscape: "◒",
  portrait: "♙",
  document: "▤",
  animals: "♧",
  vehicles: "▱",
  food: "◉",
  architecture: "⌂",
  review: "◇",
};
const desktop = isTauri();

export default function App() {
  const [source, setSource] = useState("");
  const [destination, setDestination] = useState("");
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [group, setGroup] = useState("all");
  const [query, setQuery] = useState("");
  const [threshold, setThreshold] = useState(0.55);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [message, setMessage] = useState("Chọn thư mục để bắt đầu");
  const [progress, setProgress] = useState(0);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [confirm, setConfirm] = useState(false);
  const [demo, setDemo] = useState(false);
  const active = useRef<string | null>(null);
  const classified = photos.filter((p) => p.category && !p.error);
  const copied = photos.filter((p) => p.destination).length;
  const visible = photos.filter(
    (p) =>
      (group === "all" || p.category === group) &&
      p.path.toLocaleLowerCase().includes(query.toLocaleLowerCase()),
  );
  const notify = (text: string) => setWarnings((w) => [...w, text]);
  async function run(operation: string, root = source) {
    if (!desktop) {
      setMessage("Mở bản desktop để truy cập thư mục trên máy.");
      return;
    }
    const jobId = crypto.randomUUID();
    active.current = jobId;
    setBusy(true);
    setStage(operation);
    setProgress(0);
    setWarnings([]);
    setMessage(
      operation === "scan"
        ? "Đang đọc ảnh…"
        : operation === "classify"
          ? "Đang chuẩn bị CLIP…"
          : "Đang sao chép…",
    );
    const onEvent = new Channel<Event>();
    onEvent.onmessage = (e) => {
      if (active.current !== jobId) return;
      if (e.type === "photo" && e.photo) setPhotos((p) => [...p, e.photo!]);
      if (e.type === "classified")
        setPhotos((p) =>
          p.map((x) =>
            x.path === e.path
              ? { ...x, category: e.category, score: e.score, error: undefined }
              : x,
          ),
        );
      if (e.type === "copied")
        setPhotos((p) =>
          p.map((x) =>
            x.path === e.path ? { ...x, destination: e.destination } : x,
          ),
        );
      if (e.type === "photo_error") {
        notify(`${e.path}: ${e.message}`);
        setPhotos((p) =>
          p.map((x) => (x.path === e.path ? { ...x, error: e.message } : x)),
        );
      }
      if (e.type === "warning") notify(e.message || "Không đọc được ảnh");
      if (e.type === "status") setMessage(e.message || "");
      if (e.type === "progress")
        setProgress(Math.round(((e.current || 0) / (e.total || 1)) * 100));
      if (e.type === "error") {
        notify(e.message || "Có lỗi");
        setMessage("Không hoàn tất tác vụ");
      }
      if (e.type === "done") {
        setProgress(100);
        setMessage(
          operation === "scan"
            ? `Đã đọc ${e.count} ảnh`
            : operation === "classify"
              ? "Đã xử lý xong. Xem lại nhóm và các lỗi trước khi sao chép."
              : `Đã sao chép ${e.count} ảnh. Nhật ký: ${e.journal}`,
        );
      }
    };
    try {
      await invoke("run_worker", {
        jobId,
        onEvent,
        request: {
          operation,
          source: root,
          destination,
          threshold,
          photos: (operation === "copy"
            ? classified.filter((p) => !p.destination)
            : operation === "classify"
              ? photos.filter((p) => !p.destination)
              : photos
          ).map(({ path, stamp, category }) => ({ path, stamp, category })),
        },
      });
    } catch (error) {
      notify(String(error));
      setMessage("Tác vụ đã dừng. Kết quả đã hoàn tất vẫn được giữ lại.");
    } finally {
      if (active.current === jobId) {
        active.current = null;
        setBusy(false);
        setStage("");
      }
    }
  }
  async function chooseSource() {
    try {
      const selected = await open({
        directory: true,
        multiple: false,
        title: "Chọn thư mục ảnh gốc",
      });
      if (typeof selected === "string") {
        setSource(selected);
        setPhotos([]);
        setGroup("all");
        setDemo(false);
        await run("scan", selected);
      }
    } catch (error) {
      notify(String(error));
    }
  }
  async function chooseDestination() {
    try {
      const selected = await open({
        directory: true,
        multiple: false,
        title: "Chọn thư mục lưu bản sao",
      });
      if (typeof selected === "string" && selected !== destination) {
        setDestination(selected);
        setPhotos((p) => p.map((x) => ({ ...x, destination: undefined })));
      }
    } catch (error) {
      notify(String(error));
    }
  }
  async function cancel() {
    if (active.current)
      try {
        await invoke("cancel_worker", { jobId: active.current });
      } catch (error) {
        notify(String(error));
      }
  }
  function showDemo() {
    const examples = [
      ["landscape", "Đồi xanh buổi sáng", "#a0b7a1", "#e6d5ac"],
      ["portrait", "Một ngày bình yên", "#c59f8a", "#e4c9b8"],
      ["architecture", "Góc phố quen", "#a5b2bc", "#e1d2b8"],
      ["food", "Bữa sáng cuối tuần", "#b58a61", "#dfc5a1"],
      ["review", "Khoảnh khắc 05", "#a1a0b6", "#d6c8c4"],
      ["landscape", "Bên bờ biển", "#8eaeb8", "#e5d7b6"],
    ];
    setDemo(true);
    setWarnings([]);
    setGroup("all");
    setPhotos(
      examples.map(([category, title, a, b], i) => ({
        path: `${title}.jpg`,
        stamp: "demo",
        category,
        score: category === "review" ? 0.38 : 0.81 + i * 0.02,
        thumbnail: `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="600" height="420"><defs><linearGradient id="g" x2=".8" y2="1"><stop stop-color="${b}"/><stop offset="1" stop-color="${a}"/></linearGradient></defs><rect width="600" height="420" fill="url(#g)"/><circle cx="450" cy="100" r="42" fill="#fff" opacity=".45"/><path d="M0 330L180 160L350 340L470 225L600 310V420H0Z" fill="${a}"/><path d="M0 385L260 255L490 420H0Z" fill="#233f38" opacity=".18"/></svg>`)}`,
      })),
    );
    setMessage(
      "Dữ liệu minh họa giao diện — chưa chạy AI, không truy cập file trên máy.",
    );
  }
  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href="#">
          <span className="brand-icon">▧</span>
          <span>
            image library<small>KHÔNG GIAN ẢNH CỦA BẠN</small>
          </span>
        </a>
        <div className="workspace-label">THƯ VIỆN</div>
        <nav aria-label="Nhóm ảnh">
          {Object.entries(groups).map(([key, label]) => (
            <button
              key={key}
              className={`nav-item ${group === key ? "selected" : ""}`}
              onClick={() => setGroup(key)}
            >
              <span className="nav-symbol">{symbols[key]}</span>
              {label}
              <span className="count">
                {key === "all"
                  ? photos.length
                  : photos.filter((p) => p.category === key).length}
              </span>
            </button>
          ))}
        </nav>
        <div className="privacy">
          <span>◈</span>
          <strong>Ảnh ở lại trên máy bạn</strong>
          <p>
            CLIP xử lý cục bộ. Không gửi ảnh đến dịch vụ AI. Lần đầu cần tải mô
            hình.
          </p>
        </div>
        <div className="sidebar-footer">
          <span className="status-dot" />
          {desktop ? "Ứng dụng desktop" : "Bản xem giao diện"}
          <span className="version">v0.1</span>
        </div>
      </aside>
      <main>
        <header>
          <div className="breadcrumb">
            Không gian cá nhân <span>/</span> Thư viện ảnh
          </div>
          <span className="local-badge">◉ LOCAL FIRST</span>
        </header>
        <section className="content">
          <div className="page-heading">
            <div>
              <div className="eyebrow">SẮP XẾP ÍT HƠN. KHÁM PHÁ NHIỀU HƠN.</div>
              <h1>
                Những khoảnh khắc,
                <br />
                <em>đúng nơi thuộc về.</em>
              </h1>
              <p>
                Để AI giúp bạn sắp xếp thư viện. Bạn quyết định điều gì được
                lưu.
              </p>
            </div>
            <div className="heading-mark" aria-hidden="true">
              ▧<span>✦</span>
            </div>
          </div>
          {!desktop && (
            <div className="browser-note">
              <div>
                <strong>Đây là giao diện web của ứng dụng desktop.</strong>
                <p>Chọn thư mục và chạy AI cần bản Tauri trên máy của bạn.</p>
              </div>
              <button className="secondary" onClick={showDemo}>
                Xem dữ liệu minh họa ↗
              </button>
            </div>
          )}
          <section className="workflow" aria-label="Quy trình phân loại">
            <div className="step">
              <span className="step-number">01</span>
              <div>
                <h3>Thư mục nguồn</h3>
                <p title={source}>
                  {source || "Nơi chứa những bức ảnh của bạn"}
                </p>
                <button
                  disabled={busy || !desktop}
                  onClick={chooseSource}
                  className="text-button"
                >
                  {source ? "Đổi thư mục" : "Chọn thư mục"} <span>↗</span>
                </button>
              </div>
            </div>
            <div className="step">
              <span className="step-number">02</span>
              <div>
                <h3>Phân loại bằng AI</h3>
                <p>CLIP · 7 nhóm nội dung</p>
                <button
                  disabled={
                    busy ||
                    !photos.length ||
                    demo ||
                    !desktop ||
                    photos.every((p) => !!p.destination)
                  }
                  className="text-button"
                  onClick={() => {
                    setPhotos((p) =>
                      p.map((x) =>
                        x.destination
                          ? x
                          : {
                              ...x,
                              category: undefined,
                              score: undefined,
                              error: undefined,
                            },
                      ),
                    );
                    void run("classify");
                  }}
                >
                  Phân loại ảnh <span>✦</span>
                </button>
              </div>
            </div>
            <div className="step">
              <span className="step-number">03</span>
              <div>
                <h3>Lưu bản sao đã sắp xếp</h3>
                <p title={destination}>
                  {destination || "Ảnh gốc luôn được giữ nguyên"}
                </p>
                <button
                  disabled={busy || !desktop}
                  className="text-button"
                  onClick={chooseDestination}
                >
                  {destination ? "Đổi thư mục đích" : "Chọn nơi lưu"}{" "}
                  <span>↗</span>
                </button>
              </div>
            </div>
          </section>
          <div className="settings">
            <label>
              Ngưỡng cần xem lại{" "}
              <input
                type="range"
                min="0.2"
                max="0.95"
                step="0.05"
                value={threshold}
                disabled={busy}
                onChange={(e) => setThreshold(Number(e.target.value))}
              />
              <strong>{Math.round(threshold * 100)}%</strong>
            </label>
            <span>
              Điểm CLIP tương đối giữa các nhóm, không phải độ chính xác.
            </span>
          </div>
          <div className="gallery-toolbar">
            <div>
              <h2>
                {groups[group]} <span>{visible.length}</span>
              </h2>
              <p>
                {demo
                  ? "Ảnh minh họa · kết quả giả lập"
                  : `${classified.length} ảnh đã phân loại · ${copied} ảnh đã sao chép`}
              </p>
            </div>
            <div className="gallery-actions">
              <label className="search">
                <span>⌕</span>
                <input
                  aria-label="Tìm ảnh"
                  placeholder="Tìm theo tên ảnh…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </label>
              <button
                className="primary"
                disabled={
                  busy ||
                  !destination ||
                  !classified.some((p) => !p.destination) ||
                  demo ||
                  !desktop
                }
                onClick={() => setConfirm(true)}
              >
                Sao chép theo nhóm ↗
              </button>
            </div>
          </div>
          <div className="activity" role="status">
            <span className={`status-dot ${busy ? "working" : ""}`} />
            <span>{message}</span>
            {busy && (
              <button className="text-button" onClick={cancel}>
                Hủy tác vụ
              </button>
            )}
          </div>
          {busy && (
            <div
              className="progress"
              role="progressbar"
              aria-label={stage}
              aria-valuenow={progress}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div style={{ width: `${progress || 3}%` }} />
            </div>
          )}
          {warnings.length > 0 && (
            <details className="warnings" open>
              <summary>{warnings.length} thông báo cần chú ý</summary>
              <ul>
                {warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </details>
          )}
          {visible.length ? (
            <div className="photo-grid">
              {visible.map((photo) => (
                <article className="photo-card" key={photo.path}>
                  <div className="photo-image">
                    <img
                      src={photo.thumbnail}
                      alt={photo.path}
                      loading="lazy"
                    />
                    {photo.destination && (
                      <span className="copied-badge">✓ Đã sao chép</span>
                    )}
                  </div>
                  <div className="photo-info">
                    <h3 title={photo.path}>
                      {photo.path.split(/[\\/]/).pop()}
                    </h3>
                    <div className="photo-meta">
                      <span
                        className={photo.category === "review" ? "review" : ""}
                      >
                        {photo.error
                          ? "⚠ Lỗi xử lý"
                          : photo.category
                            ? groups[photo.category]
                            : "Chưa phân loại"}
                      </span>
                      {photo.score !== undefined && (
                        <span>{Math.round(photo.score * 100)}%</span>
                      )}
                    </div>
                    {photo.category && !demo && (
                      <select
                        aria-label={`Nhóm của ${photo.path}`}
                        value={photo.category}
                        disabled={busy || !!photo.destination}
                        onChange={(e) =>
                          setPhotos((p) =>
                            p.map((x) =>
                              x.path === photo.path
                                ? { ...x, category: e.target.value }
                                : x,
                            ),
                          )
                        }
                      >
                        {Object.entries(groups)
                          .filter(([key]) => key !== "all")
                          .map(([key, label]) => (
                            <option key={key} value={key}>
                              {label}
                            </option>
                          ))}
                      </select>
                    )}
                    {photo.error && (
                      <p className="photo-error">{photo.error}</p>
                    )}
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <div className="empty">
              <div className="empty-icon">
                ▧<span>+</span>
              </div>
              <h2>
                {photos.length
                  ? "Không tìm thấy ảnh phù hợp"
                  : "Một thư viện ngăn nắp bắt đầu từ đây"}
              </h2>
              <p>
                {photos.length
                  ? "Thử tên khác hoặc chọn nhóm ảnh khác."
                  : "Chọn thư mục ảnh. Xem trước cách AI phân loại, rồi lưu bản sao vào từng nhóm."}
              </p>
              {!photos.length && (
                <button
                  className="primary"
                  disabled={busy || !desktop}
                  onClick={chooseSource}
                >
                  ＋ Chọn thư mục ảnh
                </button>
              )}
              <small>JPG, PNG, WEBP, BMP, TIFF · Bao gồm thư mục con</small>
            </div>
          )}
          <footer>
            <span>✦ Một góc nhỏ cho những điều đáng nhớ.</span>
            <span>Ảnh gốc được giữ nguyên · Bạn luôn kiểm soát</span>
          </footer>
        </section>
      </main>
      {confirm && (
        <div className="modal-backdrop">
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="confirm-title"
          >
            <div className="eyebrow">XÁC NHẬN SAO CHÉP</div>
            <h2 id="confirm-title">Sẵn sàng sắp xếp thư viện?</h2>
            <p>
              Sao chép {classified.filter((p) => !p.destination).length} ảnh vào
              các thư mục theo nhóm tại:
            </p>
            <code>{destination}</code>
            <p>
              Không xóa ảnh gốc. Tên trùng được thêm số. Ảnh “Cần xem lại” được
              lưu vào thư mục <b>review</b>. Hủy tác vụ giữ các bản sao đã hoàn
              tất; nhật ký ghi trong thư mục đích.
            </p>
            <div className="modal-actions">
              <button className="secondary" onClick={() => setConfirm(false)}>
                Quay lại
              </button>
              <button
                className="primary"
                onClick={() => {
                  setConfirm(false);
                  void run("copy");
                }}
              >
                Xác nhận sao chép
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
