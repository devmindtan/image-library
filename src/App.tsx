import { useMemo, useRef, useState, useEffect } from "react";
import { Channel, invoke, isTauri, convertFileSrc } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";

import MediaViewer from "./MediaViewer";

type Media = {
  path: string;
  stamp: string;
  thumbnail: string;
  kind: "image" | "video";
  size: number;
  modified: number;
  category?: string;
  score?: number;
  error?: string;
  previewError?: string;
  destination?: string;
};
type PlanItem = {
  path: string;
  stamp: string;
  target: string;
  category: string;
  kind: string;
  size: number;
  action: "skip" | "move" | "copy";
};
type Mode = "inplace" | "copy";
type Strategy = "type" | "content";
type Request = {
  operation: string;
  source: string;
  destination?: string;
  mode?: Mode;
  strategy?: Strategy;
  threshold?: number;
  photos?: { path: string; stamp: string; category?: string }[];
  plan?: PlanItem[];
};
type WorkerEvent = {
  type: string;
  photo?: Media;
  path?: string;
  newPath?: string;
  stamp?: string;
  category?: string;
  score?: number;
  message?: string;
  current?: number;
  total?: number;
  count?: number;
  skipped?: number;
  destination?: string;
  journal?: string;
  plan?: PlanItem[];
  detail?: Record<string, unknown>;
};
type View = "grid" | "compact" | "list" | "folders";
const groups: Record<string, string> = {
  images: "Ảnh",
  videos: "Video",
  landscape: "Phong cảnh",
  portrait: "Chân dung",
  document: "Tài liệu",
  animals: "Động vật",
  vehicles: "Phương tiện",
  food: "Ẩm thực",
  architecture: "Kiến trúc",
  review: "Cần xem lại",
};
const desktop = isTauri();
const normalize = (path: string) => path.replaceAll("\\", "/");
const fileName = (path: string) => normalize(path).split("/").pop() || path;
const parent = (path: string) =>
  normalize(path).split("/").slice(0, -1).join("/");
export function formatBytes(bytes: number) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  return `${new Intl.NumberFormat("vi-VN", { maximumFractionDigits: i ? 1 : 0 }).format(bytes / 1024 ** i)} ${units[i]}`;
}
const bytesOf = (items: Media[]) => items.reduce((n, p) => n + p.size, 0);
function initialView(): View {
  try {
    const v = localStorage.getItem("library-view");
    return ["grid", "compact", "list", "folders"].includes(v || "")
      ? (v as View)
      : "grid";
  } catch {
    return "grid";
  }
}

export default function App() {
  const [viewer, setViewer] = useState<Media | null>(null);
  const [mediaUrl, setMediaUrl] = useState("");
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [trashSelection, setTrashSelection] = useState<Media[] | null>(null);
  const trashDialog = useRef<HTMLDialogElement>(null);
  const viewerToken = useRef(0);
  useEffect(() => {
    if (trashSelection) trashDialog.current?.showModal();
    else trashDialog.current?.close();
  }, [trashSelection]);
  const [source, setSource] = useState("");
  const [destination, setDestination] = useState("");
  const [items, setItems] = useState<Media[]>([]);
  const [kind, setKind] = useState("all");
  const [category, setCategory] = useState("all");
  const [folder, setFolder] = useState("");
  const [query, setQuery] = useState("");
  const [view, setView] = useState<View>(initialView);
  const [sort, setSort] = useState("name");
  const [selection, setSelection] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState<Mode>("inplace");
  const [strategy, setStrategy] = useState<Strategy>("content");
  const [threshold, setThreshold] = useState(0.55);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(
    "Mở một thư mục để hiển thị ảnh và video.",
  );
  const [progress, setProgress] = useState(0);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [demo, setDemo] = useState(false);
  const [preview, setPreview] = useState<{
    request: Request;
    plan: PlanItem[];
  } | null>(null);
  const active = useRef<string | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    try {
      localStorage.setItem("library-view", view);
    } catch {
      /* Read-only browser storage does not block the library. */
    }
  }, [view]);
  useEffect(() => {
    if (preview && !dialog.current?.open) dialog.current?.showModal();
    else if (!preview && dialog.current?.open) dialog.current.close();
  }, [preview]);
  const filtered = useMemo(
    () =>
      items
        .filter(
          (p) =>
            (kind === "all" || p.kind === kind) &&
            (category === "all" || p.category === category) &&
            (!folder || normalize(p.path).startsWith(folder + "/")) &&
            p.path.toLocaleLowerCase().includes(query.toLocaleLowerCase()),
        )
        .sort((a, b) =>
          sort === "size-desc"
            ? b.size - a.size
            : sort === "size-asc"
              ? a.size - b.size
              : sort === "newest"
                ? b.modified - a.modified
                : a.path.localeCompare(b.path, "vi", { numeric: true }),
        ),
    [items, kind, category, folder, query, sort],
  );
  const folderEntries = useMemo(() => {
    const result = new Map<string, Media[]>();
    for (const item of filtered) {
      const rest = normalize(item.path).slice(folder ? folder.length + 1 : 0);
      if (!rest.includes("/")) continue;
      const key = (folder ? folder + "/" : "") + rest.split("/")[0];
      result.set(key, [...(result.get(key) || []), item]);
    }
    return [...result.entries()].sort(([a], [b]) => a.localeCompare(b, "vi"));
  }, [filtered, folder]);
  const shown =
    view === "folders"
      ? filtered.filter((p) => parent(p.path) === folder)
      : filtered;
  const selectedItems = items.filter((p) => selection.has(p.path));
  const scope = selection.size ? selectedItems : filtered;
  const images = items.filter((p) => p.kind === "image");
  const videos = items.filter((p) => p.kind === "video");
  const notify = (text: string) => setWarnings((w) => [...w, text]);
  const payload = (list: Media[]) =>
    list.map(({ path, stamp, category }) => ({ path, stamp, category }));
  function toggle(path: string) {
    setSelection((old) => {
      const next = new Set(old);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }
  async function run(request: Request): Promise<boolean> {
    if (!desktop) {
      setMessage("Mở ứng dụng desktop để làm việc với file trên máy.");
      return false;
    }
    if (active.current) return false;
    const id = crypto.randomUUID();
    active.current = id;
    setBusy(true);
    setProgress(0);
    setWarnings([]);
    const taskWarnings: string[] = [];
    const report = (text: string) => {
      taskWarnings.push(text);
      notify(text);
    };
    let success = true;
    let resultMessage =
      "Tác vụ đã dừng. Kiểm tra nhật ký nếu đã thay đổi file.";
    setMessage(
      request.operation === "scan"
        ? "Đang quét thư mục…"
        : request.operation === "plan"
          ? "Đang kiểm tra và tạo bản xem trước…"
          : request.operation === "classify"
            ? "Đang phân loại ảnh bằng CLIP…"
            : "Đang sắp xếp file…",
    );
    if (request.operation === "scan") {
      setItems([]);
      setSelection(new Set());
    }
    const onEvent = new Channel<WorkerEvent>();
    onEvent.onmessage = (e) => {
      if (active.current !== id) return;
      if (e.type === "metadata" && e.detail) setDetail(e.detail);
      if (e.type === "trashed") {
        setItems((p) => p.filter((x) => x.path !== e.path));
        setSelection((old) => {
          const next = new Set(old);
          next.delete(e.path!);
          return next;
        });
      }
      if (e.type === "photo" && e.photo) setItems((p) => [...p, e.photo!]);
      if (e.type === "classified")
        setItems((p) =>
          p.map((x) =>
            x.path === e.path
              ? { ...x, category: e.category, score: e.score, error: undefined }
              : x,
          ),
        );
      if (e.type === "copied")
        setItems((p) =>
          p.map((x) =>
            x.path === e.path
              ? { ...x, destination: e.destination, error: undefined }
              : x,
          ),
        );
      if (e.type === "moved")
        setItems((p) =>
          p.map((x) =>
            x.path === e.path
              ? {
                  ...x,
                  path: e.newPath!,
                  category: e.category,
                  stamp: e.stamp!,
                  error: undefined,
                }
              : x,
          ),
        );
      if (e.type === "plan" && e.plan) setPreview({ request, plan: e.plan });
      if (e.type === "warning") report(e.message || "Không đọc được file");
      if (e.type === "photo_error") {
        report(`${e.path}: ${e.message}`);
        setItems((p) =>
          p.map((x) => (x.path === e.path ? { ...x, error: e.message } : x)),
        );
      }
      if (e.type === "status") setMessage(e.message || "");
      if (e.type === "progress")
        setProgress(Math.round(((e.current || 0) / (e.total || 1)) * 100));
      if (e.type === "error") {
        success = false;
        report(e.message || "Có lỗi");
        resultMessage = "Không hoàn tất tác vụ.";
        setMessage(resultMessage);
      }
      if (e.type === "done") {
        setProgress(100);
        resultMessage =
          request.operation === "metadata"
            ? "Đã đọc metadata."
            : request.operation === "trash"
              ? `Đã chuyển ${e.count} file vào Thùng rác. Nhật ký: ${e.journal}`
              : request.operation === "scan"
                ? `Đã hiển thị ${e.count} file. Chưa thay đổi file nào.`
                : request.operation === "classify"
                  ? "Đã xử lý phân loại. Bạn có thể chỉnh nhóm từng ảnh."
                  : request.operation === "plan"
                    ? "Kiểm tra đường dẫn trước khi xác nhận."
                    : `Đã ${request.mode === "inplace" ? "di chuyển" : "sao chép"} ${e.count} file, giữ nguyên ${e.skipped || 0} file đúng vị trí. Nhật ký: ${e.journal}`;
        setMessage(resultMessage);
      }
    };
    try {
      await invoke("run_worker", { jobId: id, onEvent, request });
    } catch (error) {
      success = false;
      report(String(error));
      setMessage(resultMessage);
    } finally {
      if (active.current === id) {
        active.current = null;
        setBusy(false);
      }
    }
    // Re-read the real tree even after cancellation, including a possible linked duplicate.
    if (request.operation === "apply" && request.mode === "inplace") {
      setFolder("");
      setSelection(new Set());
      await run({ operation: "scan", source: request.source });
      setWarnings((current) => [...taskWarnings, ...current]);
      setMessage(resultMessage + " Đã quét lại thư mục.");
    }
    return success;
  }
  async function openViewer(item: Media) {
    if (busy) return;
    const token = ++viewerToken.current;
    setViewer(item);
    setDetail(null);
    setMediaUrl("");
    if (demo) {
      setMediaUrl(item.thumbnail);
      setDetail({
        general: {
          "Tên file": item.path,
          "Dung lượng (bytes)": item.size,
          "Lưu ý": "Dữ liệu minh họa",
        },
      });
      return;
    }
    void run({ operation: "metadata", source, photos: payload([item]) });
    try {
      const path = await invoke<string>("authorize_media", {
        source,
        path: item.path,
      });
      if (viewerToken.current === token) setMediaUrl(convertFileSrc(path));
    } catch (error) {
      notify(String(error));
      if (viewerToken.current === token)
        setDetail({ warnings: [String(error)] });
    }
  }
  function closeViewer() {
    viewerToken.current++;
    setViewer(null);
    setMediaUrl("");
    if (active.current) void cancel();
  }
  function stepViewer(step: number) {
    if (!viewer) return;
    const index = filtered.findIndex((p) => p.path === viewer.path);
    const next = filtered[(index + step + filtered.length) % filtered.length];
    if (next) void openViewer(next);
  }
  async function chooseSource() {
    try {
      const selected = await open({
        directory: true,
        multiple: false,
        title: "Mở thư mục ảnh và video",
      });
      if (typeof selected === "string") {
        setSource(selected);
        setDemo(false);
        setFolder("");
        setKind("all");
        setCategory("all");
        setQuery("");
        await run({ operation: "scan", source: selected });
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
        setItems((p) => p.map((x) => ({ ...x, destination: undefined })));
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
  async function makePreview() {
    const list = scope.filter((p) => mode !== "copy" || !p.destination);
    if (!list.length) {
      setMessage("Không có file cần sắp xếp trong phạm vi này.");
      return;
    }
    await run({
      operation: "plan",
      source,
      destination,
      mode,
      strategy,
      photos: payload(list),
    });
  }
  function showDemo() {
    const data: [string, Media["kind"], string, number, string][] = [
      ["Kỳ nghỉ/biển.jpg", "image", "landscape", 2453120, "#bad0d6"],
      ["Gia đình/chân dung.jpg", "image", "portrait", 1835008, "#e2c6b2"],
      ["Kỳ nghỉ/chuyến đi.mp4", "video", "videos", 146800640, ""],
      ["Tài liệu/hóa đơn.png", "image", "document", 563200, "#d9ded4"],
      ["clip.mov", "video", "videos", 73400320, ""],
      ["khu vườn.jpg", "image", "landscape", 3145728, "#aec4a1"],
    ];
    setDemo(true);
    setSource("Thư viện minh họa");
    setFolder("");
    setKind("all");
    setCategory("all");
    setQuery("");
    setSelection(new Set());
    setWarnings([]);
    setItems(
      data.map(([path, kind, category, size, color], i) => ({
        path,
        kind,
        category,
        size,
        stamp: "demo",
        modified: 1700000000 + i,
        score: kind === "image" ? 0.8 : undefined,
        thumbnail:
          kind === "image"
            ? `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="320" height="220"><rect width="320" height="220" fill="${color}"/><path d="M0 220L110 70L210 170L260 110L320 170V220Z" fill="#657c6e" opacity=".4"/></svg>`)}`
            : "",
      })),
    );
    setMessage("Dữ liệu minh họa. Chưa truy cập hoặc thay đổi file thật.");
  }
  function filterKind(value: string) {
    setKind(value);
    setCategory("all");
    setSelection(new Set());
  }
  const mediaPreview = (item: Media) =>
    item.thumbnail ? (
      <img src={item.thumbnail} alt="" loading="lazy" />
    ) : (
      <div className={`file-placeholder ${item.kind}`}>
        <span>{item.kind === "video" ? "▶" : "▧"}</span>
        <small>{fileName(item.path).split(".").pop()?.toUpperCase()}</small>
      </div>
    );
  const categorySelect = (item: Media) =>
    item.kind === "video" ? (
      <span className="tag">Video</span>
    ) : (
      <select
        aria-label={`Nhóm của ${item.path}`}
        value={item.category || ""}
        disabled={busy || demo}
        onChange={(e) =>
          setItems((p) =>
            p.map((x) =>
              x.path === item.path
                ? {
                    ...x,
                    category: e.target.value || undefined,
                    score: undefined,
                    error: undefined,
                  }
                : x,
            ),
          )
        }
      >
        <option value="">Chưa phân loại</option>
        {Object.entries(groups)
          .filter(([key]) => key !== "videos")
          .map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
      </select>
    );
  return (
    <div className="app-shell">
      <header className="app-header">
        <a className="brand" href="#" aria-label="Thư viện ảnh và video">
          Thư viện <span>Ảnh & Video</span>
        </a>
        <div className="header-actions">
          {!desktop && (
            <button onClick={showDemo} disabled={busy}>
              Xem minh họa
            </button>
          )}
          <button
            className="primary"
            onClick={chooseSource}
            disabled={busy || !desktop}
          >
            Mở thư mục
          </button>
        </div>
      </header>
      <div className="workspace">
        <aside className="sidebar" aria-label="Bộ lọc thư viện">
          <h2>Thư viện</h2>
          <nav>
            {[
              ["all", "Tất cả", items],
              ["image", "Ảnh", images],
              ["video", "Video", videos],
            ].map(([key, label, list]) => (
              <button
                key={key as string}
                aria-pressed={kind === key && category === "all"}
                onClick={() => filterKind(key as string)}
              >
                <span>{label as string}</span>
                <span className="filter-stats">
                  {(list as Media[]).length}
                  <small>{formatBytes(bytesOf(list as Media[]))}</small>
                </span>
              </button>
            ))}
          </nav>
          <details className="category-filters">
            <summary>Lọc theo nhãn ảnh</summary>
            {Object.entries(groups)
              .filter(([key]) => !["images", "videos"].includes(key))
              .map(([key, label]) => {
                const list = images.filter((p) => p.category === key);
                return (
                  <button
                    key={key}
                    aria-pressed={category === key}
                    onClick={() => {
                      setKind("image");
                      setCategory(key);
                      setSelection(new Set());
                    }}
                  >
                    <span>{label}</span>
                    <span>{list.length}</span>
                  </button>
                );
              })}
          </details>
          <div className="storage-summary">
            <strong>{formatBytes(bytesOf(items))}</strong>
            <span>Tổng dung lượng file</span>
            <small>Chỉ đọc khi mở thư mục. Thay đổi file cần xác nhận.</small>
          </div>
        </aside>
        <main>
          <section className="library-heading">
            <div>
              <h1>{source ? fileName(source) : "Ảnh và video của bạn"}</h1>
              <p className="source-path" title={source}>
                {source ||
                  "Mở thư mục hiện có để xem trực tiếp, không cần sao chép."}
              </p>
            </div>
            <button
              disabled={busy || !source || demo}
              onClick={() => void run({ operation: "scan", source })}
            >
              Quét lại
            </button>
          </section>
          {!desktop && (
            <p className="browser-note">
              Bản xem trên trình duyệt. Mở ứng dụng Tauri để đọc và sắp xếp file
              trên máy.
            </p>
          )}
          <section className="summary" aria-label="Dung lượng thư viện">
            <div>
              <span>Tổng</span>
              <strong>
                {items.length} file · {formatBytes(bytesOf(items))}
              </strong>
            </div>
            <div>
              <span>Ảnh</span>
              <strong>
                {images.length} · {formatBytes(bytesOf(images))}
              </strong>
            </div>
            <div>
              <span>Video</span>
              <strong>
                {videos.length} · {formatBytes(bytesOf(videos))}
              </strong>
            </div>
          </section>
          <section className="organizer" aria-label="Sắp xếp thư mục">
            <div className="organizer-options">
              <label>
                Thao tác
                <select
                  aria-label="Thao tác sắp xếp"
                  value={mode}
                  disabled={busy}
                  onChange={(e) => setMode(e.target.value as Mode)}
                >
                  <option value="inplace">Sắp xếp tại thư mục đang mở</option>
                  <option value="copy">Sao chép sang thư mục khác</option>
                </select>
              </label>
              <label>
                Tạo thư mục theo
                <select
                  aria-label="Cách phân nhóm"
                  value={strategy}
                  disabled={busy}
                  onChange={(e) => setStrategy(e.target.value as Strategy)}
                >
                  <option value="type">Theo loại: Ảnh / Video</option>
                  <option value="content">Theo nội dung ảnh + Video</option>
                </select>
              </label>
              {mode === "copy" && (
                <button
                  title={destination}
                  disabled={busy || !desktop || demo}
                  onClick={chooseDestination}
                >
                  {destination
                    ? `Nơi lưu: ${fileName(destination)}`
                    : "Chọn nơi lưu"}
                </button>
              )}
              <button
                className="primary"
                disabled={
                  busy ||
                  !scope.length ||
                  !desktop ||
                  demo ||
                  (mode === "copy" && !destination)
                }
                onClick={makePreview}
              >
                Xem trước sắp xếp
              </button>
            </div>
            <p className="group-explanation">
              Nhãn ảnh mô tả nội dung để lọc thư viện. “Tạo thư mục theo” quyết
              định thư mục đích khi sắp xếp: ví dụ Phong cảnh → landscape/,
              Video → videos/. Chạy AI hoặc đổi nhãn chưa di chuyển file; chỉ
              xác nhận sắp xếp mới thay đổi vị trí.
            </p>
            <p>
              {mode === "inplace"
                ? "Di chuyển vào thư mục con của thư mục đang mở. Không nhân đôi file."
                : "Tạo bản sao trong thư mục đích và giữ nguyên nguồn."}{" "}
              {selection.size
                ? `Áp dụng cho ${scope.length} file đã chọn.`
                : `Áp dụng cho ${scope.length} file đang lọc.`}
            </p>
            <div className="ai-options">
              <button
                disabled={
                  busy ||
                  demo ||
                  !desktop ||
                  !scope.some((p) => p.kind === "image")
                }
                className="primary"
                onClick={() => {
                  setStrategy("content");
                  void run({
                    operation: "classify",
                    source,
                    threshold,
                    photos: payload(scope.filter((p) => p.kind === "image")),
                  });
                }}
              >
                Phân loại ảnh bằng AI
              </button>
              <label>
                Ngưỡng cần xem lại{" "}
                <input
                  aria-label="Ngưỡng cần xem lại"
                  type="range"
                  min="0.2"
                  max="0.95"
                  step="0.05"
                  value={threshold}
                  disabled={busy}
                  onChange={(e) => setThreshold(Number(e.target.value))}
                />
                {Math.round(threshold * 100)}%
              </label>
              <small>
                Video luôn vào nhóm Video. Điểm AI không phải độ chính xác.
              </small>
            </div>
            {strategy === "content" &&
              scope.some((p) => p.kind === "image" && !p.category) && (
                <p>
                  Ảnh chưa phân loại:{" "}
                  {
                    scope.filter((p) => p.kind === "image" && !p.category)
                      .length
                  }
                  . Bấm “Phân loại ảnh bằng AI” hoặc chọn nhóm cho từng ảnh
                  trước khi sắp xếp.
                </p>
              )}
          </section>
          <section className="toolbar" aria-label="Chế độ xem">
            <input
              type="search"
              aria-label="Tìm file"
              placeholder="Tìm tên hoặc đường dẫn…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <select
              aria-label="Sắp xếp hiển thị"
              value={sort}
              onChange={(e) => setSort(e.target.value)}
            >
              <option value="name">Tên A → Z</option>
              <option value="size-desc">Dung lượng giảm dần</option>
              <option value="size-asc">Dung lượng tăng dần</option>
              <option value="newest">Mới nhất</option>
            </select>
            <div className="view-switch" role="group" aria-label="Chế độ xem">
              {(
                [
                  ["grid", "Lưới"],
                  ["compact", "Gọn"],
                  ["list", "Danh sách"],
                  ["folders", "Thư mục"],
                ] as [View, string][]
              ).map(([key, label]) => (
                <button
                  key={key}
                  aria-pressed={view === key}
                  onClick={() => setView(key)}
                >
                  {label}
                </button>
              ))}
            </div>
          </section>
          <div className="library-meta">
            <div className="breadcrumbs" aria-label="Đường dẫn thư mục">
              <button onClick={() => setFolder("")}>Thư mục gốc</button>
              {folder
                .split("/")
                .filter(Boolean)
                .map((part, index) => (
                  <span key={index}>
                    {" "}
                    /{" "}
                    <button
                      onClick={() =>
                        setFolder(
                          folder
                            .split("/")
                            .slice(0, index + 1)
                            .join("/"),
                        )
                      }
                    >
                      {part}
                    </button>
                  </span>
                ))}
            </div>
            <span>
              {filtered.length} file · {formatBytes(bytesOf(filtered))}
              {selection.size > 0 &&
                ` | Đã chọn ${selection.size} · ${formatBytes(bytesOf(selectedItems))}`}
            </span>
          </div>
          <div className="activity" role="status">
            <span>{message}</span>
            {busy && <button onClick={cancel}>Hủy tác vụ</button>}
          </div>
          {busy && (
            <progress aria-label="Tiến trình" max="100" value={progress || 2} />
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
          {filtered.length > 0 && (
            <div className="selection-bar">
              <label>
                <input
                  type="checkbox"
                  aria-label="Chọn tất cả file đang lọc"
                  disabled={busy}
                  checked={
                    filtered.length > 0 &&
                    filtered.every((p) => selection.has(p.path))
                  }
                  onChange={(e) =>
                    setSelection(
                      e.target.checked
                        ? new Set(filtered.map((p) => p.path))
                        : new Set(),
                    )
                  }
                />{" "}
                Chọn tất cả file đang lọc
              </label>
              {selection.size > 0 && (
                <button
                  className="danger"
                  disabled={busy || demo || !desktop}
                  onClick={() => setTrashSelection(selectedItems)}
                >
                  Xóa {selection.size} file đã chọn
                </button>
              )}
              {selection.size > 0 && (
                <button onClick={() => setSelection(new Set())} disabled={busy}>
                  Bỏ chọn
                </button>
              )}
              <span>
                {view === "folders"
                  ? "Thư mục con và file trực tiếp bên dưới"
                  : "Xem file, chỉnh nhóm rồi tạo bản xem trước."}
              </span>
            </div>
          )}
          {view === "folders" && folderEntries.length > 0 && (
            <div className="folder-grid">
              {folderEntries.map(([path, list]) => (
                <button
                  className="folder-card"
                  key={path}
                  onClick={() => {
                    setFolder(path);
                    setSelection(new Set());
                  }}
                >
                  <span className="folder-icon" aria-hidden="true">
                    ▱
                  </span>
                  <strong>{fileName(path)}</strong>
                  <small>
                    {list.length} file · {formatBytes(bytesOf(list))}
                  </small>
                </button>
              ))}
            </div>
          )}
          {shown.length > 0 ? (
            view === "list" || view === "folders" ? (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>
                        <span className="sr-only">Chọn</span>
                      </th>
                      <th>Tên file</th>
                      <th>Loại</th>
                      <th>Dung lượng</th>
                      <th>Nhóm</th>
                      <th>Thư mục</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((item) => (
                      <tr key={item.path}>
                        <td>
                          <input
                            aria-label={`Chọn ${item.path}`}
                            type="checkbox"
                            disabled={busy}
                            checked={selection.has(item.path)}
                            onChange={() => toggle(item.path)}
                          />
                        </td>
                        <td>
                          <div className="table-file">
                            <button
                              className="table-preview preview-button"
                              aria-label={`Mở ${item.path}`}
                              disabled={busy}
                              onClick={() => void openViewer(item)}
                            >
                              {mediaPreview(item)}
                            </button>
                            <div>
                              <strong title={item.path}>
                                {fileName(item.path)}
                              </strong>
                              {item.previewError && (
                                <small>Không có ảnh xem trước</small>
                              )}
                              {item.error && (
                                <small className="error-text">
                                  {item.error}
                                </small>
                              )}
                              {item.destination && <small>Đã sao chép</small>}
                            </div>
                          </div>
                        </td>
                        <td>{item.kind === "video" ? "Video" : "Ảnh"}</td>
                        <td className="file-size" title={`${item.size} bytes`}>
                          {formatBytes(item.size)}
                        </td>
                        <td>
                          {categorySelect(item)}
                          <button
                            className="danger row-delete"
                            disabled={busy || demo || !desktop}
                            onClick={() => setTrashSelection([item])}
                          >
                            Xóa
                          </button>
                        </td>
                        <td className="table-path" title={parent(item.path)}>
                          {parent(item.path) || "Thư mục gốc"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className={`media-grid ${view}`}>
                {shown.map((item) => (
                  <article className="media-card" key={item.path}>
                    <div className="media-preview">
                      <button
                        className="open-media"
                        aria-label={`Mở ${item.path}`}
                        disabled={busy}
                        onClick={() => void openViewer(item)}
                      >
                        {mediaPreview(item)}
                      </button>
                      <label className="card-check">
                        <input
                          aria-label={`Chọn ${item.path}`}
                          type="checkbox"
                          disabled={busy}
                          checked={selection.has(item.path)}
                          onChange={() => toggle(item.path)}
                        />
                      </label>
                      <span className="media-kind">
                        {item.kind === "video" ? "Video" : "Ảnh"}
                      </span>
                    </div>
                    <div className="media-info">
                      <h2 title={item.path}>{fileName(item.path)}</h2>
                      <p className="file-details">
                        <span
                          className="file-size"
                          title={`${item.size} bytes`}
                        >
                          {formatBytes(item.size)}
                        </span>
                        <span title={parent(item.path)}>
                          {parent(item.path) || "Thư mục gốc"}
                        </span>
                      </p>
                      {categorySelect(item)}
                      <div className="file-actions">
                        <button
                          disabled={busy}
                          onClick={() => void openViewer(item)}
                        >
                          Xem / Chi tiết
                        </button>
                        <button
                          className="danger"
                          disabled={busy || demo || !desktop}
                          onClick={() => setTrashSelection([item])}
                        >
                          Xóa
                        </button>
                      </div>
                      {item.score !== undefined && (
                        <small>Điểm AI: {Math.round(item.score * 100)}%</small>
                      )}
                      {item.previewError && (
                        <small>Không tạo được ảnh xem trước</small>
                      )}
                      {item.error && (
                        <small className="error-text">{item.error}</small>
                      )}
                      {item.destination && <small>Đã sao chép</small>}
                    </div>
                  </article>
                ))}
              </div>
            )
          ) : (
            (!folderEntries.length || view !== "folders") && (
              <div className="empty">
                <h2>
                  {items.length
                    ? "Không có file phù hợp"
                    : "Chưa có ảnh hoặc video"}
                </h2>
                <p>
                  {items.length
                    ? "Thử bộ lọc khác hoặc quay về thư mục gốc."
                    : "Mở thư mục để xem các file hiện có. Chỉ khi xác nhận sắp xếp, vị trí file mới thay đổi."}
                </p>
                {!items.length && (
                  <button
                    className="primary"
                    onClick={chooseSource}
                    disabled={busy || !desktop}
                  >
                    Mở thư mục để bắt đầu
                  </button>
                )}
              </div>
            )
          )}
          <footer>
            Ảnh: JPG, PNG, WEBP, BMP, TIFF · Video: MP4, MOV, MKV, AVI, WEBM và
            các định dạng phổ biến khác.
          </footer>
        </main>
      </div>
      {viewer && (
        <MediaViewer
          item={viewer}
          url={mediaUrl}
          detail={detail}
          busy={busy}
          onClose={closeViewer}
          onStep={stepViewer}
          onDelete={() => {
            setTrashSelection([viewer]);
            closeViewer();
          }}
        />
      )}
      <dialog
        ref={trashDialog}
        onCancel={() => setTrashSelection(null)}
        onClose={() => setTrashSelection(null)}
        aria-labelledby="trash-title"
      >
        {trashSelection && (
          <>
            <h2 id="trash-title">
              Chuyển {trashSelection.length} file vào Thùng rác?
            </h2>
            <p>
              {formatBytes(bytesOf(trashSelection))}. Có thể khôi phục qua Thùng
              rác của hệ điều hành. Nếu Thùng rác không khả dụng, app báo lỗi và
              giữ file; không xóa vĩnh viễn.
            </p>
            <ul className="trash-list">
              {trashSelection.map((p) => (
                <li key={p.path}>{p.path}</li>
              ))}
            </ul>
            <div className="dialog-actions">
              <button onClick={() => setTrashSelection(null)}>Giữ lại</button>
              <button
                className="danger"
                disabled={busy}
                onClick={() => {
                  const list = trashSelection;
                  setTrashSelection(null);
                  void run({
                    operation: "trash",
                    source,
                    photos: payload(list),
                  });
                }}
              >
                Xác nhận chuyển vào Thùng rác
              </button>
            </div>
          </>
        )}
      </dialog>
      <dialog
        ref={dialog}
        onCancel={() => setPreview(null)}
        onClose={() => setPreview(null)}
        aria-labelledby="preview-title"
      >
        {preview && (
          <>
            <h2 id="preview-title">
              Xem trước{" "}
              {preview.request.mode === "inplace"
                ? "di chuyển tại chỗ"
                : "sao chép"}
            </h2>
            <p>
              {preview.plan.filter((p) => p.action !== "skip").length} file sẽ{" "}
              {preview.request.mode === "inplace"
                ? "đổi vị trí"
                : "được sao chép"}{" "}
              ·{" "}
              {formatBytes(
                preview.plan
                  .filter((p) => p.action !== "skip")
                  .reduce((n, p) => n + p.size, 0),
              )}
              . {preview.plan.filter((p) => p.action === "skip").length} file đã
              đúng vị trí.
            </p>
            <p className="destination-path">
              {preview.request.mode === "inplace"
                ? preview.request.source
                : preview.request.destination}
            </p>
            <div className="plan-list">
              <table>
                <thead>
                  <tr>
                    <th>Hiện tại</th>
                    <th>Sau sắp xếp</th>
                    <th>Dung lượng</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.plan.map((p) => (
                    <tr key={p.path}>
                      <td>{p.path}</td>
                      <td>
                        {p.action === "skip" ? "Giữ nguyên: " : ""}
                        {p.target}
                      </td>
                      <td>{formatBytes(p.size)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p>
              {preview.request.mode === "inplace"
                ? "Xác nhận sẽ di chuyển các file nguồn vào thư mục con. Không tạo thêm bản sao."
                : "Nguồn được giữ nguyên; file trùng tên được thêm số."}{" "}
              Hủy giữa chừng giữ kết quả đã hoàn tất; app quét lại sau khi di
              chuyển. Nhật ký được lưu trong thư mục đang sắp xếp.
            </p>
            <div className="dialog-actions">
              <button onClick={() => setPreview(null)}>Quay lại</button>
              <button
                className="primary"
                disabled={
                  busy || preview.plan.every((p) => p.action === "skip")
                }
                onClick={() => {
                  const current = preview;
                  setPreview(null);
                  void run({
                    ...current.request,
                    operation: "apply",
                    plan: current.plan,
                  });
                }}
              >
                Xác nhận{" "}
                {preview.request.mode === "inplace" ? "di chuyển" : "sao chép"}
              </button>
            </div>
          </>
        )}
      </dialog>
    </div>
  );
}
