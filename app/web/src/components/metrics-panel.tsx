import { CheckIcon, WarningIcon } from "@/components/icons"
import { fixed, signed, speed } from "@/lib/format"
import type { SpeedUnit, SwingListItem, SwingRecord, SwingSummary } from "@/lib/types"

interface MetricsPanelProps {
  swing: SwingRecord | null
  swings: SwingListItem[]
  unit: SpeedUnit
  receiving: SwingSummary | null
}

function Tile({ label, value, unit, hint }: { label: string; value: string; unit?: string; hint?: string }) {
  return (
    <div className="bg-surface px-3 py-2.5">
      <div className="text-xs text-ink-secondary">{label}</div>
      <div className="mt-0.5 flex items-baseline gap-1">
        <span className="text-2xl font-semibold tabular-nums tracking-tight">{value}</span>
        {unit && <span className="text-xs text-ink-muted">{unit}</span>}
      </div>
      {hint && <div className="text-2xs text-ink-muted">{hint}</div>}
    </div>
  )
}

export function MetricsPanel({ swing, swings, unit, receiving }: MetricsPanelProps) {
  if (!swing) {
    return (
      <section className="panel p-4">
        <h2 className="panel-title">Last swing</h2>
        <p className="mt-3 text-ink-secondary">
          {receiving ? "Swing detected. Receiving data from the sensor…" : "No swings yet. Take a swing and the numbers appear here."}
        </p>
      </section>
    )
  }

  const m = swing.metrics
  const batSpeed = speed(m.bat_speed_mps, unit)
  const others = swings.filter((s) => s.id !== swing.id)
  const average = others.length ? others.reduce((sum, s) => sum + speed(s.metrics.bat_speed_mps, unit), 0) / others.length : null
  const reduced = swing.quality.confidence === "reduced"

  return (
    <section className="panel overflow-hidden">
      <div className="p-4">
        <div className="flex items-center justify-between">
          <h2 className="panel-title">Bat speed</h2>
          {receiving && <span className="text-xs text-ink-muted">Receiving next swing…</span>}
        </div>
        <div className="mt-1 flex items-baseline gap-2">
          <span className="text-hero font-semibold tabular-nums tracking-tighter">{fixed(batSpeed)}</span>
          <span className="text-lg text-ink-secondary">{unit}</span>
        </div>
        <div className="mt-1 text-xs text-ink-secondary">
          {m.impact ? "At the sweet spot, at contact" : "Peak at the sweet spot, no contact detected"}
          {average !== null && (
            <span className="text-ink-muted">
              {" · "}
              <span className="tabular-nums">{signed(batSpeed - average)}</span> vs the other {others.length} swing
              {others.length === 1 ? "" : "s"}
            </span>
          )}
        </div>

        <div
          className={`mt-3 flex items-start gap-2 rounded-md border px-2.5 py-2 text-xs ${
            reduced ? "border-warning/40" : "border-line"
          }`}
        >
          {reduced ? (
            <WarningIcon className="mt-px shrink-0 text-warning" />
          ) : (
            <CheckIcon className="mt-px shrink-0 text-good" />
          )}
          <div>
            <div className="font-medium">{reduced ? "Reduced confidence" : "Clean capture"}</div>
            {reduced && (
              <ul className="mt-0.5 text-ink-secondary">
                {swing.quality.reasons.map((reason) => (
                  <li key={reason}>{reason.charAt(0).toUpperCase() + reason.slice(1)}</li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-px border-t border-line bg-line">
        <Tile label="Peak hand speed" value={fixed(speed(m.peak_hand_speed_mps, unit))} unit={unit} />
        <Tile
          label="Attack angle"
          value={`${signed(m.attack_angle_deg)}°`}
          hint={m.attack_angle_deg >= 0 ? "Barrel moving upward" : "Barrel moving downward"}
        />
        <Tile
          label="Vertical bat angle"
          value={`${signed(m.vertical_bat_angle_deg)}°`}
          hint={m.vertical_bat_angle_deg <= 0 ? "Barrel below the hands" : "Barrel above the hands"}
        />
        <Tile label="Time to contact" value={fixed(m.time_to_contact_ms, 0)} unit="ms" />
        <Tile label="Peak rotation" value={fixed(m.peak_omega_dps, 0)} unit="°/s" />
        <Tile label="Swing side" value={m.handedness === "R" ? "Right" : "Left"} hint="From rotation direction" />
      </div>
    </section>
  )
}
