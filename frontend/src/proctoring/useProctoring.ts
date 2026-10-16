import { useCallback, useEffect, useRef, useState } from 'react';
import { uploadSnapshot } from '../api/proctoring';

// Camera-snapshot proctoring for authed exams. Once enabled (after consent), it
// opens the camera, takes an identity snapshot, then a proctoring snapshot every
// intervalMs; captureResponse(turn) grabs one tied to an answer. All uploads are
// best-effort — a failed snapshot never interrupts the exam.
export function useProctoring(
  { sessionId, enabled, intervalMs = 20000 }:
  { sessionId: string; enabled: boolean; intervalMs?: number },
) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const timerRef = useRef<number>(0);
  const [active, setActive] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const snap = useCallback(async (kind: 'identity' | 'proctor' | 'response', turnIndex?: number) => {
    const v = videoRef.current;
    if (!v || !v.videoWidth) return;
    const c = canvasRef.current ?? (canvasRef.current = document.createElement('canvas'));
    c.width = v.videoWidth; c.height = v.videoHeight;
    c.getContext('2d')?.drawImage(v, 0, 0, c.width, c.height);
    const blob: Blob | null = await new Promise((res) => c.toBlob((b) => res(b), 'image/jpeg', 0.7));
    if (!blob) return;
    try { await uploadSnapshot(sessionId, kind, blob, turnIndex); } catch { /* best-effort */ }
  }, [sessionId]);

  useEffect(() => {
    if (!enabled || !sessionId) return;
    let cancelled = false;
    (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 }, audio: false });
        if (cancelled) { stream.getTracks().forEach((t) => t.stop()); return; }
        streamRef.current = stream;
        const v = document.createElement('video');
        v.muted = true; v.playsInline = true; v.srcObject = stream;
        videoRef.current = v;
        await v.play().catch(() => { /* headless play may warn; frames still render */ });
        setActive(true);
        window.setTimeout(() => void snap('identity'), 800);     // let the first frame arrive
        timerRef.current = window.setInterval(() => void snap('proctor'), intervalMs);
      } catch {
        setError('Camera unavailable — proctoring snapshots are disabled.');
      }
    })();
    return () => {
      cancelled = true;
      if (timerRef.current) window.clearInterval(timerRef.current);
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
      videoRef.current = null;
      setActive(false);
    };
  }, [enabled, sessionId, intervalMs, snap]);

  const captureResponse = useCallback((turnIndex: number) => { void snap('response', turnIndex); }, [snap]);
  return { active, error, captureResponse };
}
