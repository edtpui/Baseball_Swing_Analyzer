import type { Settings, SwingListItem, SwingRecord } from "./types"

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (typeof body?.detail === "string") detail = body.detail
      else if (Array.isArray(body?.detail) && body.detail[0]?.msg) detail = body.detail[0].msg
    } catch {
      // The error body was not JSON; the status line is the best description available.
    }
    throw new Error(detail)
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T)
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
})

export const api = {
  listSwings: () => request<SwingListItem[]>("/api/swings"),
  getSwing: (id: string) => request<SwingRecord>(`/api/swings/${encodeURIComponent(id)}`),
  deleteSwing: (id: string) => request<void>(`/api/swings/${encodeURIComponent(id)}`, { method: "DELETE" }),
  saveSettings: (settings: Settings) => request<Settings>("/api/settings", json("PUT", settings)),
  recalibrate: () => request<{ ok: boolean }>("/api/device/recalibrate", { method: "POST" }),
  simulateSwing: () => request<{ ok: boolean }>("/api/sim/swing", { method: "POST" }),
}
