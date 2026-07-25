import type { UseProgressState } from "../hooks/useProgress";

export function ProgressBar({
  connected,
  status,
  percent,
  message,
  error,
}: UseProgressState) {
  if (!status) return null;

  const isFailed = status === "failed";
  const isCompleted = status === "completed";

  return (
    <div style={{ marginTop: "12px" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          fontSize: "11px",
          color: "var(--text-secondary)",
          marginBottom: "4px",
        }}
      >
        <span>
          {connected ? "● 已连接" : "○ 连接中"} · {message || status}
        </span>
        <span>{percent}%</span>
      </div>
      <div
        style={{
          width: "100%",
          height: "6px",
          background: "var(--bg-primary)",
          borderRadius: "3px",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${percent}%`,
            height: "100%",
            background: isFailed
              ? "#a55"
              : isCompleted
              ? "#4a5"
              : "var(--accent)",
            transition: "width 0.3s ease",
          }}
        />
      </div>
      {error && (
        <div style={{ color: "#a55", fontSize: "12px", marginTop: "6px" }}>
          {error}
        </div>
      )}
    </div>
  );
}
