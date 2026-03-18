import * as React from 'react';
import { useState, useCallback } from 'react';
import { ChatPanel } from './widgets/ChatPanel';
import { ModelSelector } from './widgets/ModelSelector';
import { SessionList } from './widgets/SessionList';
import { NotebookList } from './widgets/NotebookList';
import { PromptBank } from './widgets/PromptBank';
import { getSession } from './services/api';

/* ====== Error Boundary ====== */

interface ErrorBoundaryState {
  hasError: boolean;
  error: string;
}

class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  ErrorBoundaryState
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false, error: '' };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error: error.message };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('Paper Review panel error:', error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{ padding: '20px', textAlign: 'center', color: '#666' }}>
          <h3 style={{ color: '#d32f2f' }}>Panel Error</h3>
          <p style={{ fontSize: '12px' }}>{this.state.error}</p>
          <button
            onClick={() => this.setState({ hasError: false, error: '' })}
            style={{
              padding: '6px 16px',
              border: '1px solid #ccc',
              borderRadius: '4px',
              background: '#fff',
              cursor: 'pointer',
              marginTop: '8px',
            }}
          >
            Recover
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

/* ====== Main Panel ====== */

export function PaperReviewPanel(): React.ReactElement {
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [model, setModel] = useState('claude-sonnet-4-6');
  const [activeTab, setActiveTab] = useState<'chat' | 'notebooks'>('chat');

  // Reference to ChatPanel's sendMessage for prompt bank
  const [pendingPrompt, setPendingPrompt] = useState<string | null>(null);

  const handleSessionSelect = useCallback(async (sessionId: string) => {
    setActiveSessionId(sessionId);

    // Load session details to restore model
    try {
      const session = await getSession(sessionId);
      if (session.model) {
        setModel(session.model);
      }
    } catch (err) {
      console.error('Failed to load session details:', err);
    }
  }, []);

  const handleSessionCreated = useCallback((sessionId: string) => {
    setActiveSessionId(sessionId);
  }, []);

  const handleSessionDeletedFull = useCallback((deletedId: string) => {
    setActiveSessionId(current => {
      if (current === deletedId) {
        return null;
      }
      return current;
    });
  }, []);

  const handlePromptSelect = useCallback((prompt: string) => {
    setPendingPrompt(prompt);
    setActiveTab('chat');
  }, []);

  const handlePromptConsumed = useCallback(() => {
    setPendingPrompt(null);
  }, []);

  return (
    <ErrorBoundary>
      <div className="pr-panel">
        {/* Sidebar controls */}
        <div className="pr-sidebar-controls">
          <ModelSelector value={model} onChange={setModel} />

          <SessionList
            activeSessionId={activeSessionId}
            model={model}
            onSessionSelect={handleSessionSelect}
            onSessionCreated={handleSessionCreated}
            onSessionDeleted={handleSessionDeletedFull}
          />
        </div>

        {/* Tab bar */}
        <div className="pr-tab-bar">
          <button
            className={`pr-tab ${activeTab === 'chat' ? 'pr-tab-active' : ''}`}
            onClick={() => setActiveTab('chat')}
          >
            Chat
          </button>
          <button
            className={`pr-tab ${activeTab === 'notebooks' ? 'pr-tab-active' : ''}`}
            onClick={() => setActiveTab('notebooks')}
          >
            Notebooks
          </button>
        </div>

        {/* Content area */}
        <div className="pr-content-area">
          {activeTab === 'chat' && (
            <ChatPanel
              sessionId={activeSessionId}
              model={model}
              pendingPrompt={pendingPrompt}
              onPromptConsumed={handlePromptConsumed}
            />
          )}

          {activeTab === 'notebooks' && (
            <NotebookList />
          )}
        </div>

        {/* Prompt bank - always visible at bottom when on chat tab */}
        {activeTab === 'chat' && (
          <PromptBank
            onSelectPrompt={handlePromptSelect}
            sessionId={activeSessionId}
          />
        )}
      </div>
    </ErrorBoundary>
  );
}
