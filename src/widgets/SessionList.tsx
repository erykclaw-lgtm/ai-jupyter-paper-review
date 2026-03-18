import * as React from 'react';
import { useState, useEffect, useCallback, useRef } from 'react';
import {
  listSessions,
  createSession,
  deleteSession as apiDeleteSession,
  updateSession,
  SessionInfo,
} from '../services/api';

interface SessionListProps {
  activeSessionId: string | null;
  model: string;
  onSessionSelect: (sessionId: string) => void;
  onSessionCreated: (sessionId: string) => void;
  onSessionDeleted?: (sessionId: string) => void;
}

export function SessionList({
  activeSessionId,
  model,
  onSessionSelect,
  onSessionCreated,
  onSessionDeleted,
}: SessionListProps): React.ReactElement {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  const editInputRef = useRef<HTMLInputElement>(null);

  const loadSessions = useCallback(async () => {
    try {
      const data = await listSessions();
      setSessions(data);
    } catch {
      // Silently fail — sessions might not be available yet
    }
  }, []);

  useEffect(() => {
    loadSessions();

    // Poll every 3 seconds to pick up streaming status changes
    const interval = setInterval(loadSessions, 3000);
    return () => clearInterval(interval);
  }, [loadSessions]);

  // Focus input when editing starts
  useEffect(() => {
    if (editingId && editInputRef.current) {
      editInputRef.current.focus();
      editInputRef.current.select();
    }
  }, [editingId]);

  const handleNew = useCallback(async () => {
    setIsLoading(true);
    try {
      const { session_id } = await createSession(undefined, model);
      onSessionCreated(session_id);
      await loadSessions();
    } catch (err) {
      console.error('Failed to create session:', err);
    } finally {
      setIsLoading(false);
    }
  }, [model, onSessionCreated, loadSessions]);

  const handleDelete = useCallback(
    async (e: React.MouseEvent, sessionId: string) => {
      e.stopPropagation();
      try {
        await apiDeleteSession(sessionId);
        onSessionDeleted?.(sessionId);
        await loadSessions();
      } catch (err) {
        console.error('Failed to delete session:', err);
      }
    },
    [loadSessions, onSessionDeleted]
  );

  const handleDoubleClick = useCallback(
    (e: React.MouseEvent, session: SessionInfo) => {
      e.stopPropagation();
      setEditingId(session.session_id);
      setEditValue(session.paper_title || '');
    },
    []
  );

  const handleRenameSubmit = useCallback(
    async (sessionId: string) => {
      const trimmed = editValue.trim();
      setEditingId(null);
      if (!trimmed) return;

      try {
        await updateSession(sessionId, { paper_title: trimmed });
        await loadSessions();
      } catch (err) {
        console.error('Failed to rename session:', err);
      }
    },
    [editValue, loadSessions]
  );

  const handleRenameKeyDown = useCallback(
    (e: React.KeyboardEvent, sessionId: string) => {
      if (e.key === 'Enter') {
        handleRenameSubmit(sessionId);
      } else if (e.key === 'Escape') {
        setEditingId(null);
      }
    },
    [handleRenameSubmit]
  );

  const formatDate = (dateStr: string): string => {
    if (!dateStr) return '';
    const date = new Date(dateStr);
    return date.toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  return (
    <div className="pr-session-list">
      <div className="pr-session-header">
        <span className="pr-label">Sessions</span>
        <button
          className="pr-btn-small"
          onClick={handleNew}
          disabled={isLoading}
        >
          + New
        </button>
      </div>

      <div className="pr-session-items">
        {sessions.length === 0 && (
          <div className="pr-session-empty">
            No sessions yet. Click "+ New" to start.
          </div>
        )}

        {sessions.map(session => (
          <div
            key={session.session_id}
            className={`pr-session-item ${
              session.session_id === activeSessionId ? 'pr-session-active' : ''
            }`}
            onClick={() => onSessionSelect(session.session_id)}
          >
            {editingId === session.session_id ? (
              <input
                ref={editInputRef}
                className="pr-session-rename-input"
                value={editValue}
                onChange={e => setEditValue(e.target.value)}
                onBlur={() => handleRenameSubmit(session.session_id)}
                onKeyDown={e => handleRenameKeyDown(e, session.session_id)}
                onClick={e => e.stopPropagation()}
                placeholder="Session name..."
              />
            ) : (
              <div
                className="pr-session-title"
                onDoubleClick={e => handleDoubleClick(e, session)}
                title="Double-click to rename"
              >
                {session.streaming && (
                  <span className="pr-session-streaming-dot" title="Claude is working" />
                )}
                {session.paper_title || 'Untitled Review'}
              </div>
            )}
            <div className="pr-session-meta">
              {formatDate(session.created_at)}
              {session.message_count > 0 && ` · ${session.message_count} msgs`}
              {session.streaming && (
                <span className="pr-session-streaming-label"> · Working</span>
              )}
            </div>
            <button
              className="pr-session-delete"
              onClick={e => handleDelete(e, session.session_id)}
              title="Delete session"
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
