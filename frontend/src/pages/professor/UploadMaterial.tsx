import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { CheckCircle, AlertCircle, Loader2, Network, Calendar } from 'lucide-react';
import { uploadMaterial, listVersions } from '../../api/materials';
import type { MaterialVersion } from '../../api/materials';
import { setSyllabus } from '../../api/courses';
import { listSessions } from '../../api/sessions';
import FileUpload from '../../components/FileUpload';
import { DEFAULT_ORG } from '../../config';

export default function UploadMaterial() {
  const [params] = useSearchParams();
  const isSyllabus = params.get('syllabus') === '1';
  const syllabusCourseId = params.get('courseId') || '';
  const coursePrefilled = !!params.get('course');
  const [courseName, setCourseName] = useState(params.get('course') || '');
  const [topic, setTopic] = useState('');
  // A material is always mapped to a class session. When the course is known we
  // let the professor pick an existing session or create one; otherwise the
  // backend instantiates a session automatically.
  const preselectedSession = params.get('sessionId') || '';
  const [sessionChoice, setSessionChoice] = useState(preselectedSession || 'new');
  const [newSessionDate, setNewSessionDate] = useState('');
  const { data: sessionsData } = useQuery({
    queryKey: ['course-sessions', syllabusCourseId],
    queryFn: () => listSessions(syllabusCourseId),
    enabled: !!syllabusCourseId,
  });
  const sessions = sessionsData?.sessions ?? [];
  const [progress, setProgress] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState('');
  const [uploadResult, setUploadResult] = useState<{ material_id: string; version_no: number; course_id?: string } | null>(null);
  const [uploadedCount, setUploadedCount] = useState(0);
  const [versions, setVersions] = useState<MaterialVersion[]>([]);
  const [stalled, setStalled] = useState('');
  const [pollInterval, setPollInterval] = useState<ReturnType<typeof setInterval> | null>(null);

  // Stop polling after this long if a version never reaches a terminal status,
  // so a stuck job shows a "still processing" notice instead of an infinite spinner.
  const POLL_INTERVAL_MS = 3000;
  const POLL_TIMEOUT_MS = 15 * 60 * 1000; // 15 min — covers a large (~200pg) reading
  const MAX_BATCH = 10; // upload up to 10 files at once; each becomes its own material

  // Poll every uploaded material until all are ready/failed (or we time out).
  const pollBatch = (materialIds: string[]) => {
    const startedAt = Date.now();
    const interval = setInterval(async () => {
      try {
        const perMaterial = await Promise.all(
          materialIds.map((id) => listVersions(DEFAULT_ORG, id).catch(() => [])));
        // One row per material: its latest version (highest version_no).
        const latest = perMaterial
          .map((vers) => vers.slice().sort((a, b) => b.version_no - a.version_no)[0])
          .filter(Boolean) as MaterialVersion[];
        setVersions(latest);
        const allTerminal = latest.length === materialIds.length &&
          latest.every((v) => v.status === 'ready' || v.status === 'failed');
        if (allTerminal) {
          clearInterval(interval);
          setPollInterval(null);
        } else if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
          clearInterval(interval);
          setPollInterval(null);
          setStalled('Still processing — large files can take a few minutes. '
            + 'You can leave this page; the status will update on the course materials list when it finishes.');
        }
      } catch {
        // ignore polling errors
      }
    }, POLL_INTERVAL_MS);
    setPollInterval(interval);
  };

  const handleFilesSelected = async (files: File[]) => {
    if (files.length === 0 || !courseName.trim()) return;
    // A course has a single syllabus; batch applies to the materials path only.
    const batch = isSyllabus ? files.slice(0, 1) : files.slice(0, MAX_BATCH);

    setUploading(true);
    setError('');
    setStalled('');
    setSuccess(false);
    setVersions([]);

    let batchSessionId = sessionChoice !== 'new' ? sessionChoice : undefined;
    const sessionDate = sessionChoice === 'new' ? (newSessionDate || undefined) : undefined;
    const materialIds: string[] = [];
    const failures: string[] = [];
    let firstResult: typeof uploadResult = null;

    // Upload sequentially so progress is monotonic and one bad file doesn't abort
    // the rest; each file shares the batch's topic + session. For a "+ New session"
    // batch the first upload creates the session; the rest reuse its id so all
    // files land under ONE session (not one per file).
    for (let i = 0; i < batch.length; i++) {
      const file = batch[i];
      const base = Math.round((i / batch.length) * 100);
      try {
        const result = await uploadMaterial(
          DEFAULT_ORG, courseName, file,
          (pct) => setProgress(base + Math.round(pct / batch.length)),
          topic, batchSessionId, sessionDate, isSyllabus);
        if (!batchSessionId && result.session_id) batchSessionId = result.session_id;
        materialIds.push(result.material_id);
        if (!firstResult) firstResult = result;
        if (isSyllabus && syllabusCourseId) {
          try {
            await setSyllabus(syllabusCourseId, {
              material_id: result.material_id,
              material_version_id: result.material_version_id,
              file_name: file.name,
            });
          } catch { /* non-fatal: the material still uploaded */ }
        }
      } catch (err) {
        failures.push(`${file.name}: ${err instanceof Error ? err.message : 'upload failed'}`);
      }
    }

    setProgress(100);
    setUploading(false);
    if (failures.length) {
      setError(failures.length === batch.length
        ? `Upload failed. ${failures[0]}`
        : `${failures.length} of ${batch.length} files failed to upload: ${failures.join('; ')}`);
    }
    if (materialIds.length) {
      setUploadResult(firstResult);
      setUploadedCount(materialIds.length);
      setSuccess(true);
      pollBatch(materialIds);
    }
  };

  const statusColor = (status: string) => {
    switch (status) {
      case 'ready': return 'text-green-700 bg-green-50 border-green-200';
      case 'failed': return 'text-red-700 bg-red-50 border-red-200';
      case 'pending': case 'uploaded': return 'text-yellow-700 bg-yellow-50 border-yellow-200';
      default: return 'text-blue-700 bg-blue-50 border-blue-200';
    }
  };

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">{isSyllabus ? 'Upload Syllabus' : 'Upload Course Materials'}</h1>
        <p className="text-sm text-gray-500 mt-1">
          {isSyllabus
            ? 'Upload the course syllabus. It is stored as a viewable document (and can inform the concept graph).'
            : 'Upload course materials for your class session. The pipeline extracts, chunks, and embeds them automatically.'}
        </p>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 p-6 space-y-5">
        {/* Course name */}
        <div>
          <label htmlFor="course" className="block text-sm font-medium text-gray-700 mb-1">
            Course Name
          </label>
          <input
            id="course"
            type="text"
            value={courseName}
            onChange={(e) => setCourseName(e.target.value)}
            readOnly={coursePrefilled}
            className={`w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent ${coursePrefilled ? 'bg-gray-50 text-gray-600 cursor-not-allowed' : ''}`}
            placeholder="e.g. CS101-Intro-to-ML"
          />
          <p className="text-xs text-gray-400 mt-1">
            {coursePrefilled ? 'From the selected course.' : 'Course name/code. Auto-created if new.'}
          </p>
        </div>

        {/* Topic / Class Session */}
        <div>
          <label htmlFor="topic" className="block text-sm font-medium text-gray-700 mb-1">
            Topic / Class Session
          </label>
          <input
            id="topic"
            type="text"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            placeholder="e.g. Week 3 — Supervised Learning"
          />
          <p className="text-xs text-gray-400 mt-1">Titles the class session (the heading files are grouped under). Files keep their own names. Ignored if you pick an existing session below.</p>
        </div>

        {/* Class session — a material is always attached to a session */}
        {syllabusCourseId && (
          <div>
            <label htmlFor="session" className="block text-sm font-medium text-gray-700 mb-1">Class Session</label>
            <select
              id="session"
              value={sessionChoice}
              onChange={(e) => setSessionChoice(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="new">+ New session</option>
              {sessions.map((s) => (
                <option key={s.session_id} value={s.session_id}>
                  {s.session_date ? new Date(s.session_date + 'T00:00:00').toLocaleDateString() : 'Undated session'}
                  {s.session_document ? ` — ${s.session_document.slice(0, 40)}` : ''}
                </option>
              ))}
            </select>
            {sessionChoice === 'new' && (
              <input
                type="date"
                value={newSessionDate}
                onChange={(e) => setNewSessionDate(e.target.value)}
                className="mt-2 w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                aria-label="New session date (optional)"
              />
            )}
            <p className="text-xs text-gray-400 mt-1">This material is attached to a class session — pick an existing one or create a new session (optional date).</p>
          </div>
        )}

        {/* File upload */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">
            {isSyllabus ? 'File' : 'Files'}
          </label>
          {!courseName.trim() ? (
            <div className="border-2 border-dashed border-gray-200 rounded-xl p-8 text-center text-sm text-gray-400">
              Enter a course name above to enable upload
            </div>
          ) : (
            <FileUpload
              accept=".pdf,.docx,.doc,.rtf,.txt,.pptx,.md"
              multiple={!isSyllabus}
              maxFiles={isSyllabus ? 1 : MAX_BATCH}
              onFilesSelected={handleFilesSelected}
              uploading={uploading}
              progress={progress}
            />
          )}
          {!isSyllabus && (
            <p className="text-xs text-gray-400 mt-1">Upload up to {MAX_BATCH} files at once — each is processed as its own material.</p>
          )}
        </div>

        {/* Success */}
        {success && uploadResult && (
          <div className="flex items-center gap-3 p-4 bg-green-50 border border-green-200 rounded-lg">
            <CheckCircle className="w-5 h-5 text-green-600 shrink-0" />
            <div>
              <p className="text-sm font-medium text-green-800">
                {uploadedCount > 1
                  ? `Uploaded ${uploadedCount} files — processing below.`
                  : 'Upload successful — processing below.'}
              </p>
            </div>
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="flex items-center gap-3 p-4 bg-red-50 border border-red-200 rounded-lg">
            <AlertCircle className="w-5 h-5 text-red-600 shrink-0" />
            <div>
              <p className="text-sm font-medium text-red-800">Upload failed</p>
              <p className="text-xs text-red-600 mt-0.5">{error}</p>
            </div>
          </div>
        )}

        {/* Stalled — polling gave up but the job may still finish server-side */}
        {stalled && (
          <div className="flex items-center gap-3 p-4 bg-amber-50 border border-amber-200 rounded-lg">
            <AlertCircle className="w-5 h-5 text-amber-600 shrink-0" />
            <p className="text-xs text-amber-700">{stalled}</p>
          </div>
        )}

        {/* Pipeline status (polling) */}
        {versions.length > 0 && (
          <div className="space-y-2">
            <h3 className="text-sm font-medium text-gray-700">Pipeline Status</h3>
            {versions.map((v) => (
              <div key={v.material_version_id} className="space-y-1">
                <div className="flex items-center gap-3 text-sm">
                  <span className="font-mono text-gray-500">v{v.version_no}</span>
                  <span className={`px-2 py-0.5 rounded border text-xs font-medium ${statusColor(v.status)}`}>
                    {v.status}
                  </span>
                  <span className="text-gray-400">{v.file_name}</span>
                  {v.status !== 'ready' && v.status !== 'failed' && (
                    <Loader2 className="w-3 h-3 animate-spin text-blue-500" />
                  )}
                </div>
                {v.status === 'failed' && (
                  <div className="flex items-start gap-2 rounded-md bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700">
                    <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                    <span>{v.error?.message || `Ingestion failed for "${v.file_name}". Please try a different file.`}</span>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Next steps */}
        {success && (
          <div className="flex flex-wrap gap-3">
            {(() => {
              // Prefer the course id resolved by the upload itself (covers standalone
              // uploads where the URL only carried the course name); fall back to the
              // URL's courseId, then the dashboard.
              const gid = uploadResult?.course_id || syllabusCourseId;
              // After a syllabus upload the course is now unlocked — send the
              // professor to the Sessions tab where they can auto-create sessions
              // from it. Other uploads go to the concept graph as before.
              if (isSyllabus) {
                return (
                  <Link
                    to={gid ? `/professor/courses/${gid}?tab=sessions` : '/professor/dashboard'}
                    className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
                  >
                    <Calendar className="w-4 h-4" /> Continue — create sessions →
                  </Link>
                );
              }
              return (
                <Link
                  to={gid ? `/professor/courses/${gid}?tab=graph` : '/professor/dashboard'}
                  className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
                >
                  <Network className="w-4 h-4" /> View Concept Graph →
                </Link>
              );
            })()}
            <button
              onClick={() => {
                setSuccess(false);
                setProgress(0);
                setUploadResult(null);
                setUploadedCount(0);
                setVersions([]);
                setStalled('');
                if (pollInterval) clearInterval(pollInterval);
              }}
              className="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors"
            >
              Upload Another
            </button>
          </div>
        )}
      </div>

      {/* How it works */}
      <div className="bg-blue-50 border border-blue-100 rounded-xl p-5">
        <h3 className="text-sm font-medium text-blue-900 mb-2">What happens after upload</h3>
        <ol className="text-sm text-blue-700 space-y-1 list-decimal list-inside">
          <li>File lands in S3 at <code className="text-xs">org/course/materials/id/v1/filename</code></li>
          <li>Worker picks up the job from SQS</li>
          <li>Extracts text → structure-aware chunking</li>
          <li>Embeds chunks via Bedrock Titan v2 → pgvector</li>
          <li>Status flips to <span className="font-medium">ready</span></li>
        </ol>
      </div>
    </div>
  );
}
