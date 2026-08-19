import { useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, UserPlus, Upload, Trash2, Users, Loader2, Plug } from 'lucide-react';
import { listStudents, enrollStudents, unenrollStudent, parseEmails, type Student } from '../../api/courses';

export default function CourseStudents() {
  const { courseId = '' } = useParams();
  const queryClient = useQueryClient();
  const [single, setSingle] = useState('');
  const [bulk, setBulk] = useState('');
  const [note, setNote] = useState('');

  const { data: students = [], isLoading } = useQuery({
    queryKey: ['course-students', courseId],
    queryFn: () => listStudents(courseId),
    enabled: !!courseId,
  });

  const enroll = useMutation({
    mutationFn: (emails: string[]) => enrollStudents(courseId, emails),
    onSuccess: (r) => {
      setNote(`Added ${r.added}${r.skipped ? `, ${r.skipped} already enrolled/invalid` : ''}. Roster: ${r.count}.`);
      setSingle('');
      setBulk('');
      queryClient.invalidateQueries({ queryKey: ['course-students', courseId] });
    },
    onError: () => setNote('Failed to add students. Please try again.'),
  });

  const remove = useMutation({
    mutationFn: (email: string) => unenrollStudent(courseId, email),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['course-students', courseId] }),
  });

  const onCsv = (file: File) => {
    const reader = new FileReader();
    reader.onload = () => setBulk(String(reader.result || ''));
    reader.readAsText(file);
  };

  const bulkEmails = parseEmails(bulk);

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <Link to={`/professor/courses/${courseId}`} className="inline-flex items-center gap-2 text-sm text-gray-500 hover:text-gray-800">
          <ArrowLeft className="w-4 h-4" /> Back to course
        </Link>
        <h1 className="text-2xl font-bold text-gray-900 mt-2 flex items-center gap-2"><Users className="w-6 h-6 text-blue-600" /> Add Students</h1>
        <p className="text-sm text-gray-500 mt-1">Enroll students by email. Enrolled students see this course's exams.</p>
      </div>

      {/* Single email */}
      <div className="bg-white border border-gray-200 rounded-xl p-5 space-y-3">
        <h2 className="text-sm font-semibold text-gray-800">Add one student</h2>
        <div className="flex gap-2">
          <input
            type="email"
            value={single}
            onChange={(e) => setSingle(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && single.trim()) enroll.mutate([single]); }}
            placeholder="student@univ.edu"
            className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            onClick={() => enroll.mutate([single])}
            disabled={!single.trim() || enroll.isPending}
            className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            <UserPlus className="w-4 h-4" /> Add
          </button>
        </div>
      </div>

      {/* CSV / bulk */}
      <div className="bg-white border border-gray-200 rounded-xl p-5 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-800">Add a list (CSV)</h2>
          <label className="inline-flex items-center gap-2 text-xs px-3 py-1.5 border border-gray-300 rounded-lg text-gray-600 hover:bg-gray-50 cursor-pointer">
            <Upload className="w-3.5 h-3.5" /> Upload .csv
            <input type="file" accept=".csv,text/csv,text/plain" className="hidden" onChange={(e) => e.target.files?.[0] && onCsv(e.target.files[0])} />
          </label>
        </div>
        <textarea
          value={bulk}
          onChange={(e) => setBulk(e.target.value)}
          rows={4}
          placeholder="Paste emails (comma, space, or newline separated), or upload a .csv above"
          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-y"
        />
        <div className="flex items-center gap-3">
          <button
            onClick={() => enroll.mutate(bulkEmails)}
            disabled={bulkEmails.length === 0 || enroll.isPending}
            className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            {enroll.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <UserPlus className="w-4 h-4" />}
            Add {bulkEmails.length} student{bulkEmails.length === 1 ? '' : 's'}
          </button>
          {note && <span className="text-sm text-gray-500">{note}</span>}
        </div>
      </div>

      {/* Instructure (disabled) */}
      <div className="bg-white border border-gray-200 rounded-xl p-5 opacity-70">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Plug className="w-4 h-4 text-gray-400" />
            <h2 className="text-sm font-semibold text-gray-800">Sync from Instructure (Canvas)</h2>
          </div>
          <span className="text-[11px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 font-medium">Coming soon</span>
        </div>
        <p className="text-xs text-gray-500 mt-1">Pull your course roster directly via the Instructure API. Disabled for now.</p>
        <button disabled className="mt-3 px-4 py-2 bg-gray-200 text-gray-500 rounded-lg text-sm font-medium cursor-not-allowed">Connect Instructure</button>
      </div>

      {/* Roster */}
      <div className="bg-white border border-gray-200 rounded-xl">
        <div className="px-5 py-3 border-b border-gray-100 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-800">Roster</h2>
          <span className="text-xs text-gray-400">{students.length} enrolled</span>
        </div>
        {isLoading ? (
          <div className="p-6 text-center"><Loader2 className="w-5 h-5 text-blue-600 animate-spin mx-auto" /></div>
        ) : students.length === 0 ? (
          <p className="p-6 text-center text-sm text-gray-500">No students enrolled yet. This course is open to all students until you add a roster.</p>
        ) : (
          <ul className="divide-y divide-gray-100">
            {students.map((s: Student) => (
              <li key={s.email} className="flex items-center justify-between px-5 py-2.5">
                <span className="text-sm text-gray-800">{s.email}</span>
                <button onClick={() => remove.mutate(s.email)} className="p-1.5 text-red-600 hover:bg-red-50 rounded" title="Remove">
                  <Trash2 className="w-4 h-4" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
