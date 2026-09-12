/**
 * FormFields — reusable, fully-typed field primitives for the workflow step
 * config panels. Dark-mode Tailwind styling matched to the existing
 * TextField/SelectField helpers in WorkflowStepConfig.
 *
 * Exports:
 *  - INPUT_CLS / fieldId  — shared styling + id helpers (used by KeyValueEditor)
 *  - FieldShell           — label + helper-text wrapper
 *  - TextAreaField        — multiline text with label + helper
 *  - NumberField          — numeric input (undefined-safe)
 *  - ToggleField          — boolean switch with label + helper
 */
import type { ReactNode } from 'react';

// Shared input styling — kept in sync with TextField/SelectField.
export const INPUT_CLS =
  'w-full px-3 py-2 rounded-xl bg-[#0F1826]/5 border border-white/10 text-white/90 ' +
  'placeholder-white/30 text-xs focus:outline-none focus:ring-2 focus:ring-sky-500 ' +
  'focus:border-transparent';

export function fieldId(label: string, prefix = 'cfg'): string {
  return `${prefix}-${label.replace(/\s+/g, '-').toLowerCase()}`;
}

// ── Label + helper wrapper ────────────────────────────────────────────────────

export function FieldShell({
  label, htmlFor, description, children, action,
}: {
  label: string;
  htmlFor?: string;
  description?: ReactNode;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <label htmlFor={htmlFor} className="block text-xs font-medium text-white/50">
          {label}
        </label>
        {action}
      </div>
      {children}
      {description && <p className="text-xs text-white/30 mt-1">{description}</p>}
    </div>
  );
}

// ── Multiline text ────────────────────────────────────────────────────────────

export function TextAreaField({
  label, value, onChange, placeholder, description, mono = false, rows = 4,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  description?: ReactNode;
  mono?: boolean;
  rows?: number;
}) {
  const id = fieldId(label);
  return (
    <FieldShell label={label} htmlFor={id} description={description}>
      <textarea
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        rows={rows}
        className={`${INPUT_CLS} resize-y ${mono ? 'font-mono' : ''}`}
      />
    </FieldShell>
  );
}

// ── Numeric input ─────────────────────────────────────────────────────────────

export function NumberField({
  label, value, onChange, placeholder, description, min, max, step,
}: {
  label: string;
  value: number | undefined;
  onChange: (v: number | undefined) => void;
  placeholder?: string;
  description?: ReactNode;
  min?: number;
  max?: number;
  step?: number;
}) {
  const id = fieldId(label, 'cfg-num');
  return (
    <FieldShell label={label} htmlFor={id} description={description}>
      <input
        id={id}
        type="number"
        value={value ?? ''}
        min={min}
        max={max}
        step={step}
        onChange={(e) => {
          const raw = e.target.value;
          onChange(raw === '' ? undefined : Number(raw));
        }}
        placeholder={placeholder}
        className={INPUT_CLS}
      />
    </FieldShell>
  );
}

// ── Boolean switch ────────────────────────────────────────────────────────────

export function ToggleField({
  label, value, onChange, description,
}: {
  label: string;
  value: boolean;
  onChange: (v: boolean) => void;
  description?: ReactNode;
}) {
  const id = fieldId(label, 'cfg-toggle');
  return (
    <div>
      <div className="flex items-center justify-between gap-3">
        <label htmlFor={id} className="text-xs font-medium text-white/50 cursor-pointer">
          {label}
        </label>
        <button
          id={id}
          type="button"
          role="switch"
          aria-checked={value}
          onClick={() => onChange(!value)}
          className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full
            transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500
            ${value ? 'bg-sky-500' : 'bg-white/15'}`}
        >
          <span
            className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform
              ${value ? 'translate-x-4' : 'translate-x-1'}`}
          />
        </button>
      </div>
      {description && <p className="text-xs text-white/30 mt-1">{description}</p>}
    </div>
  );
}
