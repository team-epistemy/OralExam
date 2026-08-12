import { useState, useEffect, useRef } from 'react';

export interface ProgressStep {
  label: string;
  /** The progress percentage at which this step ends (e.g., 25 means 0-25%) */
  endPercent: number;
}

interface ProgressStepsProps {
  steps: ProgressStep[];
  /** Whether the operation is currently running */
  active: boolean;
  /** Total duration in ms to simulate through all steps (default 10000) */
  duration?: number;
  /** Called when progress completes naturally (timer finishes) */
  onSimulationComplete?: () => void;
  /** Whether the real operation has completed (jumps to 100%) */
  completed?: boolean;
}

export default function ProgressSteps({
  steps,
  active,
  duration = 10000,
  completed = false,
}: ProgressStepsProps) {
  const [progress, setProgress] = useState(0);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (active && !completed) {
      setProgress(0);
      const tick = 100; // update every 100ms
      // We simulate up to 90% over the duration, leaving room for the real completion
      const maxSimulated = 90;
      const increment = (maxSimulated / (duration / tick));

      intervalRef.current = setInterval(() => {
        setProgress((prev) => {
          const next = prev + increment;
          if (next >= maxSimulated) {
            // Stall at max simulated - don't clear interval, just stop advancing
            return maxSimulated;
          }
          return next;
        });
      }, tick);

      return () => {
        if (intervalRef.current) clearInterval(intervalRef.current);
      };
    }

    if (completed) {
      // Jump to 100%
      if (intervalRef.current) clearInterval(intervalRef.current);
      setProgress(100);
    }

    if (!active && !completed) {
      setProgress(0);
    }
  }, [active, completed, duration]);

  // Determine current step label
  const currentStep = steps.find((step) => progress <= step.endPercent) || steps[steps.length - 1];

  if (!active && !completed) return null;

  return (
    <div className="w-full space-y-2">
      {/* Progress bar */}
      <div className="w-full h-1.5 bg-gray-200 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full bg-gradient-to-r from-blue-500 to-blue-600 transition-all duration-300 ease-out"
          style={{ width: `${Math.min(progress, 100)}%` }}
        />
      </div>
      {/* Step label */}
      {!completed ? (
        <p className="animate-pulse text-sm text-gray-600">{currentStep.label}</p>
      ) : (
        <p className="text-sm text-green-600 font-medium">Complete</p>
      )}
    </div>
  );
}
