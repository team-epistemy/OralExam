import { useQuery } from '@tanstack/react-query';
import { FileText, X, Loader2, Download, AlertTriangle } from 'lucide-react';
import { getMaterialView } from '../api/materials';

/**
 * Full-screen modal that opens an attached document for the professor.
 * `materialId` may be a material_id or a material_version_id. PDFs and images
 * render inline; other types offer a download.
 */
export default function DocumentViewerModal({
  materialId,
  fallbackName,
  onClose,
}: {
  materialId: string;
  fallbackName: string;
  onClose: () => void;
}) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['material-view', materialId],
    queryFn: () => getMaterialView(materialId),
    retry: false,
  });

  const name = data?.file_name || fallbackName;
  const ext = (name.split('.').pop() || '').toLowerCase();
  const isPdf = ext === 'pdf' || data?.source_type === 'pdf';
  const isImage = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp'].includes(ext);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-xl shadow-xl w-full max-w-5xl h-[90vh] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
          <div className="flex items-center gap-2 min-w-0">
            <FileText className="w-4 h-4 text-gray-400 flex-shrink-0" />
            <p className="text-sm font-medium text-gray-900 truncate">{name}</p>
          </div>
          <div className="flex items-center gap-2">
            {data?.url && (
              <a
                href={data.url}
                target="_blank"
                rel="noreferrer"
                download={name}
                className="inline-flex items-center gap-1 text-xs px-2 py-1 border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50 transition-colors"
              >
                <Download className="w-3.5 h-3.5" /> Download
              </a>
            )}
            <button onClick={onClose} className="p-1.5 text-gray-500 hover:bg-gray-100 rounded transition-colors" title="Close">
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>
        <div className="flex-1 bg-gray-100 min-h-0">
          {isLoading ? (
            <div className="h-full flex items-center justify-center">
              <Loader2 className="w-8 h-8 text-blue-600 animate-spin" />
            </div>
          ) : isError || !data?.url ? (
            <div className="h-full flex flex-col items-center justify-center gap-2 text-center px-6">
              <AlertTriangle className="w-8 h-8 text-amber-500" />
              <p className="text-sm text-gray-600">Couldn't load this document. It may still be processing.</p>
            </div>
          ) : isPdf ? (
            <iframe title={name} src={data.url} className="w-full h-full border-0" />
          ) : isImage ? (
            <div className="h-full overflow-auto flex items-center justify-center p-4">
              <img src={data.url} alt={name} className="max-w-full max-h-full object-contain" />
            </div>
          ) : (
            <div className="h-full flex flex-col items-center justify-center gap-3 text-center px-6">
              <FileText className="w-10 h-10 text-gray-300" />
              <p className="text-sm text-gray-600">
                In-browser preview isn't available for <span className="font-medium">.{ext || 'this'}</span> files.
              </p>
              <a
                href={data.url}
                target="_blank"
                rel="noreferrer"
                download={name}
                className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
              >
                <Download className="w-4 h-4" /> Download to view
              </a>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
