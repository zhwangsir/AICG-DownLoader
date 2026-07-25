import { useEffect, useRef, useState } from "react";
import type { ProgressEvent } from "../api/client";

export interface UseProgressState {
  connected: boolean;
  status: ProgressEvent["status"] | null;
  percent: number;
  message: string;
  result: unknown;
  error: string | null;
}

export function useProgress(streamUrl: string | null) {
  const [state, setState] = useState<UseProgressState>({
    connected: false,
    status: null,
    percent: 0,
    message: "",
    result: null,
    error: null,
  });
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!streamUrl) {
      return;
    }

    const es = new EventSource(streamUrl);
    eventSourceRef.current = es;

    es.onopen = () => {
      setState((prev) => ({ ...prev, connected: true }));
    };

    es.onmessage = (event) => {
      if (!event.data || event.data.startsWith(":heartbeat")) {
        return;
      }
      try {
        const data: ProgressEvent = JSON.parse(event.data);
        setState({
          connected: true,
          status: data.status,
          percent: data.percent,
          message: data.message,
          result: data.result,
          error: data.error,
        });

        if (data.status === "completed" || data.status === "failed") {
          setTimeout(() => es.close(), 500);
        }
      } catch {
        // 忽略非 JSON 心跳或异常消息
      }
    };

    es.onerror = () => {
      setState((prev) => ({ ...prev, connected: false }));
      // EventSource 会自动重连；若任务已完成则关闭
    };

    return () => {
      es.close();
      eventSourceRef.current = null;
    };
  }, [streamUrl]);

  const reset = () => {
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
    setState({
      connected: false,
      status: null,
      percent: 0,
      message: "",
      result: null,
      error: null,
    });
  };

  return { ...state, reset };
}
