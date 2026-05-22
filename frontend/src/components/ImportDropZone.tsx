import type { ImportResult } from '../types';
import * as api from '../api/client';

interface Props {
  onImported: (result: ImportResult) => void;
}

export default function ImportDropZone({ onImported }: Props) {
  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (!file || !file.name.endsWith('.epub')) return;
    try {
      const result = await api.importEpub(file);
      onImported(result);
    } catch {
      // handled silently, could add error state
    }
  };

  return (
    <div
      className="import-drop-zone"
      onDragOver={(e) => e.preventDefault()}
      onDrop={handleDrop}
    >
      <p>Drop an epub file here to import</p>
    </div>
  );
}
