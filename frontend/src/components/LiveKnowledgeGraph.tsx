import { useEffect, useRef } from 'react';

// The live "watch it think" graph: concept nodes ignite and causal edges draw
// themselves as the student demonstrates them. Data-driven — the exam feeds it the
// accumulated expected-path graph plus which nodes/edges have been demonstrated so
// far (from the grader's eds_components) — so what you see IS what's being scored.

export interface LiveGraphProps {
  nodes: string[];                       // all concept labels, stable insertion order
  edges: { src: string; dst: string }[]; // all causal edges
  litNodes: string[];                    // demonstrated concept labels
  litEdges: string[];                    // demonstrated edges, keyed `${src}|${dst}`
  currentTopic?: string | null;          // concept under examination right now
}

const edgeKey = (s: string, d: string) => `${s}|${d}`;

// kebab/snake ids -> readable Title Case, wrapped to ≤2 short lines
function labelLines(raw: string): string[] {
  const pretty = (raw || '').replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim()
    .replace(/\b\w/g, (c) => c.toUpperCase());
  const words = pretty.split(' ').filter(Boolean);
  const lines: string[] = []; let cur = '';
  for (const w of words) {
    const next = cur ? `${cur} ${w}` : w;
    if (next.length <= 13 || !cur) cur = next;
    else { lines.push(cur); cur = w; if (lines.length === 2) { cur = ''; break; } }
  }
  if (cur && lines.length < 2) lines.push(cur);
  return lines.length ? lines : [''];
}

// stable phyllotaxis (golden-angle) layout in an 800x620 design space — node i keeps
// its spot as later nodes are appended, so the map grows without jumping.
function layout(n: number): Array<{ x: number; y: number }> {
  const W = 800, H = 620, cx = W / 2, cy = H / 2 - 18;
  const golden = Math.PI * (3 - Math.sqrt(5));
  const R = Math.min(W, H) * 0.40;
  const out: Array<{ x: number; y: number }> = [];
  for (let i = 0; i < n; i++) {
    const r = n <= 1 ? 0 : R * Math.sqrt(i / (n - 1));
    const a = i * golden;
    out.push({ x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) });
  }
  return out;
}

export default function LiveKnowledgeGraph({ nodes, edges, litNodes, litEdges, currentTopic }: LiveGraphProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef({
    pos: [] as Array<{ x: number; y: number }>,
    nodeT0: {} as Record<string, number>,   // ignition start per node label
    edgeT0: {} as Record<string, number>,   // draw start per edge key
    litN: new Set<string>(),
    litE: new Set<string>(),
    raf: 0 as number,
  });
  const reduce = typeof window !== 'undefined' && window.matchMedia
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // reconcile incoming data: recompute layout, and stamp t0 for newly-lit items
  useEffect(() => {
    const s = stateRef.current;
    s.pos = layout(nodes.length);
    const now = performance.now();
    litNodes.forEach((l) => { if (!s.litN.has(l)) { s.litN.add(l); s.nodeT0[l] = reduce ? now - 999 : now; } });
    litEdges.forEach((k) => { if (!s.litE.has(k)) { s.litE.add(k); s.edgeT0[k] = reduce ? now - 999 : now; } });
  }, [nodes, edges, litNodes, litEdges, reduce]);

  useEffect(() => {
    const canvas = canvasRef.current; if (!canvas) return;
    const ctx = canvas.getContext('2d'); if (!ctx) return;
    const DW = 800, DH = 620;
    let scale = 1;
    const resize = () => {
      const cssW = canvas.clientWidth || 300;
      const cssH = cssW * DH / DW;
      canvas.style.height = `${cssH}px`;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
      scale = canvas.width / DW;
    };
    const easeOut = (t: number) => 1 - Math.pow(1 - t, 3);
    const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

    const idxOf: Record<string, number> = {};
    nodes.forEach((n, i) => { idxOf[n] = i; });

    const draw = (now: number) => {
      const s = stateRef.current;
      ctx.setTransform(scale, 0, 0, scale, 0, 0);
      ctx.clearRect(0, 0, DW, DH);
      const pos = s.pos;

      // edges
      for (const e of edges) {
        const a = pos[idxOf[e.src]], b = pos[idxOf[e.dst]];
        if (!a || !b) continue;
        const key = edgeKey(e.src, e.dst);
        const lit = s.litE.has(key);
        if (!lit) {
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
          ctx.strokeStyle = 'rgba(212,201,176,.14)'; ctx.lineWidth = 1.4; ctx.stroke();
          continue;
        }
        const p = easeOut(Math.min((now - (s.edgeT0[key] || now)) / 620, 1));
        const hx = lerp(a.x, b.x, p), hy = lerp(a.y, b.y, p);
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(hx, hy);
        ctx.strokeStyle = 'rgba(196,147,63,.92)'; ctx.lineWidth = 2.6;
        ctx.shadowColor = 'rgba(196,147,63,.7)'; ctx.shadowBlur = 8; ctx.stroke(); ctx.shadowBlur = 0;
        if (p < 1 && !reduce) {
          ctx.beginPath(); ctx.arc(hx, hy, 3.6, 0, 7);
          ctx.fillStyle = '#F3E4C4'; ctx.shadowColor = '#C4933F'; ctx.shadowBlur = 14; ctx.fill(); ctx.shadowBlur = 0;
        }
      }

      // nodes
      const R = 22;
      for (let i = 0; i < nodes.length; i++) {
        const p = pos[i]; if (!p) continue;
        const label = nodes[i];
        const isLit = s.litN.has(label);
        const isCur = !!currentTopic && label === currentTopic;
        const t = isLit ? easeOut(Math.min((now - (s.nodeT0[label] || now)) / 520, 1)) : 0;

        ctx.beginPath(); ctx.arc(p.x, p.y, R, 0, 7);
        ctx.fillStyle = 'rgba(27,42,74,.72)'; ctx.fill();
        ctx.lineWidth = isCur ? 2.5 : 1.5;
        ctx.strokeStyle = isCur ? 'rgba(196,147,63,.9)'
          : isLit ? `rgba(196,147,63,${0.4 + 0.6 * t})` : 'rgba(212,201,176,.34)';
        ctx.stroke();

        if (isLit) {
          ctx.beginPath(); ctx.arc(p.x, p.y, R, 0, 7);
          ctx.fillStyle = `rgba(196,147,63,${0.9 * t})`;
          ctx.shadowColor = `rgba(196,147,63,${0.9 * t})`; ctx.shadowBlur = 26 * t; ctx.fill(); ctx.shadowBlur = 0;
          ctx.beginPath(); ctx.arc(p.x, p.y, R * 0.5, 0, 7);
          ctx.fillStyle = `rgba(247,236,202,${0.95 * t})`; ctx.fill();
          if (t < 1 && !reduce) {
            ctx.beginPath(); ctx.arc(p.x, p.y, R + 16 * t, 0, 7);
            ctx.strokeStyle = `rgba(196,147,63,${0.5 * (1 - t)})`; ctx.lineWidth = 2; ctx.stroke();
          }
        }

        // label below
        ctx.font = '600 15px system-ui, sans-serif';
        ctx.textAlign = 'center'; ctx.textBaseline = 'top';
        ctx.fillStyle = isLit ? `rgba(245,240,225,${0.55 + 0.45 * t})` : 'rgba(214,205,184,.5)';
        labelLines(label).forEach((ln, li) => ctx.fillText(ln, p.x, p.y + R + 7 + li * 15));
      }
    };

    let running = true;
    const loop = (now: number) => { if (!running) return; draw(now); stateRef.current.raf = requestAnimationFrame(loop); };
    resize(); stateRef.current.raf = requestAnimationFrame(loop);
    const onResize = () => resize();
    window.addEventListener('resize', onResize);
    return () => { running = false; cancelAnimationFrame(stateRef.current.raf); window.removeEventListener('resize', onResize); };
  }, [nodes, edges, currentTopic, reduce]);

  return (
    <div className="rounded-xl overflow-hidden" style={{ background: 'radial-gradient(120% 90% at 30% 15%, #16213B, #0D1526)' }}>
      <canvas ref={canvasRef} style={{ display: 'block', width: '100%' }} />
    </div>
  );
}
