import * as React from 'react';
import { useState, useCallback } from 'react';

/**
 * A prompt in the prompt bank.
 */
interface Prompt {
  id: string;
  label: string;
  icon: string;
  description: string;
  /** The prompt text sent to Claude. Use {url} as placeholder for paper URL. */
  template: string;
  /** If true, prompt the user for a paper URL before sending */
  needsUrl?: boolean;
}

const PROMPTS: Prompt[] = [
  {
    id: 'guided-walkthrough',
    label: 'Guided Walkthrough',
    icon: '\u{1F9ED}',
    description: 'Step-by-step walkthrough with intuition, code demos, and web research',
    needsUrl: true,
    template: `Fetch this paper and help me go through it: {url}

Create a Jupyter notebook that walks through the paper step by step. For each section:

- **Math**: Go through all of the math carefully. Derive every equation step by step, including any hidden or skipped steps. When common theorems, identities, or techniques are used (e.g. Jensen's inequality, chain rule on expectations, matrix inversion lemma), state them explicitly and walk through their application in context.
- **Intuition**: Explain the reasoning and motivation behind each design choice in detail — why did the authors do it this way? What's the geometric, statistical, or information-theoretic intuition?
- **Code**: Where helpful, include annotated PyTorch code blocks that demonstrate how a mechanism works, with inline comments mapping math notation to code variables.
- **Resources**: Explain all referenced resources, supplementary materials, and prior work, and tie them together with the main contributions. Use web search to gather additional context — related blog posts, follow-up work, community discussion, or errata.

Keep all visualizations inline in the notebook — use plt.show() or display(), never write separate .png/.svg/.pdf image files.

No exercises needed — focus purely on building deep understanding. Be thorough and long-form; think tutorial-length lecture notes, not a summary.`,
  },
];

interface PromptBankProps {
  onSelectPrompt: (prompt: string) => void;
  sessionId: string | null;
}

export function PromptBank({ onSelectPrompt, sessionId }: PromptBankProps): React.ReactElement {
  const [isExpanded, setIsExpanded] = useState(false);
  const [urlInput, setUrlInput] = useState('');
  const [activePromptId, setActivePromptId] = useState<string | null>(null);

  const handleSelect = useCallback((prompt: Prompt) => {
    if (!sessionId) return;

    if (prompt.needsUrl) {
      setActivePromptId(prompt.id);
      return;
    }

    onSelectPrompt(prompt.template);
    setIsExpanded(false);
  }, [sessionId, onSelectPrompt]);

  const handleUrlSubmit = useCallback(() => {
    if (!urlInput.trim() || !activePromptId) return;

    const prompt = PROMPTS.find(p => p.id === activePromptId);
    if (prompt) {
      const filled = prompt.template.replace('{url}', urlInput.trim());
      onSelectPrompt(filled);
    }

    setUrlInput('');
    setActivePromptId(null);
    setIsExpanded(false);
  }, [urlInput, activePromptId, onSelectPrompt]);

  const handleUrlKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleUrlSubmit();
    }
    if (e.key === 'Escape') {
      setActivePromptId(null);
    }
  }, [handleUrlSubmit]);

  return (
    <div className="pr-prompt-bank">
      <button
        className="pr-prompt-bank-toggle"
        onClick={() => setIsExpanded(!isExpanded)}
        disabled={!sessionId}
      >
        <span className="pr-prompt-bank-icon">{'\u26A1'}</span>
        <span>Prompts</span>
        <span className="pr-prompt-bank-arrow">{isExpanded ? '\u25BC' : '\u25B2'}</span>
      </button>

      {isExpanded && (
        <div className="pr-prompt-bank-menu">
          {PROMPTS.map(prompt => (
            <div key={prompt.id}>
              <button
                className="pr-prompt-bank-item"
                onClick={() => handleSelect(prompt)}
                disabled={!sessionId}
              >
                <span className="pr-prompt-item-icon">{prompt.icon}</span>
                <div className="pr-prompt-item-text">
                  <div className="pr-prompt-item-label">{prompt.label}</div>
                  <div className="pr-prompt-item-desc">{prompt.description}</div>
                </div>
              </button>

              {activePromptId === prompt.id && (
                <div className="pr-prompt-url-input">
                  <input
                    type="text"
                    value={urlInput}
                    onChange={e => setUrlInput(e.target.value)}
                    onKeyDown={handleUrlKeyDown}
                    placeholder="Paste paper URL and press Enter..."
                    autoFocus
                  />
                  <button onClick={handleUrlSubmit} disabled={!urlInput.trim()}>
                    Go
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
