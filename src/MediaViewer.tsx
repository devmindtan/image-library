import { useEffect, useRef, useState } from "react";
type Item = {
  path: string;
  kind: "image" | "video";
  size: number;
  category?: string;
};
export default function MediaViewer({
  item,
  url,
  detail,
  busy,
  onClose,
  onStep,
  onDelete,
}: {
  item: Item;
  url: string;
  detail: Record<string, unknown> | null;
  busy: boolean;
  onClose: () => void;
  onStep: (step: number) => void;
  onDelete: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [zoom, setZoom] = useState(100);
  const [error, setError] = useState("");
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  useEffect(() => {
    setZoom(100);
    setError("");
  }, [item.path]);
  return (
    <dialog
      className="viewer"
      ref={dialog}
      onCancel={onClose}
      onClose={onClose}
      aria-labelledby="viewer-title"
    >
      <div className="viewer-heading">
        <h2 id="viewer-title">{item.path}</h2>
        <button onClick={onClose}>Đóng</button>
      </div>
      <div className="viewer-controls">
        <button disabled={busy} onClick={() => onStep(-1)}>
          Trước
        </button>
        <button disabled={busy} onClick={() => onStep(1)}>
          Tiếp
        </button>
        {item.kind === "image" && (
          <>
            <button
              aria-label="Thu nhỏ"
              onClick={() => setZoom((z) => Math.max(50, z - 25))}
            >
              −
            </button>
            <span>{zoom}%</span>
            <button
              aria-label="Phóng to"
              onClick={() => setZoom((z) => Math.min(400, z + 25))}
            >
              +
            </button>
            <button onClick={() => setZoom(100)}>Vừa khung</button>
          </>
        )}
        <button className="danger" disabled={busy} onClick={onDelete}>
          Xóa file này
        </button>
      </div>
      <div className="viewer-media">
        {url ? (
          item.kind === "video" ? (
            <video
              key={url}
              src={url}
              controls
              playsInline
              preload="metadata"
              onError={() =>
                setError(
                  "Không phát được video này. Codec hoặc định dạng có thể không được WebView hỗ trợ; file vẫn được giữ nguyên.",
                )
              }
            />
          ) : (
            <img
              src={url}
              alt={item.path}
              style={{
                width: `${zoom}%`,
                maxWidth: zoom > 100 ? "none" : "100%",
                maxHeight: zoom > 100 ? "none" : "65vh",
              }}
              onError={() =>
                setError(
                  "Không đọc được ảnh đầy đủ. Hãy kiểm tra file hoặc quét lại.",
                )
              }
            />
          )
        ) : (
          <p>
            {busy
              ? "Đang mở file…"
              : "Không có bản xem trước. File minh họa hoặc không mở được file gốc."}
          </p>
        )}
      </div>
      {error && <p role="alert">{error}</p>}
      <details className="metadata" open>
        <summary>Thông tin file và metadata</summary>
        {detail ? (
          Object.entries(detail).map(([section, value]) => (
            <section key={section}>
              <h3>
                {{
                  general: "Thông tin chung",
                  image: "Thông tin ảnh",
                  exif: "EXIF",
                  video: "Thông tin video",
                  streams: "Luồng hình / âm thanh",
                  warnings: "Lưu ý",
                }[section] || section}
              </h3>
              {typeof value === "object" &&
              value !== null &&
              !Array.isArray(value) ? (
                <dl>
                  {Object.entries(value).map(([key, val]) => (
                    <div key={key}>
                      <dt>{key}</dt>
                      <dd>
                        {typeof val === "object"
                          ? JSON.stringify(val)
                          : String(val)}
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <pre>{JSON.stringify(value, null, 2)}</pre>
              )}
            </section>
          ))
        ) : (
          <p>
            {busy
              ? "Đang đọc metadata…"
              : "Không đọc được metadata. Xem thông báo trong thư viện."}
          </p>
        )}
      </details>
    </dialog>
  );
}
