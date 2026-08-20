import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, AlertCircle, BookOpen } from 'lucide-react';
import { createCourse } from '../../api/courses';

export default function CreateCourse() {
  const navigate = useNavigate();
  const [courseName, setCourseName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    const name = courseName.trim();
    if (!name) return;
    setBusy(true);
    setError('');
    try {
      const course = await createCourse(name);
      navigate(`/professor/courses/${course.course_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create course');
      setBusy(false);
    }
  };

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Create Course</h1>
        <p className="text-sm text-gray-500 mt-1">
          Set up a new course. You can add materials, students, and assignments once it's created.
        </p>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 p-6 space-y-5">
        <div>
          <label htmlFor="course" className="block text-sm font-medium text-gray-700 mb-1">
            Course Name
          </label>
          <input
            id="course"
            type="text"
            value={courseName}
            onChange={(e) => setCourseName(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') submit(); }}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            placeholder="e.g. CS101-Intro-to-ML"
            autoFocus
          />
          <p className="text-xs text-gray-400 mt-1">Course name/code. Must be unique within your organization.</p>
        </div>

        {error && (
          <div className="flex items-center gap-3 p-4 bg-red-50 border border-red-200 rounded-lg">
            <AlertCircle className="w-5 h-5 text-red-600 shrink-0" />
            <p className="text-sm text-red-700">{error}</p>
          </div>
        )}

        <button
          onClick={submit}
          disabled={busy || !courseName.trim()}
          className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors disabled:opacity-50"
        >
          {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <BookOpen className="w-4 h-4" />}
          {busy ? 'Creating...' : 'Create Course'}
        </button>
      </div>
    </div>
  );
}
