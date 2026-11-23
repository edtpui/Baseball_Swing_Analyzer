import type { Quaternion, SpeedUnit, Vec3 } from "./types"

const MPS_TO_MPH = 2.2369363
const MPS_TO_KMH = 3.6
export const METRES_PER_INCH = 0.0254

export function speed(mps: number, unit: SpeedUnit): number {
  return mps * (unit === "mph" ? MPS_TO_MPH : MPS_TO_KMH)
}

export function fixed(value: number, digits = 1): string {
  // Avoid "-0.0" for small negative angles.
  const text = value.toFixed(digits)
  return Number(text) === 0 ? (0).toFixed(digits) : text
}

export function signed(value: number, digits = 1): string {
  const text = fixed(value, digits)
  return value > 0 && Number(text) !== 0 ? `+${text}` : text
}

export function clockTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
}

export function dayLabel(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })
}

/** Unit vector from knob to barrel in the world frame (Z up) for a sensor quaternion. */
export function batDirection(q: Quaternion, sensorFacesOut: boolean): Vec3 {
  const [w, x, y, z] = q
  const s = sensorFacesOut ? -1 : 1
  return [s * 2 * (x * z + w * y), s * 2 * (y * z - w * x), s * (1 - 2 * (x * x + y * y))]
}

export function rotateAboutVertical(v: Vec3, angle: number): Vec3 {
  const c = Math.cos(angle)
  const s = Math.sin(angle)
  return [c * v[0] - s * v[1], s * v[0] + c * v[1], v[2]]
}

/** Picks a round axis maximum and step so ticks land on clean numbers. */
export function niceScale(maxValue: number, targetTicks = 5): { max: number; step: number } {
  if (!(maxValue > 0)) return { max: 1, step: 1 }
  const rough = maxValue / targetTicks
  const magnitude = 10 ** Math.floor(Math.log10(rough))
  const normalised = rough / magnitude
  const step = (normalised <= 1 ? 1 : normalised <= 2 ? 2 : normalised <= 5 ? 5 : 10) * magnitude
  return { max: Math.ceil(maxValue / step) * step, step }
}
