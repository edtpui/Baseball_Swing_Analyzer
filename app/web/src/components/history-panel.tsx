import { useState } from "react"

import { TrashIcon, WarningIcon } from "@/components/icons"
import { clockTime, dayLabel, fixed, signed, speed } from "@/lib/format"
import type { SpeedUnit, SwingListItem } from "@/lib/types"

interface HistoryPanelProps {
  swings: SwingListItem[]
  selectedId: string | null
  unit: SpeedUnit
  onSelect: (id: string) => void
  onDelete: (id: string) => void
}

export function HistoryPanel({ swings, selectedId, unit, onSelect, onDelete }: HistoryPanelProps) {
  const [confirming, setConfirming] = useState<string | null>(null)

  const speeds = swings.map((s) => speed(s.metrics.bat_speed_mps, unit))
  const average = speeds.length ? speeds.reduce((a, b) => a + b, 0) / speeds.length : 0
  const best = speeds.length ? Math.max(...speeds) : 0
  const today = new Date().toDateString()

  return (
    <section className="panel flex min-h-0 flex-col overflow-hidden">
      <div className="flex items-baseline justify-between px-4 pt-4 pb-3">
        <h2 className="panel-title">Swings</h2>
        {swings.length > 0 && (
          <span className="text-xs tabular-nums text-ink-secondary">
            {swings.length} recorded · avg {fixed(average)} · best {fixed(best)} {unit}
          </span>
        )}
      </div>

      {swings.length === 0 ? (
        <p className="px-4 pb-4 text-ink-secondary">Recorded swings are listed here and kept between sessions.</p>
      ) : (
        <div className="max-h-[26rem] overflow-auto border-t border-line">
          <table className="w-full text-xs tabular-nums">
            <thead className="sticky top-0 z-10 bg-surface-sunken text-ink-secondary">
              <tr>
                <th className="py-1.5 pr-2 pl-4 text-left font-medium">Time</th>
                <th className="px-2 py-1.5 text-right font-medium">Bat ({unit})</th>
                <th className="px-2 py-1.5 text-right font-medium">Attack</th>
                <th className="px-2 py-1.5 text-right font-medium">To contact</th>
                <th className="w-9 py-1.5 pr-3 pl-1">
                  <span className="sr-only">Delete</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {swings.map((s) => {
                const selected = s.id === selectedId
                const isToday = new Date(s.recorded_at).toDateString() === today
                return (
                  <tr
                    key={s.id}
                    onClick={() => onSelect(s.id)}
                    aria-selected={selected}
                    className={`cursor-pointer border-t border-line ${selected ? "bg-accent-subtle" : "hover:bg-surface-hover"}`}
                  >
                    <td className={`py-1.5 pr-2 pl-4 ${selected ? "shadow-[inset_3px_0_0_var(--accent)]" : ""}`}>
                      <button type="button" className="text-left" onClick={() => onSelect(s.id)}>
                        {clockTime(s.recorded_at)}
                        {!isToday && <span className="ml-1.5 text-ink-muted">{dayLabel(s.recorded_at)}</span>}
                      </button>
                      {s.confidence === "reduced" && (
                        <WarningIcon
                          className="ml-1.5 inline size-3.5 align-[-2px] text-warning"
                          aria-label="Reduced confidence"
                          aria-hidden={false}
                          role="img"
                        />
                      )}
                      {!s.metrics.impact && <span className="ml-1.5 text-ink-muted">no contact</span>}
                    </td>
                    <td className="px-2 py-1.5 text-right font-medium">{fixed(speed(s.metrics.bat_speed_mps, unit))}</td>
                    <td className="px-2 py-1.5 text-right">{signed(s.metrics.attack_angle_deg)}°</td>
                    <td className="px-2 py-1.5 text-right">{fixed(s.metrics.time_to_contact_ms, 0)} ms</td>
                    <td className="py-1 pr-3 pl-1 text-right" onClick={(e) => e.stopPropagation()}>
                      {confirming === s.id ? (
                        <span className="inline-flex gap-1">
                          <button
                            type="button"
                            className="rounded-sm px-1 font-medium text-critical hover:underline"
                            onClick={() => {
                              setConfirming(null)
                              onDelete(s.id)
                            }}
                          >
                            Delete
                          </button>
                          <button
                            type="button"
                            className="rounded-sm px-1 text-ink-secondary hover:underline"
                            onClick={() => setConfirming(null)}
                          >
                            Keep
                          </button>
                        </span>
                      ) : (
                        <button
                          type="button"
                          className="rounded-sm p-1 text-ink-muted hover:bg-surface-hover hover:text-ink"
                          aria-label={`Delete swing recorded at ${clockTime(s.recorded_at)}`}
                          onClick={() => setConfirming(s.id)}
                        >
                          <TrashIcon />
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
