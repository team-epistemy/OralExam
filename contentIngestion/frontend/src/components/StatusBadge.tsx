interface StatusBadgeProps {
  status: string;
  size?: 'sm' | 'md';
}

const colorMap: Record<string, string> = {
  pending: 'bg-yellow-100 text-yellow-800',
  processing: 'bg-blue-100 text-blue-800',
  ready: 'bg-green-100 text-green-800',
  active: 'bg-green-100 text-green-800',
  approved: 'bg-green-100 text-green-800',
  rejected: 'bg-red-100 text-red-800',
  error: 'bg-red-100 text-red-800',
  draft: 'bg-gray-100 text-gray-800',
  closed: 'bg-gray-100 text-gray-800',
  in_progress: 'bg-blue-100 text-blue-800',
  completed: 'bg-green-100 text-green-800',
  uploading: 'bg-blue-100 text-blue-800',
  abandoned: 'bg-red-100 text-red-800',
  easy: 'bg-green-100 text-green-800',
  medium: 'bg-yellow-100 text-yellow-800',
  hard: 'bg-red-100 text-red-800',
};

export default function StatusBadge({ status, size = 'sm' }: StatusBadgeProps) {
  const colors = colorMap[status] || 'bg-gray-100 text-gray-800';
  const sizeClasses = size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-3 py-1 text-sm';

  return (
    <span className={`inline-flex items-center rounded-full font-medium capitalize ${colors} ${sizeClasses}`}>
      {status.replace(/_/g, ' ')}
    </span>
  );
}
