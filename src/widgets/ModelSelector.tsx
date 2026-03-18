import * as React from 'react';
import { useState, useEffect } from 'react';
import { listModels, ModelInfo } from '../services/api';

interface ModelSelectorProps {
  value: string;
  onChange: (modelId: string) => void;
}

export function ModelSelector({ value, onChange }: ModelSelectorProps): React.ReactElement {
  const [models, setModels] = useState<ModelInfo[]>([]);

  useEffect(() => {
    listModels()
      .then(setModels)
      .catch(() => {
        // Fallback if API is unavailable
        setModels([
          { id: 'claude-opus-4-6', name: 'Claude Opus 4.6', tier: 'opus' },
          { id: 'claude-sonnet-4-6', name: 'Claude Sonnet 4.6', tier: 'sonnet' },
          { id: 'claude-haiku-4-5-20251001', name: 'Claude Haiku 4.5', tier: 'haiku' },
        ]);
      });
  }, []);

  return (
    <div className="pr-model-selector">
      <label className="pr-label">Model</label>
      <select
        className="pr-select"
        value={value}
        onChange={e => onChange(e.target.value)}
      >
        {models.map(m => (
          <option key={m.id} value={m.id}>
            {m.name}
          </option>
        ))}
      </select>
    </div>
  );
}
