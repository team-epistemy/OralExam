import { useState } from 'react';
import { X, Plus, Loader2 } from 'lucide-react';
import { createCourse } from '../api/courses';

// Single-field "create a course" modal, shared by the nav's Add Course button
// and the Dashboard's Create Course affordance. On success it hands back the new
// course id so the caller can refresh its list and navigate to the course.
export default function AddCourseModal({ onClose, onCreated }: { onClose: () => void; onCreated: (courseId: string) => void }) {
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    if (!name.trim()) return;
    setBusy(true);
    setError('');
    try {
      const c = await createCourse(name.trim());
      onCreated(c.course_id);
    } catch {
      setError('Could not create the course. Please try again.');
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-sm p-5 space-y-4" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-gray-900">Add Course</h3>
          <button onClick={onClose} className="p-1 text-gray-400 hover:bg-gray-100 rounded"><X className="w-4 h-4" /></button>
        </div>
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') submit(); }}
          placeholder="e.g. Operations Management"
          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        {error && <p className="text-xs text-red-600">{error}</p>}
        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="text-sm px-3 py-1.5 border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50">Cancel</button>
          <button onClick={submit} disabled={!name.trim() || busy} className="inline-flex items-center gap-2 text-sm px-4 py-1.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50">
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />} Create
          </button>
        </div>
      </div>
    </div>
  );
}
