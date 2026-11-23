import { CheckIcon, GearIcon, MoonIcon, OfflineIcon, SearchIcon, SunIcon, WarningIcon } from "@/components/icons"
import type { SocketState } from "@/hooks/use-swing-server"
import type { LinkStatus, LiveData, SpeedUnit, Theme } from "@/lib/types"

interface SiteHeaderProps {
  socket: SocketState
  status: LinkStatus
  live: LiveData | null
  simulate: boolean
  unit: SpeedUnit
  theme: Theme
  onUnit: (unit: SpeedUnit) => void
  onTheme: (theme: Theme) => void
  onRecalibrate: () => void
  onSimulateSwing: () => void
  onOpenSettings: () => void
}

interface Badge {
  label: string
  detail?: string
  tone: "good" | "warning" | "critical" | "neutral"
  icon: React.ReactNode
}

function linkBadge(socket: SocketState, status: LinkStatus, live: LiveData | null): Badge {
  if (socket !== "open") {
    return {
      label: socket === "connecting" ? "Connecting to the app server" : "App server offline",
      detail: socket === "closed" ? "Start it with: python -m server" : undefined,
      tone: socket === "connecting" ? "neutral" : "critical",
      icon: <OfflineIcon />,
    }
  }
  switch (status.state) {
    case "connected":
      if (live?.imu_fault) {
        return { label: "Sensor fault", detail: "The motion chip is not answering", tone: "critical", icon: <WarningIcon /> }
      }
      return { label: "Sensor connected", detail: status.name, tone: "good", icon: <CheckIcon /> }
    case "connecting":
      return { label: "Connecting to sensor", detail: status.name, tone: "neutral", icon: <SearchIcon /> }
    case "disconnected":
      return { label: "Sensor disconnected", detail: status.error ?? "Retrying", tone: "warning", icon: <WarningIcon /> }
    default:
      return { label: "Searching for sensor", detail: "Switch the sensor on", tone: "neutral", icon: <SearchIcon /> }
  }
}

const TONE_CLASS: Record<Badge["tone"], string> = {
  good: "text-good",
  warning: "text-warning",
  critical: "text-critical",
  neutral: "text-ink-muted",
}

const STATE_LABEL: Record<LiveData["state"], string> = {
  idle: "Ready",
  swinging: "Swing in progress",
  settling: "Settling",
  unknown: "Unknown",
}

export function SiteHeader(props: SiteHeaderProps) {
  const { socket, status, live, simulate, unit, theme } = props
  const badge = linkBadge(socket, status, live)
  const connected = socket === "open" && status.state === "connected"

  return (
    <header className="panel flex flex-wrap items-center gap-x-5 gap-y-2 px-4 py-2.5">
      <h1 className="text-base font-semibold tracking-tight">Swing Analyzer</h1>

      <div className="flex items-center gap-2" role="status">
        <span className={TONE_CLASS[badge.tone]}>{badge.icon}</span>
        <span className="font-medium">{badge.label}</span>
        {badge.detail && <span className="max-w-72 truncate text-xs text-ink-muted">{badge.detail}</span>}
      </div>

      {connected && live && !live.imu_fault && (
        <dl className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-secondary">
          <div className="flex gap-1.5">
            <dt className="text-ink-muted">State</dt>
            <dd className="font-medium">{live.transferring ? "Sending swing" : STATE_LABEL[live.state]}</dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-ink-muted">Rotation</dt>
            <dd className="w-16 font-medium tabular-nums">{live.gyro_dps.toFixed(0)} °/s</dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-ink-muted">Sampling</dt>
            <dd className="font-medium tabular-nums">{live.sample_rate_hz.toFixed(0)} Hz</dd>
          </div>
          <div className="flex items-center gap-1.5">
            <dt className="text-ink-muted">Gyro</dt>
            <dd className="inline-flex items-center gap-1 font-medium">
              {live.calibrated ? (
                "Calibrated"
              ) : (
                <>
                  <WarningIcon className="size-3.5 text-warning" />
                  Not calibrated: rest the bat
                </>
              )}
            </dd>
          </div>
        </dl>
      )}

      <div className="ml-auto flex flex-wrap items-center gap-2">
        {simulate && (
          <>
            <span className="rounded-sm border border-line-strong px-1.5 py-0.5 text-2xs font-semibold tracking-wide text-ink-secondary uppercase">
              Simulated sensor
            </span>
            <button type="button" className="btn btn-primary" onClick={props.onSimulateSwing} disabled={!connected}>
              Simulate swing
            </button>
          </>
        )}
        <button type="button" className="btn" onClick={props.onRecalibrate} disabled={!connected}>
          Calibrate gyro
        </button>

        <div className="inline-flex overflow-hidden rounded-md border border-line-strong" role="group" aria-label="Speed unit">
          {(["mph", "km/h"] as const).map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={unit === option}
              onClick={() => props.onUnit(option)}
              className={`h-[1.875rem] px-2.5 text-xs font-medium ${
                unit === option ? "bg-accent text-accent-ink" : "bg-surface text-ink-secondary hover:bg-surface-hover"
              }`}
            >
              {option}
            </button>
          ))}
        </div>

        <button
          type="button"
          className="btn btn-icon"
          aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          onClick={() => props.onTheme(theme === "dark" ? "light" : "dark")}
        >
          {theme === "dark" ? <SunIcon /> : <MoonIcon />}
        </button>
        <button type="button" className="btn btn-icon" aria-label="Settings" onClick={props.onOpenSettings}>
          <GearIcon />
        </button>
      </div>
    </header>
  )
}
