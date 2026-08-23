// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * 跨页「缺失一键补齐」请求通道（瞬态，不持久化）：
 * workflow 引用面板点「去下载」→ SettingsDialog 切到模型库页 →
 * ModelLibrarySection 消费请求（切下载页签 + 预填搜索词 + 预选目标子目录）。
 */
import { create } from "zustand";

export interface DownloadRequest {
  query: string;
  /** 期望落盘子目录（checkpoints/loras/...），结果卡片预选 */
  subdir?: string;
  nonce: number;
}

interface DownloadRequestState {
  pending: DownloadRequest | null;
  requestDownload: (query: string, subdir?: string) => void;
  clear: () => void;
}

export const useDownloadRequestStore = create<DownloadRequestState>((set) => ({
  pending: null,
  requestDownload: (query, subdir) =>
    set({ pending: { query, subdir, nonce: Date.now() } }),
  clear: () => set({ pending: null }),
}));

/** 模型文件名 → Civitai 搜索词：去扩展名，-_ 转空格。 */
export function filenameToQuery(filename: string): string {
  const stem = filename.replace(/\.[^.]+$/, "");
  return stem.replace(/[-_]+/g, " ").trim();
}
