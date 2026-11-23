import { useEffect, useRef, useState } from "react"

import { CloseIcon } from "@/components/icons"
import { METRES_PER_INCH } from "@/lib/format"
import type { Settings } from "@/lib/types"

interface SettingsDialogProps {
  open: boolean
  settings: Settings
  onClose: () => void
  onSave: (settings: Settings) => Promise<void>
}

interface FormState {
  lengthIn: string
  sweetIn: string
  facesOut: boolean
  startDps: string
  minPeakDps: string
}

function toForm(s: Settings): FormState {
  return {
    lengthIn: (s.bat_length_m / METRES_PER_INCH).toFixed(1),
    sweetIn: (s.sweet_spot_from_tip_m / METRES_PER_INCH).toFixed(1),
    facesOut: s.sensor_faces_out,
    startDps: String(s.start_dps),
    minPeakDps: String(s.min_peak_dps),
  }
}

function validate(form: FormState): Settings | string {
  const length = Number(form.lengthIn)
  const sweet = Number(form.sweetIn)
  const start = Number(form.startDps)
  const minPeak = Number(form.minPeakDps)
  if (!(length >= 20 && length <= 43)) return "Bat length must be between 20 and 43 inches."
  if (!(sweet >= 2 && sweet <= 11.5)) return "Sweet spot must be between 2 and 11.5 inches from the tip."
  if (!Number.isInteger(start) || start < 200 || start > 2000) return "Start threshold must be a whole number from 200 to 2000 °/s."
  if (!Number.isInteger(minPeak) || minPeak > 3500) return "Minimum peak must be a whole number up to 3500 °/s."
  if (minPeak < start + 100) return "Minimum peak must be at least 100 °/s above the start threshold."
  return {
    bat_length_m: length * METRES_PER_INCH,
    sweet_spot_from_tip_m: sweet * METRES_PER_INCH,
    sensor_faces_out: form.facesOut,
    start_dps: start,
    min_peak_dps: minPeak,
  }
}

export function SettingsDialog({ open, settings, onClose, onSave }: SettingsDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [form, setForm] = useState<FormState>(() => toForm(settings))
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (open && !dialog.open) {
      setForm(toForm(settings))
      setError(null)
      dialog.showModal()
    } else if (!open && dialog.open) {
      dialog.close()
    }
  }, [open, settings])

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    const result = validate(form)
    if (typeof result === "string") {
      setError(result)
      return
    }
    setSaving(true)
    setError(null)
    try {
      await onSave(result)
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setForm((f) => ({ ...f, [key]: value }))

  return (
    <dialog
      ref={dialogRef}
      onClose={onClose}
      className="m-auto w-[min(30rem,calc(100vw-2rem))] rounded-lg border border-line-strong bg-surface p-0 text-ink"
    >
      <form onSubmit={submit}>
        <div className="flex items-center justify-between border-b border-line px-4 py-3">
          <h2 className="text-base font-semibold">Settings</h2>
          <button type="button" className="btn btn-icon border-transparent" aria-label="Close settings" onClick={onClose}>
            <CloseIcon />
          </button>
        </div>

        <div className="flex flex-col gap-5 px-4 py-4">
          <fieldset className="flex flex-col gap-3">
            <legend className="panel-title mb-2">Bat</legend>
            <div className="grid grid-cols-2 gap-3">
              <label className="flex flex-col gap-1 text-xs text-ink-secondary">
                Length (in)
                <input
                  className="field"
                  type="number"
                  inputMode="decimal"
                  step="0.5"
                  value={form.lengthIn}
                  onChange={(e) => set("lengthIn", e.target.value)}
                />
              </label>
              <label className="flex flex-col gap-1 text-xs text-ink-secondary">
                Sweet spot from tip (in)
                <input
                  className="field"
                  type="number"
                  inputMode="decimal"
                  step="0.5"
                  value={form.sweetIn}
                  onChange={(e) => set("sweetIn", e.target.value)}
                />
              </label>
            </div>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-0.5 size-4 accent-accent"
                checked={form.facesOut}
                onChange={(e) => set("facesOut", e.target.checked)}
              />
              <span>
                Sensor chip faces away from the bat
                <span className="block text-xs text-ink-muted">
                  Untick if the board is mounted chip-side against the knob. If the bat points the wrong way in the live
                  view, this is the setting to flip.
                </span>
              </span>
            </label>
            <p className="text-xs text-ink-muted">Changing the bat re-analyses every stored swing.</p>
          </fieldset>

          <fieldset className="flex flex-col gap-3">
            <legend className="panel-title mb-2">Swing detection</legend>
            <div className="grid grid-cols-2 gap-3">
              <label className="flex flex-col gap-1 text-xs text-ink-secondary">
                Start threshold (°/s)
                <input
                  className="field"
                  type="number"
                  inputMode="numeric"
                  step="50"
                  value={form.startDps}
                  onChange={(e) => set("startDps", e.target.value)}
                />
              </label>
              <label className="flex flex-col gap-1 text-xs text-ink-secondary">
                Minimum peak (°/s)
                <input
                  className="field"
                  type="number"
                  inputMode="numeric"
                  step="50"
                  value={form.minPeakDps}
                  onChange={(e) => set("minPeakDps", e.target.value)}
                />
              </label>
            </div>
            <p className="text-xs text-ink-muted">
              Raise these if practice waggles are being recorded as swings; lower them if slow swings are missed.
            </p>
          </fieldset>

          {error && (
            <p role="alert" className="rounded-md border border-critical/40 px-2.5 py-2 text-xs text-critical">
              {error}
            </p>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-line px-4 py-3">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </form>
    </dialog>
  )
}
