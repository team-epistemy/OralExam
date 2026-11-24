import { useState } from 'react';
import { Share2, Copy, Check, X } from 'lucide-react';
import type { DemoLinkCreated } from '../api/demo';

// Modal showing the freshly-minted, credential-free demo URL with a copy button.
// Shared by the Active Assignments page and the course's Assignments tab.
export default function DemoLinkModal({ link, onClose }: { link: DemoLinkCreated & { title?: string }; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(link.url); setCopied(true); setTimeout(() => setCopied(false), 1500); }
    catch { /* clipboard blocked — the URL is selectable in the field */ }
  };
  return (
    <div className="fixed inset-0 bg-navy/40 backdrop-blur-sm flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl max-w-lg w-full p-6 shadow-2xl ring-1 ring-black/5" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between mb-3">
          <div className="flex items-center gap-2.5">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-gold to-gold-light flex items-center justify-center">
              <Share2 className="w-4 h-4 text-white" />
            </div>
            <h2 className="text-lg font-heading text-navy">Demo link ready</h2>
          </div>
          <button onClick={onClose} className="text-muted hover:text-ink transition-colors"><X className="w-5 h-5" /></button>
        </div>
        <p className="text-sm text-muted mb-4 leading-relaxed">
          Anyone with this link can take <span className="font-medium text-ink">{link.title || 'the demo'}</span> with
          <span className="font-medium text-ink"> no login</span> — practice mode, nothing saved. Expires in
          {' '}{link.days ?? 10} days and stops after {link.max_attempts ?? 10} attempts.
        </p>
        <div className="flex gap-2">
          <input readOnly value={link.url} onFocus={(e) => e.currentTarget.select()}
            className="flex-1 px-3 py-2.5 border border-border rounded-xl text-sm font-mono bg-parchment/60 text-ink focus:outline-none focus:ring-2 focus:ring-gold/30" />
          <button onClick={copy}
            className="inline-flex items-center gap-1.5 px-4 py-2.5 bg-navy text-white rounded-xl text-sm font-medium hover:bg-navy-light transition-colors">
            {copied ? <><Check className="w-4 h-4" /> Copied</> : <><Copy className="w-4 h-4" /> Copy</>}
          </button>
        </div>
      </div>
    </div>
  );
}
