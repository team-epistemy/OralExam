import { Link } from 'react-router-dom';
import { ChevronRight, ArrowLeft } from 'lucide-react';

// ── Navigation primitives ────────────────────────────────────────────────────
// <Breadcrumbs> renders a consistent trail (Courses › Course › …) with chevron
// separators; the last item is the current page (non-link). <BackLink> is the
// compact single-level "← label" used where a full trail is overkill.

export interface Crumb {
  label: string;
  to?: string; // omit on the current (last) item
}

export function Breadcrumbs({ items, className = '' }: { items: Crumb[]; className?: string }) {
  return (
    <nav aria-label="Breadcrumb" className={className}>
      <ol className="flex items-center flex-wrap gap-1 text-sm text-muted">
        {items.map((item, i) => {
          const last = i === items.length - 1;
          return (
            <li key={`${item.label}-${i}`} className="inline-flex items-center gap-1 min-w-0">
              {item.to && !last ? (
                <Link to={item.to} className="hover:text-navy transition-colors truncate max-w-[16rem]">
                  {item.label}
                </Link>
              ) : (
                <span
                  className={last ? 'font-medium text-navy truncate max-w-[20rem]' : 'truncate max-w-[16rem]'}
                  aria-current={last ? 'page' : undefined}
                >
                  {item.label}
                </span>
              )}
              {!last && <ChevronRight className="w-3.5 h-3.5 text-border flex-shrink-0" />}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

export function BackLink({ to, label, className = '' }: { to: string; label: string; className?: string }) {
  return (
    <Link
      to={to}
      className={`inline-flex items-center gap-1 text-sm text-muted hover:text-navy transition-colors ${className}`}
    >
      <ArrowLeft className="w-4 h-4" /> {label}
    </Link>
  );
}

export default Breadcrumbs;
