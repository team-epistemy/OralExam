import { useEffect, useRef, useState, type ComponentType } from 'react';
import { MoreHorizontal } from 'lucide-react';

// ── OverflowMenu ─────────────────────────────────────────────────────────────
// A "•••" button that opens a small popover of actions. Used to demote
// destructive/secondary actions out of a row's primary affordances. Closes on
// outside-click and Esc; keyboard-accessible; styled with brand tokens.

export interface OverflowAction {
  label: string;
  onSelect: () => void;
  icon?: ComponentType<{ className?: string }>;
  danger?: boolean;      // red styling for destructive actions
  disabled?: boolean;
}

export function OverflowMenu({
  actions,
  label = 'More actions',
  className = '',
  align = 'right',
}: {
  actions: OverflowAction[];
  label?: string;
  className?: string;
  align?: 'left' | 'right';
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  if (actions.length === 0) return null;

  return (
    <div ref={rootRef} className={`relative inline-block ${className}`}>
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); e.preventDefault(); setOpen((v) => !v); }}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        title={label}
        className="p-1.5 rounded-lg text-muted hover:text-navy hover:bg-parchment transition-colors"
      >
        <MoreHorizontal className="w-4 h-4" />
      </button>
      {open && (
        <div
          role="menu"
          className={`absolute z-50 mt-1 min-w-[10rem] rounded-xl border border-border bg-white py-1 shadow-lg ring-1 ring-black/5 ${
            align === 'right' ? 'right-0' : 'left-0'
          }`}
        >
          {actions.map((a, i) => {
            const Icon = a.icon;
            return (
              <button
                key={`${a.label}-${i}`}
                type="button"
                role="menuitem"
                disabled={a.disabled}
                onClick={(e) => {
                  e.stopPropagation();
                  e.preventDefault();
                  setOpen(false);
                  a.onSelect();
                }}
                className={`w-full flex items-center gap-2 px-3 py-2 text-left text-sm transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                  a.danger
                    ? 'text-red-600 hover:bg-red-50'
                    : 'text-ink hover:bg-parchment'
                }`}
              >
                {Icon && <Icon className="w-4 h-4 flex-shrink-0" />}
                {a.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default OverflowMenu;
