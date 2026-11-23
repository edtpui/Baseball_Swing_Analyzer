import { useEffect, useMemo, useRef, useState } from "react"

import { fixed, niceScale, speed } from "@/lib/format"
import type { SpeedUnit, SwingRecord } from "@/lib/types"

interface SpeedChartProps {
  swing: SwingRecord
  unit: SpeedUnit
  /** Replay position as an index into the series, or null when not replaying. */
  cursorIndex: number | null
  onSeek: (index: number) => void
}

const HEIGHT = 232
const MARGIN = { top: 22, right: 58, bottom: 34, left: 40 }
const TABLE_STEP_MS = 20

export function SpeedChart({ swing, unit, cursorIndex, onSeek }: SpeedChartProps) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(640)
  const [hover, setHover] = useState<number | null>(null)

  useEffect(() => {
    const element = wrapRef.current
    if (!element) return
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(320, entry.contentRect.width)))
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  const model = useMemo(() => {
    const t = swing.series.t_ms
    const bat = swing.series.bat_speed_mps.map((v) => speed(v, unit))
    const hand = swing.series.hand_speed_mps.map((v) => speed(v, unit))
    const contact = swing.path.contact_index
    const y = niceScale(Math.max(...bat, ...hand), 4)
    return { t, bat, hand, contact, y }
  }, [swing, unit])

  const { t, bat, hand, contact, y } = model
  const plotW = width - MARGIN.left - MARGIN.right
  const plotH = HEIGHT - MARGIN.top - MARGIN.bottom
  const t0 = t[0]
  const t1 = t[t.length - 1]
  const xOf = (ms: number) => MARGIN.left + ((ms - t0) / (t1 - t0 || 1)) * plotW
  const yOf = (value: number) => MARGIN.top + plotH - (value / y.max) * plotH

  const path = (values: number[], from: number, to: number) =>
    values
      .slice(from, to + 1)
      .map((v, i) => `${i === 0 ? "M" : "L"}${xOf(t[from + i]).toFixed(1)},${yOf(v).toFixed(1)}`)
      .join("")

  const yTicks: number[] = []
  for (let v = 0; v <= y.max + 1e-9; v += y.step) yTicks.push(v)
  const xTicks: number[] = []
  for (let ms = Math.ceil(t0 / 50) * 50; ms <= t1; ms += 50) xTicks.push(ms)

  const nearestIndex = (clientX: number, rect: DOMRect) => {
    const ms = t0 + ((clientX - rect.left - MARGIN.left) / plotW) * (t1 - t0)
    let best = 0
    for (let i = 1; i < t.length; i++) {
      if (Math.abs(t[i] - ms) < Math.abs(t[best] - ms)) best = i
    }
    return best
  }

  const last = t.length - 1
  // Keep the two end labels from overlapping when the lines finish close together.
  let batLabelY = yOf(bat[last])
  let handLabelY = yOf(hand[last])
  if (Math.abs(batLabelY - handLabelY) < 14) {
    const mid = (batLabelY + handLabelY) / 2
    const batAbove = bat[last] >= hand[last]
    batLabelY = mid + (batAbove ? -7 : 7)
    handLabelY = mid + (batAbove ? 7 : -7)
  }

  const tooltipLeft = hover === null ? 0 : xOf(t[hover])
  const tooltipFlip = tooltipLeft > width - 190
  const contactLabel = swing.metrics.impact ? "Contact" : "Peak speed"

  const tableRows = useMemo(() => {
    const rows: number[] = []
    let next = Math.ceil(t[0] / TABLE_STEP_MS) * TABLE_STEP_MS
    t.forEach((ms, i) => {
      if (ms >= next - 1e-6) {
        rows.push(i)
        next += TABLE_STEP_MS
      }
    })
    return rows
  }, [t])

  return (
    <div>
      <div className="mb-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-secondary">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-0.5 w-4 rounded-full bg-series-bat" />
          Bat speed, sweet spot
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-0.5 w-4 rounded-full bg-series-hand" />
          Hand speed, grip
        </span>
        <span className="inline-flex items-center gap-1.5 text-ink-muted">
          <span className="w-4 border-t-2 border-dashed border-ink-muted" />
          After contact, estimated
        </span>
      </div>

      <div ref={wrapRef} className="relative">
        <svg
          width={width}
          height={HEIGHT}
          role="img"
          aria-label={`Bat speed and hand speed in ${unit} against time relative to contact`}
          className="block select-none"
          onPointerMove={(e) => setHover(nearestIndex(e.clientX, e.currentTarget.getBoundingClientRect()))}
          onPointerLeave={() => setHover(null)}
          onClick={(e) => onSeek(nearestIndex(e.clientX, e.currentTarget.getBoundingClientRect()))}
        >
          {yTicks.map((v) => (
            <g key={v}>
              <line
                x1={MARGIN.left}
                x2={MARGIN.left + plotW}
                y1={yOf(v)}
                y2={yOf(v)}
                className={v === 0 ? "stroke-line-strong" : "stroke-line"}
                strokeWidth={1}
              />
              <text x={MARGIN.left - 8} y={yOf(v)} dy="0.32em" textAnchor="end" className="fill-ink-muted text-2xs tabular-nums">
                {v}
              </text>
            </g>
          ))}
          <text x={MARGIN.left - 8} y={10} textAnchor="end" className="fill-ink-muted text-2xs">
            {unit}
          </text>

          {xTicks.map((ms) => (
            <text
              key={ms}
              x={xOf(ms)}
              y={MARGIN.top + plotH + 15}
              textAnchor="middle"
              className="fill-ink-muted text-2xs tabular-nums"
            >
              {ms > 0 ? `+${ms}` : ms === 0 ? "0" : `−${Math.abs(ms)}`}
            </text>
          ))}
          <text
            x={MARGIN.left + plotW / 2}
            y={HEIGHT - 3}
            textAnchor="middle"
            className="fill-ink-muted text-2xs"
          >
            milliseconds relative to {contactLabel.toLowerCase()}
          </text>

          <line
            x1={xOf(0)}
            x2={xOf(0)}
            y1={MARGIN.top}
            y2={MARGIN.top + plotH}
            className="stroke-ink-muted"
            strokeWidth={1}
            strokeDasharray="2 3"
          />
          <text x={xOf(0)} y={MARGIN.top - 8} textAnchor="middle" className="fill-ink-secondary text-2xs font-medium">
            {contactLabel}
          </text>

          <path d={path(hand, 0, contact)} fill="none" className="stroke-series-hand" strokeWidth={2} strokeLinejoin="round" />
          <path d={path(hand, contact, last)} fill="none" className="stroke-series-hand" strokeWidth={2} strokeDasharray="4 4" opacity={0.7} />
          <path d={path(bat, 0, contact)} fill="none" className="stroke-series-bat" strokeWidth={2} strokeLinejoin="round" />
          <path d={path(bat, contact, last)} fill="none" className="stroke-series-bat" strokeWidth={2} strokeDasharray="4 4" opacity={0.7} />

          <circle cx={xOf(0)} cy={yOf(bat[contact])} r={4} className="fill-series-bat stroke-surface" strokeWidth={2} />
          <circle cx={xOf(0)} cy={yOf(hand[contact])} r={4} className="fill-series-hand stroke-surface" strokeWidth={2} />

          <text x={MARGIN.left + plotW + 8} y={batLabelY} dy="0.32em" className="fill-ink-secondary text-2xs font-medium">
            Bat
          </text>
          <text x={MARGIN.left + plotW + 8} y={handLabelY} dy="0.32em" className="fill-ink-secondary text-2xs font-medium">
            Hand
          </text>

          {cursorIndex !== null && hover === null && (
            <line
              x1={xOf(t[cursorIndex])}
              x2={xOf(t[cursorIndex])}
              y1={MARGIN.top}
              y2={MARGIN.top + plotH}
              className="stroke-ink-secondary"
              strokeWidth={1}
            />
          )}

          {hover !== null && (
            <g pointerEvents="none">
              <line
                x1={xOf(t[hover])}
                x2={xOf(t[hover])}
                y1={MARGIN.top}
                y2={MARGIN.top + plotH}
                className="stroke-ink-secondary"
                strokeWidth={1}
              />
              <circle cx={xOf(t[hover])} cy={yOf(bat[hover])} r={4} className="fill-series-bat stroke-surface" strokeWidth={2} />
              <circle cx={xOf(t[hover])} cy={yOf(hand[hover])} r={4} className="fill-series-hand stroke-surface" strokeWidth={2} />
            </g>
          )}
        </svg>

        {hover !== null && (
          <div
            className="pointer-events-none absolute top-2 z-10 w-44 rounded-md border border-line-strong bg-surface px-2.5 py-2 text-xs shadow-sm"
            style={tooltipFlip ? { right: width - tooltipLeft + 10 } : { left: tooltipLeft + 10 }}
          >
            <div className="mb-1 tabular-nums text-ink-muted">
              {t[hover] === 0 ? contactLabel : `${fixed(Math.abs(t[hover]), 0)} ms ${t[hover] < 0 ? "before" : "after"}`}
              {hover > contact && " (estimated)"}
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="inline-flex items-center gap-1.5 text-ink-secondary">
                <span className="size-2 rounded-full bg-series-bat" />
                Bat
              </span>
              <span className="font-medium tabular-nums">
                {fixed(bat[hover])} {unit}
              </span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="inline-flex items-center gap-1.5 text-ink-secondary">
                <span className="size-2 rounded-full bg-series-hand" />
                Hand
              </span>
              <span className="font-medium tabular-nums">
                {fixed(hand[hover])} {unit}
              </span>
            </div>
          </div>
        )}
      </div>

      <details className="mt-1 text-xs">
        <summary className="w-fit text-ink-muted hover:text-ink-secondary">Show as table</summary>
        <div className="mt-2 max-h-48 overflow-auto rounded-md border border-line">
          <table className="w-full tabular-nums">
            <thead className="sticky top-0 bg-surface-sunken text-left text-ink-secondary">
              <tr>
                <th className="px-2 py-1 font-medium">Time (ms)</th>
                <th className="px-2 py-1 text-right font-medium">Bat ({unit})</th>
                <th className="px-2 py-1 text-right font-medium">Hand ({unit})</th>
              </tr>
            </thead>
            <tbody>
              {tableRows.map((i) => (
                <tr key={i} className="border-t border-line">
                  <td className="px-2 py-0.5">{fixed(t[i], 0)}</td>
                  <td className="px-2 py-0.5 text-right">{fixed(bat[i])}</td>
                  <td className="px-2 py-0.5 text-right">{fixed(hand[i])}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  )
}
