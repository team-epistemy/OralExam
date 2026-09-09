import { useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { CheckCircle, AlertCircle, Home } from 'lucide-react';
import { uploadMaterial } from '../../api/materials';
import { setSyllabus } from '../../api/courses';
import FileUpload from '../../components/FileUpload';
import { DEFAULT_ORG } from '../../config';

export default function UploadMaterial() {
  const [params] = useSearchParams();
  const isSyllabus = params.get('syllabus') === '1';
  // `courseId` in the URL is the course context (from the Materials tab link).
  const courseIdParam = params.get('courseId') || '';
  const coursePrefilled = !!params.get('course');
  const [courseName, setCourseName] = useState(params.get('course') || '');
  // Materials stand alone under the course, identified by their topics (extracted
  // into the concept graph) — no class session is chosen or created at upload.
  const [progress, setProgress] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState('');
  const [uploadResult, setUploadResult] = useState<{ material_id: string; version_no: number; course_id?: string } | null>(null);
  const [uploadedCount, setUploadedCount] = useState(0);
  const [uploaderKey, setUploaderKey] = useState(0);
  // Every material uploaded on this page (across one-at-a-time selections) — used
  // for the uploaded count. Ingest + concept-graph build happen server-side; this
  // view shows only the upload progress, not the pipeline status.
  const uploadedIdsRef = useRef<string[]>([]);

  const MAX_BATCH = 10; // upload up to 10 files at once; each becomes its own material

  const handleFilesSelected = async (files: File[]) => {
    if (files.length === 0 || !courseName.trim()) return;
    // A course has a single syllabus; batch applies to the materials path only.
    const batch = isSyllabus ? files.slice(0, 1) : files.slice(0, MAX_BATCH);

    setUploading(true);
    setError('');
    setSuccess(false);
    if (isSyllabus) {
      uploadedIdsRef.current = [];
    }

    const materialIds: string[] = [];
    const failures: string[] = [];
    let firstResult: typeof uploadResult = null;

    // Upload sequentially so progress is monotonic and one bad file doesn't abort
    // the rest. Materials are not attached to any class session.
    for (let i = 0; i < batch.length; i++) {
      const file = batch[i];
      const base = Math.round((i / batch.length) * 100);
      try {
        const result = await uploadMaterial(
          DEFAULT_ORG, courseName, file,
          (pct) => setProgress(base + Math.round(pct / batch.length)),
          undefined, undefined, undefined, isSyllabus);
        materialIds.push(result.material_id);
        if (!firstResult) firstResult = result;
        if (isSyllabus && courseIdParam) {
          try {
            await setSyllabus(courseIdParam, {
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
      uploadedIdsRef.current = [...uploadedIdsRef.current, ...materialIds];
      setUploadResult((prev) => prev ?? firstResult);
      setUploadedCount(uploadedIdsRef.current.length);
      setSuccess(true);
    }
  };

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">{isSyllabus ? 'Upload Syllabus' : 'Upload Course Materials'}</h1>
        <p className="text-sm text-gray-500 mt-1">
          {isSyllabus
            ? 'Upload the course syllabus. It is stored as a viewable document.'
            : 'Upload course materials. The pipeline extracts, chunks, and embeds them, and maps their topics automatically.'}
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
              key={uploaderKey}
              // Materials accept spreadsheets/CSV too (stored + searchable, but not
              // graphed); a syllabus stays prose-only (viewable document).
              accept={isSyllabus
                ? '.pdf,.docx,.doc,.rtf,.txt,.pptx,.md'
                : '.pdf,.docx,.doc,.rtf,.txt,.pptx,.md,.csv,.xlsx'}
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

        {/* Next steps */}
        {success && (() => {
          // Prefer the course id resolved by the upload itself (covers standalone
          // uploads where the URL only carried the course name); fall back to the
          // URL's courseId, then the dashboard.
          const gid = uploadResult?.course_id || courseIdParam;
          const courseHome = gid ? `/professor/courses/${gid}` : '/professor/dashboard';
          return (
            <div className="flex flex-wrap gap-3">
              <button
                onClick={() => {
                  setSuccess(false);
                  setProgress(0);
                  setUploadResult(null);
                  setUploadedCount(0);
                  uploadedIdsRef.current = [];
                  // Remount FileUpload to clear its accumulated file cards.
                  setUploaderKey((k) => k + 1);
                }}
                className="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors"
              >
                Upload Another
              </button>
              {/* Back to the course dashboard. */}
              <Link
                to={courseHome}
                className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
              >
                <Home className="w-4 h-4" /> Course Home
              </Link>
            </div>
          );
        })()}

        {/* Always offer a way back to the course, even before any upload. The
            success state above shows its own Course Home (next to Upload Another). */}
        {!success && (
          <div className="flex">
            <Link
              to={courseIdParam ? `/professor/courses/${courseIdParam}` : '/professor/dashboard'}
              className="inline-flex items-center gap-2 px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors"
            >
              <Home className="w-4 h-4" /> Course Home
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}
