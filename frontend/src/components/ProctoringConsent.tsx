import { Camera, ShieldCheck } from 'lucide-react';

// Explicit consent gate shown before the camera turns on. Declining blocks the
// proctored exam (the caller decides what "decline" does — exit / cannot start).
export default function ProctoringConsent({
  onConsent, onDecline,
}: { onConsent: () => void; onDecline: () => void }) {
  return (
    <div className="max-w-xl mx-auto p-6">
      <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-6 space-y-4">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-blue-50 flex items-center justify-center">
            <Camera className="w-5 h-5 text-blue-600" />
          </div>
          <h1 className="text-lg font-semibold text-gray-900">Camera monitoring for this exam</h1>
        </div>
        <p className="text-sm text-gray-600">
          This exam is proctored. With your consent, your camera will capture still images while you take it:
        </p>
        <ul className="text-sm text-gray-600 list-disc pl-5 space-y-1">
          <li>a photo at the start to confirm your identity,</li>
          <li>periodic snapshots during the exam, and</li>
          <li>a snapshot as you submit each answer.</li>
        </ul>
        <div className="rounded-lg bg-gray-50 border border-gray-100 p-3 text-xs text-gray-500 space-y-1">
          <p className="flex items-center gap-1.5 text-gray-600 font-medium"><ShieldCheck className="w-3.5 h-3.5" /> How your images are handled</p>
          <p>Still images only — no audio or continuous video. They are stored securely, visible only to your instructor and exam administrators for integrity review, and deleted after 90 days.</p>
        </div>
        <p className="text-sm text-gray-600">
          If you decline, you won't be able to take this proctored exam. You can close this and contact your instructor.
        </p>
        <div className="flex items-center gap-3 pt-1">
          <button
            onClick={onConsent}
            className="inline-flex items-center gap-2 px-4 py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
          >
            <Camera className="w-4 h-4" /> I consent — enable camera &amp; start
          </button>
          <button
            onClick={onDecline}
            className="px-4 py-2.5 border border-gray-300 text-gray-600 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors"
          >
            Decline
          </button>
        </div>
      </div>
    </div>
  );
}
