import { useCallback, useEffect, useRef, useState } from "react"

import { api } from "@/lib/api"
import type {
  LinkStatus,
  LiveData,
  ServerMessage,
  Settings,
  SwingListItem,
  SwingRecord,
  SwingSummary,
} from "@/lib/types"

const RECONNECT_MIN_MS = 500
const RECONNECT_MAX_MS = 5000
const LIVE_UI_INTERVAL_MS = 200
const LIVE_STALE_MS = 1500

export type SocketState = "connecting" | "open" | "closed"

export interface SwingServer {
  socket: SocketState
  status: LinkStatus
  simulate: boolean
  settings: Settings | null
  /** Latest live packet, updated ~30 times a second without re-rendering. */
  liveRef: React.RefObject<LiveData | null>
  /** The same data at a rate suited to text readouts. Null when the stream is stale. */
  live: LiveData | null
  swings: SwingListItem[]
  selected: SwingRecord | null
  /** Set between the sensor announcing a swing and its data arriving. */
  receiving: SwingSummary | null
  notice: string | null
  dismissNotice: () => void
  selectSwing: (id: string) => Promise<void>
  deleteSwing: (id: string) => Promise<void>
  saveSettings: (settings: Settings) => Promise<void>
  recalibrate: () => Promise<void>
  simulateSwing: () => Promise<void>
}

export function useSwingServer(): SwingServer {
  const [socket, setSocket] = useState<SocketState>("connecting")
  const [status, setStatus] = useState<LinkStatus>({ state: "starting" })
  const [simulate, setSimulate] = useState(false)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [live, setLive] = useState<LiveData | null>(null)
  const [swings, setSwings] = useState<SwingListItem[]>([])
  const [selected, setSelected] = useState<SwingRecord | null>(null)
  const [receiving, setReceiving] = useState<SwingSummary | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const liveRef = useRef<LiveData | null>(null)
  const liveAtRef = useRef(0)
  const selectedIdRef = useRef<string | null>(null)

  const report = useCallback((error: unknown, context: string) => {
    const detail = error instanceof Error ? error.message : String(error)
    setNotice(`${context}: ${detail}`)
  }, [])

  const refreshSwings = useCallback(async () => {
    try {
      const list = await api.listSwings()
      setSwings(list)
      const current = selectedIdRef.current
      if (current && list.some((s) => s.id === current)) {
        // Stored swings are re-analysed when the bat dimensions change.
        setSelected(await api.getSwing(current))
      } else if (list.length > 0) {
        selectedIdRef.current = list[0].id
        setSelected(await api.getSwing(list[0].id))
      } else {
        selectedIdRef.current = null
        setSelected(null)
      }
    } catch (error) {
      report(error, "Could not load swing history")
    }
  }, [report])

  useEffect(() => {
    let ws: WebSocket | null = null
    let retryTimer: number | undefined
    let retryDelay = RECONNECT_MIN_MS
    let disposed = false

    const handle = (message: ServerMessage) => {
      switch (message.type) {
        case "hello":
          setStatus(message.status)
          setSettings(message.settings)
          setSimulate(message.simulate)
          void refreshSwings()
          break
        case "status":
          setStatus(message.status)
          if (message.status.state !== "connected") {
            liveRef.current = null
            setReceiving(null)
          }
          break
        case "live":
          liveRef.current = message.live
          liveAtRef.current = performance.now()
          break
        case "summary":
          setReceiving(message.summary)
          break
        case "swing":
          setReceiving(null)
          selectedIdRef.current = message.swing.id
          setSelected(message.swing)
          setSwings((list) => [
            {
              id: message.swing.id,
              recorded_at: message.swing.recorded_at,
              device_swing_id: message.swing.device_swing_id,
              metrics: message.swing.metrics,
              confidence: message.swing.quality.confidence,
            },
            ...list.filter((s) => s.id !== message.swing.id),
          ])
          break
        case "swing_error":
          setReceiving(null)
          setNotice(`Swing ${message.swing_id} was lost: ${message.error}`)
          break
        case "settings":
          setSettings(message.settings)
          break
        case "swings_changed":
          void refreshSwings()
          break
      }
    }

    const connect = () => {
      if (disposed) return
      setSocket("connecting")
      const scheme = window.location.protocol === "https:" ? "wss" : "ws"
      ws = new WebSocket(`${scheme}://${window.location.host}/ws`)
      ws.onopen = () => {
        retryDelay = RECONNECT_MIN_MS
        setSocket("open")
      }
      ws.onmessage = (event) => {
        try {
          handle(JSON.parse(event.data as string) as ServerMessage)
        } catch (error) {
          console.error("Unreadable message from the server", error)
        }
      }
      ws.onclose = () => {
        if (disposed) return
        setSocket("closed")
        liveRef.current = null
        retryTimer = window.setTimeout(connect, retryDelay)
        retryDelay = Math.min(retryDelay * 2, RECONNECT_MAX_MS)
      }
    }
    connect()

    const liveTimer = window.setInterval(() => {
      const fresh = performance.now() - liveAtRef.current < LIVE_STALE_MS
      if (!fresh) liveRef.current = null
      setLive(liveRef.current)
    }, LIVE_UI_INTERVAL_MS)

    return () => {
      disposed = true
      window.clearTimeout(retryTimer)
      window.clearInterval(liveTimer)
      ws?.close()
    }
  }, [refreshSwings])

  const selectSwing = useCallback(
    async (id: string) => {
      try {
        const record = await api.getSwing(id)
        selectedIdRef.current = id
        setSelected(record)
      } catch (error) {
        report(error, "Could not open that swing")
      }
    },
    [report],
  )

  const deleteSwing = useCallback(
    async (id: string) => {
      try {
        await api.deleteSwing(id)
        if (selectedIdRef.current === id) selectedIdRef.current = null
        await refreshSwings()
      } catch (error) {
        report(error, "Could not delete that swing")
      }
    },
    [refreshSwings, report],
  )

  const saveSettings = useCallback(async (next: Settings) => {
    // Errors go back to the settings form, which shows them next to the fields.
    setSettings(await api.saveSettings(next))
  }, [])

  const recalibrate = useCallback(async () => {
    try {
      await api.recalibrate()
      setNotice("Recalibrating: rest the bat on something solid for a second.")
    } catch (error) {
      report(error, "Could not start calibration")
    }
  }, [report])

  const simulateSwing = useCallback(async () => {
    try {
      await api.simulateSwing()
    } catch (error) {
      report(error, "Could not simulate a swing")
    }
  }, [report])

  return {
    socket,
    status,
    simulate,
    settings,
    liveRef,
    live,
    swings,
    selected,
    receiving,
    notice,
    dismissNotice: () => setNotice(null),
    selectSwing,
    deleteSwing,
    saveSettings,
    recalibrate,
    simulateSwing,
  }
}
