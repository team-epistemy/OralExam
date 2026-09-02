import { lazy, Suspense } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Network, X, Loader2, AlertTriangle } from 'lucide-react';
import { get } from '../api/client';

const ConceptGraphCanvas = lazy(() => import('./ConceptGraphCanvas'));

/**
 * Full-screen modal showing the concept graph for a SINGLE document
 * (mapping 1: document -> concept-list -> per-document graph). Read-only —
 * curation and rebuild live on the cumulative course graph in the Graph tab.
 * `materialVersionId` is the row's material_version_id (the course-scoped
 * materials list already keys rows by material_version_id).
 */
export default function DocumentGraphModal({
  materialVersionId,
  fallbackName,
  onClose,
}: {
  materialVersionId: string;
  fallbackName: string;
  onClose: () => void;
}) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['material-graph', materialVersionId],
    queryFn: () => get<any>(`/api/materials/${materialVersionId}/graph`),
    retry: false,
  });

  const name = data?.source || fallbackName;
  const concepts = data?.concepts || [];
  const edges = data?.edges || [];
  const nodeCount = data?.node_count ?? concepts.length;
  const edgeCount = data?.edge_count ?? edges.length;
  const empty = !isLoading && !isError && concepts.length === 0;

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
            <Network className="w-4 h-4 text-gray-400 flex-shrink-0" />
            <p className="text-sm font-medium text-gray-900 truncate">{name}</p>
            {!isLoading && !isError && !empty && (
              <span className="text-xs text-gray-400 flex-shrink-0">
                · {nodeCount} concepts · {edgeCount} links
              </span>
            )}
          </div>
          <button onClick={onClose} className="p-1.5 text-gray-500 hover:bg-gray-100 rounded transition-colors" title="Close">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="flex-1 bg-gray-50 min-h-0">
          {isLoading ? (
            <div className="h-full flex items-center justify-center">
              <Loader2 className="w-8 h-8 text-blue-600 animate-spin" />
            </div>
          ) : isError ? (
            <div className="h-full flex flex-col items-center justify-center gap-2 text-center px-6">
              <AlertTriangle className="w-8 h-8 text-amber-500" />
              <p className="text-sm text-gray-600">Couldn't load this document's concept graph.</p>
            </div>
          ) : empty ? (
            <div className="h-full flex flex-col items-center justify-center gap-2 text-center px-6">
              <Network className="w-8 h-8 text-gray-300" />
              <p className="text-sm text-gray-600">No concepts have been extracted from this document yet.</p>
              <p className="text-xs text-gray-400">Rebuild the course graph from the Graph tab to extract concepts.</p>
            </div>
          ) : (
            <div className="h-full p-3">
              <Suspense fallback={<div className="h-full flex items-center justify-center"><Loader2 className="w-8 h-8 text-blue-600 animate-spin" /></div>}>
                <ConceptGraphCanvas concepts={concepts} edges={edges} />
              </Suspense>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
