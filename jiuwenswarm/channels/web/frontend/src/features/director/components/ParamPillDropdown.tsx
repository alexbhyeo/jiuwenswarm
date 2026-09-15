import { useEffect, useRef, useState } from 'react';

interface ParamPillDropdownProps {
  value: string;
  label: string;
  options: Array<{ value: string; label: string }>;
  onChange: (value: string) => void;
}

const chevron = (
  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
    <path d="m6 9 6 6 6-6" />
  </svg>
);

export function ParamPillDropdown({ value, label, options, onChange }: ParamPillDropdownProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [open]);

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button type="button" className="director-param-pill" onClick={() => setOpen((v) => !v)}>
        {label}
        {chevron}
      </button>
      {open && (
        <div className="director-param-menu">
          {options.map((opt) => (
            <button
              key={opt.value}
              type="button"
              className={`director-param-menu-item ${opt.value === value ? 'director-param-menu-item--active' : ''}`}
              onClick={() => {
                onChange(opt.value);
                setOpen(false);
              }}
            >
              {opt.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
