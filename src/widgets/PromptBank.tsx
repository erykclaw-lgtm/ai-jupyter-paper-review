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
  /** The prompt text sent to Claude. Use {url} as placeholder for paper URL(s). */
  template: string;
  /** If true, prompt the user for paper URL(s) before sending */
  needsUrl?: boolean;
  /** Custom placeholder for the URL input (e.g. when multiple URLs are expected) */
  urlPlaceholder?: string;
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
- **Figures**: When a figure from the paper itself (architecture diagram, key results plot, illustrative schematic) explains something better than prose would, extract it directly from the source — the arXiv HTML/PDF or the paper's repo — save it under artifacts/<slug>/, and embed it in the notebook via a relative path with a caption crediting the paper (e.g. "Figure 2 from the paper"). Prefer the authors' own figures for architectures and empirical results; use them alongside your own demos, not instead of them.

For visualizations you generate yourself, keep them inline — use plt.show() or display(), never write separate .png/.svg/.pdf image files (extracted source figures in artifacts/<slug>/ are the one exception).

No exercises needed — focus purely on building deep understanding. Be thorough and long-form; think tutorial-length lecture notes, not a summary.

Before returning: execute every cell of the notebook top-to-bottom in a fresh kernel and confirm they all run without errors. Fix any cell that fails and re-run until the entire notebook runs clean end to end. Do not hand the notebook back to me until every cell executes successfully.`,
  },
  {
    id: 'multi-paper-survey',
    label: 'Multi-Paper Survey',
    icon: '\u{1F4DA}',
    description: 'Synthesize several papers into one unified survey/review notebook',
    needsUrl: true,
    urlPlaceholder: 'Paste paper URLs (comma or newline separated) and press Enter...',
    template: `Create a multi-paper survey and review of these papers: {url}

Fetch each paper (use WebFetch and web search) and produce a SINGLE Jupyter notebook that synthesizes across all of them — not separate per-paper summaries, but one unified survey. Structure it as:

- **Overview & motivation**: What problem space do these papers collectively address, and why does this line of work matter? Frame the common thread that ties them together.
- **Unified background**: A shared "Prerequisites" section covering the math/ML concepts needed across the papers, using one consistent notation you reuse throughout.
- **Comparative walkthrough**: For each core idea, explain the approach, derive the key equations step by step (including hidden or skipped steps), and give the intuition behind the design choices. Where papers tackle the same sub-problem differently, compare them head to head.
- **Comparison tables**: Summarize the papers along the axes that matter — method, assumptions, complexity, datasets, results, trade-offs.
- **Evolution & relationships**: How do these papers build on, contradict, or complement each other? Trace the intellectual lineage and where the field is heading.
- **Code**: Annotated PyTorch snippets that illustrate and contrast the key mechanisms, with inline comments mapping math notation to code variables.
- **Figures**: When a figure from one of the papers (architecture diagram, key results plot, illustrative schematic) explains something better than prose would, extract it directly from the source — the arXiv HTML/PDF or the paper's repo — save it under artifacts/<slug>/, and embed it in the notebook via a relative path with a caption crediting the paper it came from. Side-by-side source figures are especially valuable when comparing how different papers approach the same problem. Use them alongside your own demos, not instead of them.
- **Synthesis & open problems**: Pull the threads together — the unified takeaway, what remains unresolved, and what to read or build next.

Use web search to fill gaps — related work, follow-ups, blog posts, community discussion, errata. For visualizations you generate yourself, keep them inline — use plt.show() or display(), never write separate .png/.svg/.pdf image files (extracted source figures in artifacts/<slug>/ are the one exception). Be thorough and long-form — think a survey-length pedagogical review, not a summary.

Before returning: execute every cell of the notebook top-to-bottom in a fresh kernel and confirm they all run without errors. Fix any cell that fails and re-run until the entire notebook runs clean end to end. Do not hand the notebook back to me until every cell executes successfully.`,
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
                    placeholder={prompt.urlPlaceholder || 'Paste paper URL and press Enter...'}
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
