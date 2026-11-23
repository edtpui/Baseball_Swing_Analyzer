import { useCallback, useEffect, useRef, useState } from "react"

import { BatScene, type Playback } from "@/components/bat-scene"
import { HistoryPanel } from "@/components/history-panel"
import { CloseIcon, PauseIcon, PlayIcon } from "@/components/icons"
import { MetricsPanel } from "@/components/metrics-panel"
import { SettingsDialog } from "@/components/settings-dialog"
import { SiteHeader } from "@/components/site-header"
import { SpeedChart } from "@/components/speed-chart"
import { useSwingServer } from "@/hooks/use-swing-server"
import { batDirection, clockTime } from "@/lib/format"
import type { SpeedUnit, Theme } from "@/lib/types"

const SPEEDS = [
  { value: 1, label: "Real time" },
  { value: 0.25, label: "1/4 speed" },
  { value: 0.1, label: "1/10 speed" },
]
const DEFAULT_SPEED = 0.25
const DEFAULT_BAT_LENGTH_M = 0.838

function stored<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const value = localStorage.getItem(key)
    return allowed.includes(value as T) ? (value as T) : fallback
  } catch {
    return fallback
  }
}

function remember(key: string, value: string) {
  try {
    localStorage.setItem(key, value)
  } catch {
    // Private browsing: the choice simply lasts for this visit.
  }
}

export default function App() {
  const server = useSwingServer()
  const { selected, settings } = server

  const [unit, setUnit] = useState<SpeedUnit>(() => stored("swing.unit", ["mph", "km/h"] as const, "mph"))
  const [theme, setTheme] = useState<Theme>(() => (document.documentElement.classList.contains("dark") ? "dark" : "light"))
  const [mode, setMode] = useState<"live" | "replay">("live")
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [playing, setPlaying] = useState(true)
  const [playSpeed, setPlaySpeed] = useState(DEFAULT_SPEED)
  const [cursor, setCursor] = useState(0)

  const playbackRef = useRef<Playback>({ index: 0, playing: true, speed: DEFAULT_SPEED })
  const yawOffsetRef = useRef(0)

  // The class is switched before React re-renders so the 3D scene, which reads
  // its colours from the CSS variables, sees the new theme.
  const applyTheme = (next: Theme) => {
    document.documentElement.classList.toggle("dark", next === "dark")
    remember("swing.theme", next)
    setTheme(next)
  }

  useEffect(() => remember("swing.unit", unit), [unit])

  // A newly selected swing plays from the start.
  const selectedId = selected?.id ?? null
  useEffect(() => {
    playbackRef.current.index = 0
    playbackRef.current.playing = true
    setPlaying(true)
    setCursor(0)
    setMode(selectedId ? "replay" : "live")
  }, [selectedId])

  // Mirror the animation position into React at a rate suited to the scrubber.
  useEffect(() => {
    if (mode !== "replay") return
    const timer = window.setInterval(() => setCursor(playbackRef.current.index), 40)
    return () => window.clearInterval(timer)
  }, [mode])

  const lastIndex = selected ? selected.path.knob.length - 1 : 0

  const seek = useCallback((index: number) => {
    playbackRef.current.index = index
    playbackRef.current.playing = false
    setPlaying(false)
    setCursor(index)
  }, [])

  const togglePlay = () => {
    const next = !playing
    if (next && playbackRef.current.index >= lastIndex) playbackRef.current.index = 0
    playbackRef.current.playing = next
    setPlaying(next)
  }

  const changeSpeed = (value: number) => {
    playbackRef.current.speed = value
    setPlaySpeed(value)
  }

  const faceForward = () => {
    const live = server.liveRef.current
    if (!live) return
    const d = batDirection(live.q, settings?.sensor_faces_out ?? true)
    yawOffsetRef.current = Math.atan2(d[1], d[0])
  }

  const sensorLive = server.socket === "open" && server.status.state === "connected" && server.live !== null
  const cursorIndex = Math.min(lastIndex, Math.max(0, Math.round(cursor)))
  const cursorMs = selected ? selected.series.t_ms[cursorIndex] : 0

  return (
    <div className="mx-auto flex min-h-screen max-w-[1680px] flex-col gap-3 p-3 lg:p-4">
      <SiteHeader
        socket={server.socket}
        status={server.status}
        live={server.live}
        simulate={server.simulate}
        unit={unit}
        theme={theme}
        onUnit={setUnit}
        onTheme={applyTheme}
        onRecalibrate={() => void server.recalibrate()}
        onSimulateSwing={() => void server.simulateSwing()}
        onOpenSettings={() => setSettingsOpen(true)}
      />

      {server.notice && (
        <div role="status" className="panel flex items-center justify-between gap-3 px-4 py-2 text-sm">
          <span>{server.notice}</span>
          <button type="button" className="btn btn-icon border-transparent" aria-label="Dismiss" onClick={server.dismissNotice}>
            <CloseIcon />
          </button>
        </div>
      )}

      <main className="grid flex-1 items-start gap-3 lg:gap-4 xl:grid-cols-[minmax(0,1fr)_25rem]">
        <div className="flex min-w-0 flex-col gap-3 lg:gap-4">
          <section className="panel overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2.5">
              <div className="flex items-center gap-3">
                <div className="inline-flex overflow-hidden rounded-md border border-line-strong" role="group" aria-label="View">
                  {(["live", "replay"] as const).map((option) => (
                    <button
                      key={option}
                      type="button"
                      aria-pressed={mode === option}
                      disabled={option === "replay" && !selected}
                      onClick={() => setMode(option)}
                      className={`h-[1.875rem] px-3 text-xs font-medium disabled:opacity-50 ${
                        mode === option ? "bg-accent text-accent-ink" : "bg-surface text-ink-secondary hover:bg-surface-hover"
                      }`}
                    >
                      {option === "live" ? "Live" : "Swing replay"}
                    </button>
                  ))}
                </div>
                <span className="text-xs text-ink-secondary">
                  {mode === "live"
                    ? "Bat orientation from the sensor, as it moves"
                    : selected && `Swing recorded at ${clockTime(selected.recorded_at)}`}
                </span>
              </div>
              {mode === "live" && (
                <button type="button" className="btn" onClick={faceForward} disabled={!sensorLive}>
                  Face forward
                </button>
              )}
            </div>

            <div className="relative h-[26rem] bg-surface-sunken 2xl:h-[30rem]">
              <BatScene
                mode={mode}
                liveRef={server.liveRef}
                swing={selected}
                batLengthM={settings?.bat_length_m ?? DEFAULT_BAT_LENGTH_M}
                sensorFacesOut={settings?.sensor_faces_out ?? true}
                theme={theme}
                playbackRef={playbackRef}
                yawOffsetRef={yawOffsetRef}
              />

              {mode === "live" && !sensorLive && (
                <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                  <p className="rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink-secondary">
                    Waiting for the sensor. The bat moves here as soon as it connects.
                  </p>
                </div>
              )}

              {mode === "replay" && (
                <ul className="pointer-events-none absolute top-3 left-3 flex flex-col gap-1 rounded-md border border-line bg-surface/90 px-2.5 py-2 text-xs text-ink-secondary">
                  <li className="flex items-center gap-2">
                    <span className="h-0.5 w-4 rounded-full bg-series-bat" />
                    Sweet spot path
                  </li>
                  <li className="flex items-center gap-2">
                    <span className="h-0.5 w-4 rounded-full bg-series-hand" />
                    Knob path
                  </li>
                  <li className="flex items-center gap-2 text-ink-muted">
                    <span className="w-4 border-t-2 border-dashed border-ink-muted" />
                    After contact, estimated
                  </li>
                  <li className="text-ink-muted">Ground arrow: barrel travel at contact</li>
                </ul>
              )}
              <p className="pointer-events-none absolute right-3 bottom-2 text-2xs text-ink-muted">
                Drag to orbit · scroll to zoom · height above ground is illustrative
              </p>
            </div>

            {mode === "replay" && selected && (
              <div className="flex flex-wrap items-center gap-3 border-t border-line px-4 py-2.5">
                <button type="button" className="btn btn-icon" aria-label={playing ? "Pause" : "Play"} onClick={togglePlay}>
                  {playing ? <PauseIcon /> : <PlayIcon />}
                </button>
                <input
                  type="range"
                  aria-label="Replay position"
                  className="h-1.5 min-w-40 flex-1 accent-accent"
                  min={0}
                  max={lastIndex}
                  step={0.25}
                  value={Math.min(cursor, lastIndex)}
                  onChange={(e) => seek(Number(e.target.value))}
                />
                <span className="w-36 text-right text-xs tabular-nums text-ink-secondary">
                  {cursorMs === 0
                    ? "at contact"
                    : `${Math.abs(cursorMs).toFixed(0)} ms ${cursorMs < 0 ? "before" : "after"} contact`}
                </span>
                <select
                  aria-label="Replay speed"
                  className="field w-28"
                  value={playSpeed}
                  onChange={(e) => changeSpeed(Number(e.target.value))}
                >
                  {SPEEDS.map((s) => (
                    <option key={s.value} value={s.value}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </div>
            )}
          </section>

          <section className="panel p-4">
            <h2 className="panel-title mb-3">Speed through the swing</h2>
            {selected ? (
              <SpeedChart swing={selected} unit={unit} cursorIndex={mode === "replay" ? cursorIndex : null} onSeek={seek} />
            ) : (
              <p className="text-ink-secondary">Bat and hand speed are plotted here for each swing.</p>
            )}
          </section>
        </div>

        <div className="flex min-w-0 flex-col gap-3 lg:gap-4">
          <MetricsPanel swing={selected} swings={server.swings} unit={unit} receiving={server.receiving} />
          <HistoryPanel
            swings={server.swings}
            selectedId={selectedId}
            unit={unit}
            onSelect={(id) => void server.selectSwing(id)}
            onDelete={(id) => void server.deleteSwing(id)}
          />
        </div>
      </main>

      {settings && (
        <SettingsDialog
          open={settingsOpen}
          settings={settings}
          onClose={() => setSettingsOpen(false)}
          onSave={server.saveSettings}
        />
      )}
    </div>
  )
}
