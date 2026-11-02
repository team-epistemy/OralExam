import { createContext, useCallback, useContext, useRef, useState } from 'react';
import { CheckCircle2, AlertCircle, Info, X } from 'lucide-react';

// ── In-house toast system (no external dependency) ───────────────────────────
// A <ToastProvider> (mounted once in AppLayout) renders a top-right stack of
// auto-dismissing toasts. Anywhere under it, call useToast() to get
// toast.success / toast.error / toast.info — wire them into react-query
// mutation onSuccess/onError (or existing success/catch paths) for feedback.

export type ToastVariant = 'success' | 'error' | 'info';

interface Toast {
  id: number;
  variant: ToastVariant;
  message: string;
}

interface ToastApi {
  success: (message: string) => void;
  error: (message: string) => void;
  info: (message: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

// Errors linger a little longer than confirmations — there's a reason to read.
const DURATION: Record<ToastVariant, number> = { success: 4000, error: 6000, info: 4500 };

const VARIANT_STYLES: Record<ToastVariant, { wrap: string; icon: typeof CheckCircle2; iconClass: string }> = {
  // Success uses the brand success tokens so it tracks any reskin. Error follows
  // the app-wide red error convention; info uses the navy/gold brand palette.
  success: { wrap: 'bg-success-bg border-success/30 text-success', icon: CheckCircle2, iconClass: 'text-success' },
  error: { wrap: 'bg-red-50 border-red-200 text-red-800', icon: AlertCircle, iconClass: 'text-red-600' },
  info: { wrap: 'bg-white border-border text-ink', icon: Info, iconClass: 'text-gold' },
};

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const push = useCallback((variant: ToastVariant, message: string) => {
    const id = nextId.current++;
    setToasts((prev) => [...prev, { id, variant, message }]);
    window.setTimeout(() => dismiss(id), DURATION[variant]);
  }, [dismiss]);

  // Stable api object so consumers don't re-render on every toast change.
  const api = useRef<ToastApi>({
    success: (m: string) => push('success', m),
    error: (m: string) => push('error', m),
    info: (m: string) => push('info', m),
  });
  // Keep the closures current if push identity changes (it won't, but be safe).
  api.current.success = (m: string) => push('success', m);
  api.current.error = (m: string) => push('error', m);
  api.current.info = (m: string) => push('info', m);

  return (
    <ToastContext.Provider value={api.current}>
      {children}
      <div
        className="fixed top-4 right-4 z-[100] flex flex-col gap-2 w-[min(92vw,22rem)] pointer-events-none"
        aria-live="polite"
        aria-atomic="false"
      >
        {toasts.map((t) => {
          const s = VARIANT_STYLES[t.variant];
          const Icon = s.icon;
          return (
            <div
              key={t.id}
              role="status"
              className={`pointer-events-auto flex items-start gap-2.5 rounded-xl border px-4 py-3 shadow-lg ring-1 ring-black/5 text-sm animate-[toastIn_160ms_ease-out] ${s.wrap}`}
            >
              <Icon className={`w-4 h-4 mt-0.5 flex-shrink-0 ${s.iconClass}`} />
              <span className="flex-1 leading-snug">{t.message}</span>
              <button
                onClick={() => dismiss(t.id)}
                className="flex-shrink-0 opacity-50 hover:opacity-100 transition-opacity"
                aria-label="Dismiss"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    // Fail soft: outside a provider, toasts become no-ops rather than crashing.
    return { success: () => {}, error: () => {}, info: () => {} };
  }
  return ctx;
}
