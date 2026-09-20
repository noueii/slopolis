import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

export interface CapFieldProps {
  id: string
  label: string
  /** Where the cap bites, in the admin's terms. */
  description: string
  value: string
  error: string | null
  disabled: boolean
  onChange: (value: string) => void
}

/**
 * One cap. A blank field is the API's `null`, so the placeholder says so and
 * the description repeats it: the value a blank field sends is a policy
 * decision the admin cannot see from the input alone.
 */
export function CapField({
  id,
  label,
  description,
  value,
  error,
  disabled,
  onChange,
}: CapFieldProps) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <p
        id={`${id}-description`}
        className="max-w-2xl text-xs leading-relaxed text-muted-foreground"
      >
        {description}
      </p>
      <Input
        id={id}
        className="max-w-[220px]"
        // Text rather than `type="number"`: the browser would drop a keystroke
        // it considers invalid, hiding the mistake instead of reporting it.
        type="text"
        inputMode="numeric"
        autoComplete="off"
        placeholder="Unlimited"
        value={value}
        disabled={disabled}
        aria-invalid={error !== null}
        aria-describedby={
          error !== null ? `${id}-description ${id}-error` : `${id}-description`
        }
        onChange={(event) => onChange(event.target.value)}
      />
      {error !== null ? (
        <p id={`${id}-error`} role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  )
}
