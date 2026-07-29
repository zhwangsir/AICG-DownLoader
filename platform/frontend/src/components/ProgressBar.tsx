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
    <div className="progress-bar-container">
      <div className="progress-bar-header">
        <span>
          {connected ? "● 已连接" : "○ 连接中"} · {message || status}
        </span>
        <span>{percent}%</span>
      </div>
      <div className="progress-bar-track">
        <div
          className={`progress-bar-fill${isFailed ? " failed" : ""}${isCompleted ? " completed" : ""}`}
          style={{ width: `${percent}%` }}
        />
      </div>
      {error && (
        <div className="progress-bar-error">
          {error}
        </div>
      )}
    </div>
  );
}
