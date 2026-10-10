import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { X, Mic, Loader2, Pencil } from 'lucide-react';
import {
  createCommunicationAssignment, listCommunicationCards, type SpeakingCard,
} from '../api/assignments';

const CUSTOM = 'custom';

// "Create a speaking exam" modal: pick a Part 2 long-turn prompt from the bank
// or write your own. Whatever the prompt, the exam is one long turn plus four
// adaptive follow-ups graded on the CEFR grid — only the Part 2 stem varies.
export default function CreateSpeakingExamModal({
  courseId,
  onClose,
  onCreated,
}: {
  courseId: string;
  onClose: () => void;
  onCreated: (assignmentId: string) => void;
}) {
  const { data, isLoading: loadingCards } = useQuery({
    queryKey: ['communication-cards'],
    queryFn: listCommunicationCards,
  });

  const [selected, setSelected] = useState<string | null>(null);
  const [custom, setCustom] = useState('');
  const [title, setTitle] = useState('');
  const [titleEdited, setTitleEdited] = useState(false);
  const [duration, setDuration] = useState('15');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const cards: SpeakingCard[] = data?.cards || [];
  // Default to the server's default card once the bank arrives.
  const activeId = selected ?? data?.default_card_id ?? null;
  const activeCard = cards.find((c) => c.card_id === activeId);
  const isCustom = activeId === CUSTOM;

  // The title tracks the chosen theme until the professor types their own.
  const effectiveTitle = titleEdited
    ? title
    : isCustom
      ? 'Speaking exam'
      : activeCard ? `Speaking — ${activeCard.theme}` : 'Speaking exam';

  const pick = (cardId: string) => {
    setSelected(cardId);
    setError('');
  };

  const canSubmit = effectiveTitle.trim().length > 0
    && (!isCustom || custom.trim().length > 0)
    && !busy;

  const submit = async () => {
    if (!canSubmit) return;
    setBusy(true);
    setError('');
    try {
      const minutes = parseInt(duration, 10);
      const a = await createCommunicationAssignment(courseId, effectiveTitle.trim(), {
        durationMinutes: Number.isFinite(minutes) && minutes > 0 ? minutes : undefined,
        cardId: isCustom ? undefined : activeId ?? undefined,
        questionText: isCustom ? custom.trim() : undefined,
      });
      onCreated(a.assignment_id);
    } catch (err: any) {
      setError(err?.message || 'Could not create the speaking exam. Please try again.');
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-xl w-full max-w-lg max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <h3 className="inline-flex items-center gap-2 text-sm font-semibold text-gray-900">
            <Mic className="w-4 h-4 text-purple-600" /> New Speaking Exam
          </h3>
          <button onClick={onClose} className="p-1 text-gray-400 hover:bg-gray-100 rounded">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          <div>
            <p className="text-xs font-medium text-gray-700 mb-2">
              Part 2 question <span className="font-normal text-gray-400">— the spoken long turn</span>
            </p>
            {loadingCards ? (
              <p className="text-sm text-gray-400 inline-flex items-center gap-2">
                <Loader2 className="w-4 h-4 animate-spin" /> Loading questions…
              </p>
            ) : (
              <div className="space-y-1.5">
                {cards.map((card) => (
                  <label
                    key={card.card_id}
                    className={`block rounded-lg border p-3 cursor-pointer transition-colors ${
                      activeId === card.card_id
                        ? 'border-purple-400 bg-purple-50'
                        : 'border-gray-200 hover:bg-gray-50'
                    }`}
                  >
                    <div className="flex gap-2.5">
                      <input
                        type="radio"
                        name="speaking-card"
                        className="mt-1 accent-purple-600"
                        checked={activeId === card.card_id}
                        onChange={() => pick(card.card_id)}
                      />
                      <div className="min-w-0">
                        <p className="text-[11px] font-semibold uppercase tracking-wide text-purple-700">
                          {card.theme}
                        </p>
                        <p className="text-sm text-gray-700">{card.question}</p>
                      </div>
                    </div>
                  </label>
                ))}

                <label
                  className={`block rounded-lg border p-3 cursor-pointer transition-colors ${
                    isCustom ? 'border-purple-400 bg-purple-50' : 'border-gray-200 hover:bg-gray-50'
                  }`}
                >
                  <div className="flex gap-2.5">
                    <input
                      type="radio"
                      name="speaking-card"
                      className="mt-1 accent-purple-600"
                      checked={isCustom}
                      onChange={() => pick(CUSTOM)}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="inline-flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide text-purple-700">
                        <Pencil className="w-3 h-3" /> Write my own
                      </p>
                      {isCustom && (
                        <>
                          <textarea
                            autoFocus
                            rows={3}
                            value={custom}
                            onChange={(e) => setCustom(e.target.value)}
                            placeholder="Describe a decision you found difficult. Say what it was, who it affected, and how you decided."
                            className="mt-1.5 w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-purple-500"
                          />
                          <p className="mt-1 text-[11px] text-gray-500">
                            Ask for one thing to describe plus two or three things to
                            cover, so there is enough to speak about for a minute or
                            two. The follow-ups stay on whatever this asks about.
                          </p>
                        </>
                      )}
                    </div>
                  </div>
                </label>
              </div>
            )}
          </div>

          <div className="flex gap-3">
            <div className="flex-1 space-y-1">
              <label className="block text-xs font-medium text-gray-700">Title</label>
              <input
                value={effectiveTitle}
                onChange={(e) => { setTitleEdited(true); setTitle(e.target.value); }}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-purple-500"
              />
            </div>
            <div className="space-y-1">
              <label className="block text-xs font-medium text-gray-700">Time limit</label>
              <input
                type="number"
                min={1}
                max={240}
                value={duration}
                onChange={(e) => setDuration(e.target.value)}
                className="w-24 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-purple-500"
              />
            </div>
          </div>

          <div className="rounded-lg bg-gray-50 border border-gray-200 p-3">
            <p className="text-xs text-gray-600">
              The student answers this aloud, then gets <strong>four follow-ups</strong> that
              ladder up or down the CEFR scale depending on how well they answer.
              Every turn is graded on Range, Accuracy, Fluency, Coherence and Interaction.
            </p>
          </div>

          {error && <p className="text-xs text-red-600">{error}</p>}
        </div>

        <div className="flex items-center justify-between gap-2 px-5 py-3 border-t border-gray-100">
          <p className="text-xs text-gray-500">Goes live immediately.</p>
          <div className="flex gap-2">
            <button onClick={onClose} className="text-sm px-3 py-1.5 border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50">
              Cancel
            </button>
            <button
              onClick={submit}
              disabled={!canSubmit}
              className="inline-flex items-center gap-2 text-sm px-4 py-1.5 bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-50"
            >
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Mic className="w-4 h-4" />} Create
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
