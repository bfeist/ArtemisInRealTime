import { type JSX, useEffect, useId, useRef, useState } from "react";
import styles from "./CommFilterPicker.module.css";

interface FilterOption {
  value: string;
  label: string;
  count: number;
}

interface Props {
  label: string;
  description: string;
  options: FilterOption[];
  hidden: Set<string>;
  onChange: (hidden: Set<string>) => void;
  disabled?: boolean;
}

export default function CommFilterPicker({
  label,
  description,
  options,
  hidden,
  onChange,
  disabled = false,
}: Props): JSX.Element {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  const selected = options.filter((option) => !hidden.has(option.value));
  const summary = disabled
    ? "PAO off"
    : selected.length === options.length
      ? "All"
      : selected.length === 0
        ? "None"
        : selected.length === 1
          ? selected[0].label
          : `${selected.length} of ${options.length}`;

  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        trigger.current?.focus();
      }
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  return (
    <div
      ref={root}
      className={styles.picker}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node)) setOpen(false);
      }}
    >
      <button
        ref={trigger}
        type="button"
        className={`${styles.trigger} ${hidden.size > 0 && !disabled ? styles.filtered : ""}`}
        disabled={disabled}
        aria-label={`${label}: ${summary}`}
        aria-expanded={open && !disabled}
        aria-controls={id}
        onClick={() => setOpen(!open)}
      >
        {label}
        <span className={styles.summary}>{summary}</span>
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
          <path d="m3 4.5 3 3 3-3" stroke="currentColor" strokeWidth="1.5" />
        </svg>
      </button>
      {open && !disabled && (
        <div id={id} className={styles.panel} role="group" aria-label={label}>
          <div className={styles.panelHeader}>
            <strong>{label}</strong>
            <span>{selected.length} selected</span>
          </div>
          <p id={`${id}-description`} className={styles.description}>
            {description}
          </p>
          <div className={styles.bulkActions}>
            <span>Select</span>
            <button type="button" onClick={() => onChange(new Set())}>
              All
            </button>
            <button type="button" onClick={() => onChange(new Set(options.map((o) => o.value)))}>
              None
            </button>
          </div>
          <div className={styles.options} aria-describedby={`${id}-description`}>
            {options.map(({ value, label: optionLabel, count }) => (
              <div key={value} className={styles.option}>
                <label className={styles.optionLabel}>
                  <input
                    type="checkbox"
                    aria-label={`${optionLabel}, ${count.toLocaleString()} entries`}
                    checked={!hidden.has(value)}
                    onChange={() => {
                      const next = new Set(hidden);
                      if (next.has(value)) next.delete(value);
                      else next.add(value);
                      onChange(next);
                    }}
                  />
                  <span>{optionLabel}</span>
                  <span className={styles.count}>{count.toLocaleString()}</span>
                </label>
                <button
                  type="button"
                  className={styles.only}
                  aria-label={`Only ${optionLabel}`}
                  onClick={() =>
                    onChange(new Set(options.filter((o) => o.value !== value).map((o) => o.value)))
                  }
                >
                  Only
                </button>
              </div>
            ))}
          </div>
          <div className={styles.panelFooter}>
            <span>Changes apply instantly</span>
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                trigger.current?.focus();
              }}
            >
              Done
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
