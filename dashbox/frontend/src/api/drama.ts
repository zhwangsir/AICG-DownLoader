// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * Platform short-drama module (platform/ FastAPI, default :8100).
 *
 * Browser stays same-origin: DashBox web (:8080) nginx-proxies /api/drama/*
 * to DashBox API (:8780), which reverse-proxies to ST_DRAMA_API_URL.
 * Do not point the SPA at :8100 directly — CSP connect-src is 'self'.
 */

export interface DramaHealth {
  status: string;
  version?: string;
  [key: string]: unknown;
}

export function dramaApiPath(suffix: string): string {
  const cleaned = suffix.replace(/^\/+/, "");
  return cleaned ? `/api/drama/${cleaned}` : "/api/drama";
}

export async function getDramaHealth(signal?: AbortSignal): Promise<DramaHealth> {
  const response = await fetch(dramaApiPath("health"), {
    credentials: "include",
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw new Error(`drama health HTTP ${response.status}`);
  }
  return (await response.json()) as DramaHealth;
}
