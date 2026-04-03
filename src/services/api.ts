/**
 * API client for the Paper Review backend.
 */

import { URLExt } from '@jupyterlab/coreutils';
import { ServerConnection } from '@jupyterlab/services';

function getSettings(): ServerConnection.ISettings {
  return ServerConnection.makeSettings();
}

function apiUrl(path: string): string {
  const settings = getSettings();
  return URLExt.join(settings.baseUrl, 'api', 'paper-review', path);
}

/**
 * Get the XSRF token from cookies (required for POST requests).
 */
function getXsrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)_xsrf=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : '';
}

/**
 * Build headers for streaming fetch requests (includes auth + XSRF).
 */
function streamHeaders(settings: ServerConnection.ISettings): Record<string, string> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (settings.token) {
    headers['Authorization'] = `token ${settings.token}`;
  }
  const xsrf = getXsrfToken();
  if (xsrf) {
    headers['X-XSRFToken'] = xsrf;
  }
  return headers;
}

/**
 * Check that a response is OK, throw a descriptive error otherwise.
 * Tries to extract an error message from the JSON body if present.
 */
async function ensureOk(response: Response, action: string): Promise<void> {
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (body.error) detail = body.error;
    } catch {
      // Response body wasn't JSON
    }
    throw new Error(`${action} failed: ${detail} (HTTP ${response.status})`);
  }
}

export interface StreamEvent {
  type: 'text' | 'tool_use' | 'tool_result' | 'done' | 'error';
  text?: string;
  tool?: string;
  input?: Record<string, unknown>;
  error?: string;
  session_id?: string;
  cost?: number;
  duration?: number;
}

export interface SessionInfo {
  session_id: string;
  paper_title: string | null;
  paper_url: string | null;
  model: string;
  created_at: string;
  message_count: number;
  streaming?: boolean;
}

export interface StreamStatus {
  active: boolean;
  event_count: number;
  accumulated_text?: string;
  active_tools?: string[];
}

export interface SessionDetail {
  session_id: string;
  claude_session_id: string | null;
  paper_url: string | null;
  paper_title: string | null;
  model: string;
  system_prompt: string;
  created_at: string;
  messages: Array<{ role: string; content: string }>;
}

export interface ModelInfo {
  id: string;
  name: string;
  tier: string;
}

export interface NotebookInfo {
  name: string;
  path: string;
  last_modified: string;
  size: number;
}

/**
 * Stream a chat message to Claude Code and yield events.
 */
export async function* streamChat(
  sessionId: string,
  message: string,
  model?: string,
  signal?: AbortSignal
): AsyncGenerator<StreamEvent> {
  const settings = getSettings();
  const url = apiUrl('chat');

  const response = await fetch(url, {
    method: 'POST',
    headers: streamHeaders(settings),
    body: JSON.stringify({
      session_id: sessionId,
      message,
      model,
    }),
    signal,
  });

  if (!response.ok) {
    yield { type: 'error', error: `HTTP ${response.status}: ${response.statusText}` };
    return;
  }

  const reader = response.body?.getReader();
  if (!reader) {
    yield { type: 'error', error: 'No response body' };
    return;
  }

  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const rawLine of lines) {
      const line = rawLine.replace(/\r$/, '');
      if (line.startsWith('data: ')) {
        try {
          const event: StreamEvent = JSON.parse(line.slice(6));
          yield event;
        } catch {
          // Skip malformed events
        }
      }
    }
  }
}

/**
 * Create a new session.
 */
export async function createSession(
  paperUrl?: string,
  model?: string
): Promise<{ session_id: string }> {
  const settings = getSettings();
  const response = await ServerConnection.makeRequest(
    apiUrl('sessions'),
    {
      method: 'POST',
      body: JSON.stringify({ paper_url: paperUrl, model }),
    },
    settings
  );
  await ensureOk(response, 'Create session');
  return response.json();
}

/**
 * List all sessions.
 */
export async function listSessions(): Promise<SessionInfo[]> {
  const settings = getSettings();
  const response = await ServerConnection.makeRequest(
    apiUrl('sessions'),
    {},
    settings
  );
  await ensureOk(response, 'List sessions');
  const data = await response.json();
  return data.sessions;
}

/**
 * Get a session by ID.
 */
export async function getSession(sessionId: string): Promise<SessionDetail> {
  const settings = getSettings();
  const response = await ServerConnection.makeRequest(
    apiUrl(`sessions/${sessionId}`),
    {},
    settings
  );
  await ensureOk(response, 'Get session');
  return response.json();
}

/**
 * Update a session's metadata (e.g. title).
 */
export async function updateSession(
  sessionId: string,
  updates: { paper_title?: string; model?: string }
): Promise<void> {
  const settings = getSettings();
  const response = await ServerConnection.makeRequest(
    apiUrl(`sessions/${sessionId}`),
    {
      method: 'PATCH',
      body: JSON.stringify(updates),
    },
    settings
  );
  await ensureOk(response, 'Update session');
}

/**
 * Delete a session.
 */
export async function deleteSession(sessionId: string): Promise<void> {
  const settings = getSettings();
  const response = await ServerConnection.makeRequest(
    apiUrl(`sessions/${sessionId}`),
    { method: 'DELETE' },
    settings
  );
  await ensureOk(response, 'Delete session');
}

/**
 * List available models.
 */
export async function listModels(): Promise<ModelInfo[]> {
  const settings = getSettings();
  const response = await ServerConnection.makeRequest(
    apiUrl('models'),
    {},
    settings
  );
  await ensureOk(response, 'List models');
  const data = await response.json();
  return data.models;
}

/**
 * List notebooks in the reviews directory.
 */
export async function listNotebooks(): Promise<NotebookInfo[]> {
  const settings = getSettings();
  const response = await ServerConnection.makeRequest(
    apiUrl('notebooks'),
    {},
    settings
  );
  await ensureOk(response, 'List notebooks');
  const data = await response.json();
  return data.notebooks;
}

/**
 * Open a notebook in JupyterLab's main area.
 * Uses JupyterLab's document opening URL.
 */
export async function openNotebook(path: string): Promise<void> {
  // Use JupyterLab's built-in URL pattern to open a file
  const settings = getSettings();
  const openUrl = URLExt.join(settings.baseUrl, 'lab', 'tree', path);
  window.open(openUrl, '_self');
}

/**
 * Cancel an in-progress Claude response for a session.
 */
export async function cancelChat(sessionId: string): Promise<void> {
  const settings = getSettings();
  await fetch(apiUrl('cancel'), {
    method: 'POST',
    headers: streamHeaders(settings),
    body: JSON.stringify({ session_id: sessionId }),
  });
}

/**
 * Get the status of an active stream for a session.
 */
export async function getStreamStatus(sessionId: string): Promise<StreamStatus> {
  const settings = getSettings();
  const response = await ServerConnection.makeRequest(
    apiUrl(`stream-status/${sessionId}`),
    {},
    settings
  );
  await ensureOk(response, 'Get stream status');
  return response.json();
}

/**
 * Subscribe to an active stream for a session. Yields SSE events.
 */
export async function* subscribeStream(
  sessionId: string,
  fromIndex: number = 0,
  signal?: AbortSignal
): AsyncGenerator<StreamEvent> {
  const settings = getSettings();
  const url = apiUrl(`subscribe/${sessionId}`) + `?from=${fromIndex}`;

  const response = await fetch(url, {
    headers: streamHeaders(settings),
    signal,
  });

  if (!response.ok) return;

  const reader = response.body?.getReader();
  if (!reader) return;

  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const rawLine of lines) {
      const line = rawLine.replace(/\r$/, '');
      if (line.startsWith('data: ')) {
        try {
          const event: StreamEvent = JSON.parse(line.slice(6));
          yield event;
        } catch {
          // Skip malformed events
        }
      }
    }
  }
}

/**
 * Export a notebook and trigger browser download.
 */
async function downloadExport(
  endpoint: string,
  path: string,
  extension: string,
): Promise<void> {
  const settings = getSettings();
  const response = await fetch(apiUrl(endpoint), {
    method: 'POST',
    headers: streamHeaders(settings),
    body: JSON.stringify({ path }),
  });

  if (!response.ok) {
    let message = `Export failed: HTTP ${response.status}`;
    try {
      const data = await response.json();
      if (data.error) message = data.error;
    } catch {
      // Response body wasn't JSON — use the default message
    }
    throw new Error(message);
  }

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download =
    path.split('/').pop()?.replace('.ipynb', extension) ||
    `notebook${extension}`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export function exportPdf(path: string): Promise<void> {
  return downloadExport('export-pdf', path, '.pdf');
}

export function exportLatex(path: string): Promise<void> {
  return downloadExport('export-latex', path, '.tex');
}
