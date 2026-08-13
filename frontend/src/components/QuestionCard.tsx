import { Tag } from 'lucide-react';
import StatusBadge from './StatusBadge';

interface QuestionCardProps {
  question: {
    id: string;
    text: string;
    concept_tags: string[];
    difficulty: string;
    status: string;
  };
  actions?: React.ReactNode;
}

export default function QuestionCard({ question, actions }: QuestionCardProps) {
  return (
    <div className="border border-gray-200 rounded-lg p-4 hover:border-gray-300 transition-colors">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <p className="text-gray-900 text-sm leading-relaxed">{question.text}</p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {question.concept_tags.map((tag) => (
              <span
                key={tag}
                className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-blue-50 text-blue-700 rounded-full"
              >
                <Tag className="w-3 h-3" />
                {tag}
              </span>
            ))}
            <StatusBadge status={question.difficulty} />
            <StatusBadge status={question.status} />
          </div>
        </div>
        {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
      </div>
    </div>
  );
}
