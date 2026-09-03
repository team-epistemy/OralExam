import { useCallback, useState } from 'react';
import { Upload, File as FileIcon, X, CheckCircle, AlertCircle } from 'lucide-react';

interface FileUploadProps {
  accept?: string;
  multiple?: boolean;
  maxFiles?: number;
  onFilesSelected: (files: File[]) => void;
  uploading?: boolean;
  progress?: number;
}

export default function FileUpload({ accept, multiple = false, maxFiles, onFilesSelected, uploading = false, progress = 0 }: FileUploadProps) {
  const [dragOver, setDragOver] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [formatError, setFormatError] = useState('');

  // Extensions allowed, parsed from the `accept` prop (e.g. ".pdf,.docx").
  const allowed = (accept || '').split(',').map((e) => e.trim().toLowerCase()).filter(Boolean);
  const supportedLabel = allowed.map((e) => e.replace('.', '').toUpperCase()).join(', ');
  const unsupported = (files: File[]) =>
    allowed.length > 0 && files.some((f) => !allowed.some((ext) => f.name.toLowerCase().endsWith(ext)));

  // Validate count + format; the drag-drop path bypasses the input's accept
  // filter, so both handlers must gate here before emitting the files. Too many
  // files rejects the whole selection rather than silently truncating.
  //
  // In multi-file mode, selections ACCUMULATE: picking files one at a time keeps
  // the earlier cards on screen (deduped) instead of replacing them, so a prior
  // document never appears to vanish. Only the newly-added files are emitted to
  // the parent, so already-uploaded files aren't uploaded again. Single-file
  // mode (e.g. syllabus) keeps replace semantics.
  const fileKey = (f: File) => `${f.name}:${f.size}:${f.lastModified}`;
  const accept_files = useCallback((incoming: File[]) => {
    if (incoming.length === 0) return;

    if (!multiple) {
      const one = incoming.slice(0, 1);
      if (unsupported(one)) {
        setFormatError(`File format not supported. Supported formats: ${supportedLabel}.`);
        return;
      }
      setFormatError('');
      setSelectedFiles(one);
      onFilesSelected(one);
      return;
    }

    const existing = new Set(selectedFiles.map(fileKey));
    const fresh = incoming.filter((f) => !existing.has(fileKey(f)));
    if (fresh.length === 0) return; // all duplicates — nothing new to add
    const merged = [...selectedFiles, ...fresh];
    if (maxFiles && merged.length > maxFiles) {
      setFormatError(`Select at most ${maxFiles} files (you now have ${merged.length}).`);
      return;
    }
    if (unsupported(fresh)) {
      setFormatError(`File format not supported. Supported formats: ${supportedLabel}.`);
      return;
    }
    setFormatError('');
    setSelectedFiles(merged);
    onFilesSelected(fresh);
  }, [multiple, selectedFiles, onFilesSelected, supportedLabel, maxFiles]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      accept_files(Array.from(e.dataTransfer.files));
    },
    [accept_files]
  );

  const handleFileInput = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      accept_files(Array.from(e.target.files || []));
      // Clear the input so re-selecting the same file (e.g. after removing it)
      // still fires onChange.
      e.target.value = '';
    },
    [accept_files]
  );

  const removeFile = useCallback((index: number) => {
    setSelectedFiles((prev) => prev.filter((_, i) => i !== index));
  }, []);

  return (
    <div className="space-y-3">
      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`relative border-2 border-dashed rounded-lg p-8 text-center transition-colors cursor-pointer
          ${dragOver ? 'border-blue-500 bg-blue-50' : 'border-gray-300 hover:border-gray-400'}
          ${uploading ? 'pointer-events-none opacity-60' : ''}
        `}
      >
        <input
          type="file"
          accept={accept}
          multiple={multiple}
          onChange={handleFileInput}
          className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
          disabled={uploading}
        />
        <Upload className="w-8 h-8 mx-auto text-gray-400 mb-3" />
        <p className="text-sm text-gray-600">
          <span className="font-medium text-blue-600">Click to upload</span> or drag and drop
        </p>
        <p className="text-xs text-gray-400 mt-1">{supportedLabel ? `${supportedLabel} files` : 'Upload a file'}</p>
      </div>

      {formatError && (
        <div className="flex items-center gap-2 p-3 bg-red-50 border border-red-200 rounded-lg">
          <AlertCircle className="w-4 h-4 text-red-600 shrink-0" />
          <p className="text-sm text-red-700">{formatError}</p>
        </div>
      )}

      {selectedFiles.length > 0 && (
        <div className="space-y-2">
          {selectedFiles.map((file, i) => (
            <div key={i} className="flex items-center gap-3 p-3 bg-gray-50 rounded-lg">
              <FileIcon className="w-4 h-4 text-gray-500 shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-gray-700 truncate">{file.name}</p>
                <p className="text-xs text-gray-400">{(file.size / 1024 / 1024).toFixed(2)} MB</p>
              </div>
              {uploading ? (
                progress >= 100 ? (
                  <CheckCircle className="w-4 h-4 text-green-500 shrink-0" />
                ) : (
                  <span className="text-xs text-blue-600 font-medium shrink-0">{progress}%</span>
                )
              ) : (
                <button onClick={() => removeFile(i)} className="p-1 hover:bg-gray-200 rounded shrink-0">
                  <X className="w-4 h-4 text-gray-400" />
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {uploading && (
        <div className="w-full bg-gray-200 rounded-full h-2">
          <div
            className="bg-blue-600 h-2 rounded-full transition-all duration-300"
            style={{ width: `${progress}%` }}
          />
        </div>
      )}
    </div>
  );
}
