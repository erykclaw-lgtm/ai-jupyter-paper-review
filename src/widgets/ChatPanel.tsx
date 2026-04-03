import * as React from 'react';
import { useState, useRef, useEffect, useCallback } from 'react';
import {
  streamChat,
  getSession,
  getStreamStatus,
  subscribeStream,
  cancelChat,
  StreamEvent,
} from '../services/api';
import { MarkdownRenderer } from './MarkdownRenderer';

interface Message {
  role: 'user' | 'assistant';
  content: string;
  toolUses?: Array<{ tool: string; input: Record<string, unknown> }>;
}

const MessageList = React.memo(function MessageList({
  messages,
}: {
  messages: Message[];
}): React.ReactElement {
  return (
    <>
      {messages.map((msg, idx) => (
        <div key={idx} className={`pr-chat-message pr-chat-${msg.role}`}>
          <div className="pr-chat-message-header">
            {msg.role === 'user' ? 'You' : 'Claude'}
          </div>
          <div className="pr-chat-message-content">
            {msg.role === 'assistant' ? (
              <MarkdownRenderer content={msg.content} />
            ) : (
              <p>{msg.content}</p>
            )}
          </div>
          {msg.toolUses && msg.toolUses.length > 0 && (
            <div className="pr-chat-tools-used">
              {msg.toolUses.map((tu, i) => (
                <span key={i} className="pr-tool-badge">
                  {tu.tool}
                </span>
              ))}
            </div>
          )}
        </div>
      ))}
    </>
  );
});

interface ChatPanelProps {
  sessionId: string | null;
  model: string;
  /** A prompt injected from the prompt bank — auto-sends when set */
  pendingPrompt?: string | null;
  onPromptConsumed?: () => void;
}

export function ChatPanel({
  sessionId,
  model,
  pendingPrompt,
  onPromptConsumed,
}: ChatPanelProps): React.ReactElement {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingText, setStreamingText] = useState('');
  const [activeTools, setActiveTools] = useState<string[]>([]);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Local abort — stops the SSE reader but NOT the backend task
  const localAbortRef = useRef<AbortController | null>(null);
  // Track which session we are currently viewing
  const activeSessionRef = useRef<string | null>(sessionId);
  // Track accumulated text for cancel display
  const streamingTextRef = useRef('');
  // rAF guard for batching text state updates
  const textRafRef = useRef(0);

  const scrollRafRef = useRef(0);
  const scrollToBottom = useCallback(() => {
    if (scrollRafRef.current) return;
    scrollRafRef.current = requestAnimationFrame(() => {
      scrollRafRef.current = 0;
      const container = messagesContainerRef.current;
      if (container) {
        container.scrollTop = container.scrollHeight;
      }
    });
  }, []);

  // Clean up pending rAF on unmount
  useEffect(() => {
    return () => {
      if (scrollRafRef.current) cancelAnimationFrame(scrollRafRef.current);
    };
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, streamingText, activeTools, scrollToBottom]);

  // Abort any active stream reader on unmount to prevent state updates
  // on the unmounted component (e.g. if doSend is still running).
  useEffect(() => {
    return () => {
      localAbortRef.current?.abort();
      if (textRafRef.current) cancelAnimationFrame(textRafRef.current);
    };
  }, []);

  // -------------------------------------------------------------------
  // Process a single SSE event — shared between doSend and reconnect
  // -------------------------------------------------------------------
  const processEvent = useCallback(
    (
      event: StreamEvent,
      accText: { current: string },
      toolUses: Array<{ tool: string; input: Record<string, unknown> }>,
      mySessionId: string,
    ) => {
      // Bail if user already switched away
      if (activeSessionRef.current !== mySessionId) return;

      switch (event.type) {
        case 'text':
          accText.current += event.text || '';
          streamingTextRef.current = accText.current;
          // Batch updates to once per animation frame (~60/sec) instead of every chunk
          if (!textRafRef.current) {
            textRafRef.current = requestAnimationFrame(() => {
              textRafRef.current = 0;
              setStreamingText(accText.current);
            });
          }
          break;
        case 'tool_use':
          if (event.tool) {
            toolUses.push({ tool: event.tool, input: event.input || {} });
            setActiveTools(prev => [...prev, event.tool!]);
          }
          break;
        case 'tool_result':
          setActiveTools([]);
          break;
        case 'done':
          if (activeSessionRef.current === mySessionId) {
            setMessages(prev => [
              ...prev,
              {
                role: 'assistant',
                content: accText.current,
                toolUses: toolUses.length > 0 ? [...toolUses] : undefined,
              },
            ]);
            setStreamingText('');
            setActiveTools([]);
            setIsStreaming(false);
          }
          break;
        case 'error':
          if (activeSessionRef.current === mySessionId) {
            setMessages(prev => [
              ...prev,
              { role: 'assistant', content: `**Error:** ${event.error}` },
            ]);
            setStreamingText('');
            setIsStreaming(false);
          }
          break;
      }
    },
    []
  );

  // -------------------------------------------------------------------
  // Load session + reconnect to active stream when session changes
  // -------------------------------------------------------------------
  useEffect(() => {
    activeSessionRef.current = sessionId;

    // Abort previous local subscription (does NOT cancel backend)
    localAbortRef.current?.abort();
    localAbortRef.current = null;

    setStreamingText('');
    setActiveTools([]);
    setIsStreaming(false);

    if (!sessionId) {
      setMessages([]);
      return;
    }

    const controller = new AbortController();
    localAbortRef.current = controller;

    let cancelled = false;

    (async () => {
      try {
        // 1. Load persisted messages
        const session = await getSession(sessionId);
        if (cancelled || activeSessionRef.current !== sessionId) return;

        if (session.messages && session.messages.length > 0) {
          setMessages(
            session.messages.map(m => ({
              role: m.role as 'user' | 'assistant',
              content: m.content,
            }))
          );
        } else {
          setMessages([]);
        }

        // 2. Check for active background stream
        const status = await getStreamStatus(sessionId);
        if (cancelled || activeSessionRef.current !== sessionId) return;

        if (status.active) {
          setIsStreaming(true);
          setStreamingText(status.accumulated_text || '');
          setActiveTools(status.active_tools || []);
          streamingTextRef.current = status.accumulated_text || '';

          // Subscribe to live events from current position
          const accText = { current: status.accumulated_text || '' };
          const toolUses: Array<{ tool: string; input: Record<string, unknown> }> = [];
          let reconnectGotDone = false;

          for await (const event of subscribeStream(
            sessionId,
            status.event_count,
            controller.signal
          )) {
            if (activeSessionRef.current !== sessionId) break;
            if (event.type === 'done') reconnectGotDone = true;
            processEvent(event, accText, toolUses, sessionId);
          }

          // If subscription ended without a done event (network drop,
          // server restart), clean up streaming state and reload
          // persisted messages from disk (backend may have finished).
          if (!reconnectGotDone && activeSessionRef.current === sessionId) {
            setIsStreaming(false);
            setStreamingText('');
            setActiveTools([]);
            // Reload messages — backend persists even when frontend disconnects
            try {
              const refreshed = await getSession(sessionId);
              if (activeSessionRef.current === sessionId && refreshed.messages?.length) {
                setMessages(
                  refreshed.messages.map(m => ({
                    role: m.role as 'user' | 'assistant',
                    content: m.content,
                  }))
                );
              }
            } catch {
              // Best-effort reload
            }
          }
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        console.error('Failed to load session:', err);
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [sessionId, processEvent]);

  // -------------------------------------------------------------------
  // Send a new message
  // -------------------------------------------------------------------
  const doSend = useCallback(
    async (messageText: string) => {
      if (!messageText.trim() || !sessionId || isStreaming) return;

      const userMessage = messageText.trim();
      const mySessionId = sessionId;
      setInput('');
      setMessages(prev => [...prev, { role: 'user', content: userMessage }]);
      setIsStreaming(true);
      setStreamingText('');
      setActiveTools([]);
      streamingTextRef.current = '';

      // Abort any lingering previous stream before starting a new one
      localAbortRef.current?.abort();
      const controller = new AbortController();
      localAbortRef.current = controller;

      const accText = { current: '' };
      const toolUses: Array<{ tool: string; input: Record<string, unknown> }> = [];
      let receivedDone = false;

      try {
        for await (const event of streamChat(
          sessionId,
          userMessage,
          model,
          controller.signal
        )) {
          if (activeSessionRef.current !== mySessionId) break;
          if (event.type === 'done') receivedDone = true;
          processEvent(event, accText, toolUses, mySessionId);
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') {
          // Session switch or cancel — handled by finally block below
        } else if (activeSessionRef.current === mySessionId) {
          setMessages(prev => [
            ...prev,
            { role: 'assistant', content: `**Error:** ${String(err)}` },
          ]);
          receivedDone = true; // error message was added, don't double-add in finally
        }
      } finally {
        if (activeSessionRef.current === mySessionId) {
          // If we never got a done event (SSE drop, abort, session switch),
          // save whatever partial text we have so it doesn't vanish.
          if (!receivedDone && accText.current) {
            setMessages(prev => [
              ...prev,
              {
                role: 'assistant',
                content: accText.current,
                toolUses: toolUses.length > 0 ? [...toolUses] : undefined,
              },
            ]);
            setStreamingText('');
          }
          setIsStreaming(false);
          setActiveTools([]);
        }
        streamingTextRef.current = '';
      }
    },
    [sessionId, isStreaming, model, processEvent]
  );

  // -------------------------------------------------------------------
  // Cancel: abort local reader AND tell backend to stop
  // -------------------------------------------------------------------
  const handleCancel = useCallback(() => {
    localAbortRef.current?.abort();
    if (sessionId) {
      cancelChat(sessionId).catch(() => {});
    }
  }, [sessionId]);

  // Handle pending prompt from prompt bank
  useEffect(() => {
    if (pendingPrompt && sessionId && !isStreaming) {
      doSend(pendingPrompt);
      onPromptConsumed?.();
    }
  }, [pendingPrompt, sessionId, isStreaming, doSend, onPromptConsumed]);

  const handleSend = useCallback(() => {
    doSend(input);
  }, [input, doSend]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  return (
    <div className="pr-chat-panel">
      <div className="pr-chat-messages" ref={messagesContainerRef}>
        {messages.length === 0 && !isStreaming && (
          <div className="pr-chat-empty">
            <h3>Paper Review Assistant</h3>
            <p>Start by creating a session, then use a prompt or ask directly.</p>
            <p>You can:</p>
            <ul>
              <li>Share a paper URL for Claude to fetch and analyze</li>
              <li>Ask about equations, methods, or results</li>
              <li>Request related work searches</li>
              <li>Ask Claude to create review notebooks</li>
              <li>Run code to reproduce results</li>
            </ul>
            <p className="pr-hint">
              Use the <strong>Prompts</strong> bar below for quick actions.
            </p>
          </div>
        )}

        <MessageList messages={messages} />

        {isStreaming && (
          <div className="pr-chat-message pr-chat-assistant">
            <div className="pr-chat-message-header">Claude</div>
            <div className="pr-chat-message-content">
              {streamingText && <MarkdownRenderer content={streamingText} />}
              <div className="pr-activity-indicator">
                <div className="pr-activity-spinner" />
                <span>
                  {activeTools.length > 0
                    ? `Using ${activeTools[activeTools.length - 1]}`
                    : streamingText
                      ? 'Working'
                      : 'Thinking'}
                </span>
              </div>
            </div>
          </div>
        )}

      </div>

      <div className="pr-chat-input-container">
        <textarea
          ref={inputRef}
          className="pr-chat-input"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            sessionId
              ? 'Ask about a paper, paste a URL, or use a prompt below...'
              : 'Create or select a session to start chatting'
          }
          disabled={!sessionId || isStreaming}
        />
        {isStreaming ? (
          <button
            className="pr-chat-send-btn pr-stop-button"
            onClick={handleCancel}
          >
            Stop
          </button>
        ) : (
          <button
            className="pr-chat-send-btn"
            onClick={handleSend}
            disabled={!sessionId || !input.trim()}
          >
            Send
          </button>
        )}
      </div>
    </div>
  );
}
