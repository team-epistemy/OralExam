import { useParams, Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Loader2, ArrowLeft, AlertCircle, Clock, Download } from 'lucide-react';
import { getExamResults } from '../../api/exam';
import { EDSGauge, EDSBreakdown, EDSExplainer } from '../../components/Eds';
import { downloadTranscriptPdf, resultsTranscript } from '../../transcript';

export default function Results() {
  const { assignmentId = '' } = useParams();

  const { data, isLoading } = useQuery({
    queryKey: ['exam-results', assignmentId],
    queryFn: () => getExamResults(assignmentId),
    enabled: !!assignmentId,
    retry: false,
  });

  const notStarted = data && (data as any).status === 'not_started';
  // A graded item shows no number until the professor releases it; the draft EDS
  // is not a mark. Practice tests come back released, so they are unaffected.
  const awaitingGrade = !!data && data.grade_released === false;

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <Link to="/student/dashboard" className="inline-flex items-center gap-2 text-sm text-gray-500 hover:text-gray-800">
        <ArrowLeft className="w-4 h-4" /> Back to Dashboard
      </Link>

      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-gray-900">Exam Results</h1>
        {data && !notStarted && (
          <button
            onClick={() => { const { meta, entries } = resultsTranscript(data); downloadTranscriptPdf(meta, entries); }}
            className="inline-flex items-center gap-2 px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            <Download className="w-4 h-4" /> Download transcript
          </button>
        )}
      </div>

      {isLoading ? (
        <div className="text-center py-10"><Loader2 className="w-8 h-8 text-blue-600 animate-spin mx-auto" /></div>
      ) : !data || notStarted ? (
        <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
          <span>You haven't completed this exam yet. Take the exam first, then your results will appear here.</span>
        </div>
      ) : (
        <>
          {awaitingGrade ? (
            <div className="bg-white border border-gray-200 rounded-xl p-6">
              <div className="flex items-start gap-3">
                <Clock className="w-5 h-5 text-amber-600 flex-shrink-0 mt-0.5" />
                <div>
                  <p className="text-sm font-semibold text-gray-900">Awaiting your grade</p>
                  <p className="text-sm text-gray-500 mt-0.5">
                    You answered {data.questions_answered} of {data.total_questions} questions. Your
                    score appears here once your professor releases it.
                  </p>
                </div>
              </div>
            </div>
          ) : (
            /* EDS score summary — same gauge, bands, and breakdown as in-exam */
            <div className="bg-white border border-gray-200 rounded-xl p-6">
              <div className="flex flex-col sm:flex-row items-center gap-6">
                <EDSGauge score={data.score ?? 0} />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-semibold text-gray-900">Epistemic Depth Score (EDS)</p>
                  <p className="text-sm text-gray-500 mt-0.5">
                    {data.questions_answered} of {data.total_questions} questions answered
                  </p>
                  <EDSExplainer className="mt-2" />
                </div>
              </div>
              {data.components && (
                <div className="mt-4 pt-4 border-t border-gray-100 max-w-sm">
                  <EDSBreakdown components={data.components} />
                </div>
              )}
            </div>
          )}

          {data.feedback && (
            <div className="bg-blue-50 border border-blue-100 rounded-xl p-4 text-sm text-blue-900">{data.feedback}</div>
          )}

          {/* Per-question breakdown */}
          <div className="bg-white border border-gray-200 rounded-xl p-5">
            <h2 className="font-semibold text-gray-900 mb-3">Question breakdown</h2>
            <ol className="space-y-4">
              {(data.question_results || []).map((q, i) => (
                <li key={q.question_id || i} className="border-b border-gray-100 last:border-0 pb-3 last:pb-0">
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-sm font-medium text-gray-900">{i + 1}. {q.question_text}</p>
                    {q.score !== undefined && (
                      <span className="text-xs font-semibold text-blue-600 whitespace-nowrap">EDS {Math.round(q.score)}/100</span>
                    )}
                  </div>
                  {q.answer && <p className="mt-1 text-sm text-gray-600"><span className="text-gray-400">Your answer:</span> {q.answer}</p>}
                  {q.feedback && <p className="mt-1 text-xs text-gray-500 italic">{q.feedback}</p>}
                </li>
              ))}
            </ol>
          </div>
        </>
      )}
    </div>
  );
}
