import * as React from 'react';
import { useState, useEffect, useCallback } from 'react';
import { listModels, ModelInfo } from '../services/api';

interface ModelSelectorProps {
  value: string;
  onChange: (modelId: string) => void;
}

/** Shown only if the very first fetch fails (e.g. server briefly down). */
const OFFLINE_FALLBACK: ModelInfo[] = [
  { id: 'claude-sonnet-5', name: 'Claude Sonnet 5', tier: 'sonnet' },
  { id: 'claude-fable-5', name: 'Claude Fable 5', tier: 'fable' },
  { id: 'claude-opus-4-8', name: 'Claude Opus 4.8', tier: 'opus' },
  { id: 'claude-haiku-4-5-20251001', name: 'Claude Haiku 4.5', tier: 'haiku' },
  { id: 'gpt-5.6-sol', name: 'GPT-5.6-Sol', tier: 'gpt5' },
  { id: 'gpt-5.5', name: 'GPT-5.5', tier: 'gpt5' },
];

// Re-fetch cadence for a long-lived panel. The backend caches discovery for
// 30 min, so polling every 15 min means new model releases appear in the
// dropdown within ~45 min of Anthropic/OpenAI shipping them — no tab reload.
const REFRESH_INTERVAL_MS = 15 * 60 * 1000;

export function ModelSelector({ value, onChange }: ModelSelectorProps): React.ReactElement {
  const [models, setModels] = useState<ModelInfo[]>([]);

  const refresh = useCallback(() => {
    listModels()
      .then(ms => {
        if (ms && ms.length > 0) setModels(ms);
      })
      .catch(() => {
        // Never clobber a previously fetched live list with the fallback —
        // only seed it when we have nothing at all.
        setModels(prev => (prev.length > 0 ? prev : OFFLINE_FALLBACK));
      });
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, REFRESH_INTERVAL_MS);
    // Also refresh when the user returns to the tab — cheap (backend-cached)
    // and makes "new model just shipped" show up on the next visit.
    const onVisible = () => {
      if (document.visibilityState === 'visible') refresh();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearInterval(id);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [refresh]);

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
