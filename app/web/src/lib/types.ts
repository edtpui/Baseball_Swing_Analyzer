// Shapes of the messages and records the Python server sends (app/server).

export interface LinkStatus {
  state: "starting" | "scanning" | "connecting" | "connected" | "disconnected"
  name?: string
  address?: string
  error?: string | null
  info?: Record<string, string>
  mtu?: number
}

export type Quaternion = [number, number, number, number]

export interface LiveData {
  state: "idle" | "swinging" | "settling" | "unknown"
  calibrated: boolean
  transferring: boolean
  overrun: boolean
  imu_fault: boolean
  seq: number
  q: Quaternion
  gyro_dps: number
  accel_g: number
  sample_rate_hz: number
  swing_count: number
}

export interface Settings {
  bat_length_m: number
  sweet_spot_from_tip_m: number
  sensor_faces_out: boolean
  start_dps: number
  min_peak_dps: number
}

export interface Metrics {
  bat_speed_mps: number
  peak_bat_speed_mps: number
  peak_hand_speed_mps: number
  attack_angle_deg: number
  vertical_bat_angle_deg: number
  time_to_contact_ms: number
  peak_omega_dps: number
  impact: boolean
  handedness: "R" | "L"
}

export interface Quality {
  confidence: "high" | "reduced"
  reasons: string[]
  anchor: "good" | "moving" | "none"
  accel_clipped: boolean
  accel_source?: "main" | "fused" | "mismatch"
  gyro_clipped: boolean
  calibrated: boolean
}

export interface SwingListItem {
  id: string
  recorded_at: string
  device_swing_id: number
  metrics: Metrics
  confidence: Quality["confidence"]
}

export type Vec3 = [number, number, number]

export interface SwingRecord {
  id: string
  recorded_at: string
  device_swing_id: number
  metrics: Metrics
  quality: Quality
  series: {
    t_ms: number[]
    bat_speed_mps: number[]
    hand_speed_mps: number[]
    omega_dps: number[]
  }
  path: {
    knob: Vec3[]
    dir: Vec3[]
    contact_index: number
    start_index: number
  }
  bat: {
    length_m: number
    sweet_spot_from_tip_m: number
    grip_from_knob_m: number
    sensor_faces_out: boolean
  }
  sample_rate_hz: number
  sample_count: number
}

export interface SwingSummary {
  swing_id: number
  impact: boolean
  accel_clipped: boolean
  gyro_clipped: boolean
  peak_omega_dps: number
  duration_ms: number
  peak_accel_g: number
}

export type ServerMessage =
  | { type: "hello"; status: LinkStatus; settings: Settings; simulate: boolean }
  | { type: "status"; status: LinkStatus }
  | { type: "live"; live: LiveData }
  | { type: "summary"; summary: SwingSummary }
  | { type: "swing"; swing: SwingRecord }
  | { type: "swing_error"; swing_id: number; error: string }
  | { type: "settings"; settings: Settings }
  | { type: "swings_changed" }

export type SpeedUnit = "mph" | "km/h"
export type Theme = "light" | "dark"
