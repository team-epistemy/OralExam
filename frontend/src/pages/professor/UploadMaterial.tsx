import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { CheckCircle, AlertCircle, Loader2, Network } from 'lucide-react';
import { uploadMaterial, listVersions } from '../../api/materials';
import type { MaterialVersion } from '../../api/materials';
import { setSyllabus } from '../../api/courses';
import FileUpload from '../../components/FileUpload';
import { DEFAULT_ORG } from '../../config';

export default function UploadMaterial() {
  const [params] = useSearchParams();
  const isSyllabus = params.get('syllabus') === '1';
  const syllabusCourseId = params.get('courseId') || '';
  const [orgName, setOrgName] = useState(DEFAULT_ORG);
  const coursePrefilled = !!params.get('course');
  const [courseName, setCourseName] = useState(params.get('course') || '');
  const [topic, setTopic] = useState('');
  const [progress, setProgress] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState('');
  const [uploadResult, setUploadResult] = useState<{ material_id: string; version_no: number } | null>(null);
  const [versions, setVersions] = useState<MaterialVersion[]>([]);
  const [pollInterval, setPollInterval] = useState<ReturnType<typeof setInterval> | null>(null);

  const handleFilesSelected = async (files: File[]) => {
    if (files.length === 0 || !courseName.trim()) return;

    setUploading(true);
    setError('');
    setSuccess(false);
    setVersions([]);

    try {
      const result = await uploadMaterial(orgName, courseName, files[0], setProgress, topic);
      setUploadResult(result);
      setSuccess(true);

      // If this upload is the course syllabus, mark it so (best-effort).
      if (isSyllabus && syllabusCourseId) {
        try {
          await setSyllabus(syllabusCourseId, {
            material_id: result.material_id,
            material_version_id: result.material_version_id,
            file_name: files[0].name,
          });
        } catch {
          // non-fatal: the material still uploaded
        }
      }

      // Start polling for version status
      const interval = setInterval(async () => {
        try {
          const vers = await listVersions(orgName, result.material_id);
          setVersions(vers);
          const latest = vers.find((v) => v.material_version_id === result.material_version_id);
          if (latest && (latest.status === 'ready' || latest.status === 'failed')) {
            clearInterval(interval);
            setPollInterval(null);
          }
        } catch {
          // ignore polling errors
        }
      }, 3000);
      setPollInterval(interval);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed');
    } finally {
      setUploading(false);
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
        {/* Org name */}
        <div>
          <label htmlFor="org" className="block text-sm font-medium text-gray-700 mb-1">
            Organization Name
          </label>
          <input
            id="org"
            type="text"
            value={orgName}
            onChange={(e) => setOrgName(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            placeholder="e.g. epistemy"
          />
          <p className="text-xs text-gray-400 mt-1">Your organization/university. Auto-created if new.</p>
        </div>

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
          <p className="text-xs text-gray-400 mt-1">Optional label for this material. Defaults to the file name if left blank.</p>
        </div>

        {/* File upload */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">File</label>
          {!courseName.trim() ? (
            <div className="border-2 border-dashed border-gray-200 rounded-xl p-8 text-center text-sm text-gray-400">
              Enter a course name above to enable upload
            </div>
          ) : (
            <FileUpload
              accept=".pdf,.docx,.txt,.pptx,.md"
              onFilesSelected={handleFilesSelected}
              uploading={uploading}
              progress={progress}
            />
          )}
        </div>

        {/* Success */}
        {success && uploadResult && (
          <div className="flex items-center gap-3 p-4 bg-green-50 border border-green-200 rounded-lg">
            <CheckCircle className="w-5 h-5 text-green-600 shrink-0" />
            <div>
              <p className="text-sm font-medium text-green-800">
                Upload successful — version {uploadResult.version_no}
              </p>
              <p className="text-xs text-green-600 mt-0.5">
                Material: {uploadResult.material_id}
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

        {/* Pipeline status (polling) */}
        {versions.length > 0 && (
          <div className="space-y-2">
            <h3 className="text-sm font-medium text-gray-700">Pipeline Status</h3>
            {versions.map((v) => (
              <div key={v.material_version_id} className="flex items-center gap-3 text-sm">
                <span className="font-mono text-gray-500">v{v.version_no}</span>
                <span className={`px-2 py-0.5 rounded border text-xs font-medium ${statusColor(v.status)}`}>
                  {v.status}
                </span>
                <span className="text-gray-400">{v.file_name}</span>
                {v.status !== 'ready' && v.status !== 'failed' && (
                  <Loader2 className="w-3 h-3 animate-spin text-blue-500" />
                )}
              </div>
            ))}
          </div>
        )}

        {/* Next steps */}
        {success && (
          <div className="flex flex-wrap gap-3">
            <Link
              to="/professor/graph"
              className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
            >
              <Network className="w-4 h-4" /> View Concept Graph →
            </Link>
            <button
              onClick={() => {
                setSuccess(false);
                setProgress(0);
                setUploadResult(null);
                setVersions([]);
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
