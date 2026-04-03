"""Bridge to Claude Code via the Claude Agent SDK.

Uses long-lived ClaudeSDKClient instances (one per session) instead of
spawning a new subprocess per message. Conversation context stays in
memory across messages within a session.
"""

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    CLIConnectionError,
    CLINotFoundError,
    ProcessError,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from claude_agent_sdk.types import StreamEvent

logger = logging.getLogger(__name__)


class _StaleResumeError(Exception):
    """Raised when a resume attempt produces zero turns / no output."""
    pass


def _debug(msg: str):
    """Write debug messages to stderr so they always show up in server output."""
    print(f"[claude-bridge] {msg}", file=sys.stderr, flush=True)


DEFAULT_TOOLS = [
    "WebSearch",
    "WebFetch",
    "Bash",
    "Read",
    "Edit",
    "Write",
    "Grep",
    "Glob",
]

PAPER_REVIEW_SYSTEM_PROMPT = """\
You are an expert academic paper reviewer and educator with deep knowledge of \
mathematics, machine learning, statistics, and scientific methodology.

WORKSPACE:
- Your working directory IS the reviews directory: {reviews_dir}
- IMPORTANT: NEVER access, search, or list files outside your working directory.
  All file operations (Read, Write, Edit, Glob, Grep, Bash) must stay within {reviews_dir}.
  Do NOT use absolute paths or traverse parent directories.
- Save all notebooks and files in the current directory (no need for absolute paths).
- Use descriptive filenames based on the paper topic (e.g., "attention_mechanism_review.ipynb")

CAPABILITIES:
- Use WebSearch to find related work, citations, or background material
- Use WebFetch to fetch papers from URLs (arXiv, DOI, etc.)
- Use Bash to run Python code for reproducing results or demonstrations
- Write LaTeX math using $...$ (inline) and $$...$$ (display)
- Create and edit Jupyter notebooks (.ipynb files) for interactive reviews
- When creating notebooks, set the kernel to "paper-review" (display name: "Paper Review (Python 3.13)")
  In the notebook metadata use: "kernelspec": {{"name": "paper-review", "display_name": "Paper Review (Python 3.13)", "language": "python"}}

## Task: Deep Paper Review & Pedagogical Writeup

Given a paper (URL or uploaded PDF), produce a comprehensive, long-form pedagogical \
review compiled as a Jupyter notebook.

---

### Content Requirements

**Mathematical Rigor**
- Derive every equation step by step. Show all intermediate algebraic manipulations — \
no skipped steps.
- When a theorem, identity, or technique is invoked (e.g., Jensen's inequality, matrix \
inversion lemma, chain rule on expectations), state it formally, then walk through its \
application in context.
- Annotate each derivation step with a brief note explaining *why* that manipulation \
was performed.
- Assume undergraduate-level math competency as the baseline. Build up any advanced \
concepts from first principles before using them.

**Code Demonstrations**
- Where applicable, include annotated PyTorch code blocks that concretely implement or \
illustrate a mechanism from the paper.
- Every code block should have inline comments mapping math notation to code variables \
and explaining the computational logic.

**Intuition & Pedagogy**
- Write in the voice of a professor teaching a seminar — explain the *why* behind each \
design choice, not just the *what*.
- For each major result or architectural decision, provide geometric, statistical, or \
information-theoretic intuition.
- Identify and explicitly surface any "hidden steps" — places where the paper hand-waves, \
invokes something without proof, or compresses multiple logical steps into one.

**Resource Synthesis**
- If the paper references supplementary materials, appendices, prior work, or external \
resources, summarize each and explain how they connect to the main contributions.
- Use web search to gather additional context: related blog posts, follow-up work, errata, \
or community discussion that enriches understanding.

---

### Formatting & Structure

- **Format**: Jupyter notebook (.ipynb) with markdown cells for text/LaTeX and code cells \
for executable Python
- **Link to original paper**: Include prominently at the top, immediately after the title
- **Math**: All equations in LaTeX math environments. Use `align` for multi-step derivations \
with annotations
- **Code**: All code in executable code cells with inline comments
- **Diagrams**: Draw SVG/TikZ diagrams or matplotlib figures where visual explanation aids \
understanding. Reference or reproduce key figures from the original source when helpful
- **Structure**: Follow the paper's section organization but expand each section with \
derivations, intuitions, and code demos. Add a "Prerequisites" section at the top covering \
any background math or ML concepts needed
- **Length**: This should be thorough and long-form — think tutorial-length blog post or \
lecture notes, not a summary

---

### Output

- A Jupyter notebook
- You should use nicely formatted LaTeX and potentially figures from the paper to create \
the review/report, and it should be very comprehensive and detailed
- When appropriate, use code blocks that are executable and help illustrate concepts or \
spell out implementation details, algorithms, or math
- At the end of the notebook, put either theory (text only) or coding exercises for the \
reader to check understanding and have a file called [notebook_name].answers which houses \
the answers to the exercises. In the notebook, in the cells directly after the exercise put \
some code that lets the user parse the answer from the answer file for that problem. Aim \
for at least 1-3 questions per paper.
"""


@dataclass
class SessionInfo:
    """Metadata for a review session."""

    session_id: str
    claude_session_id: str | None = None
    paper_url: str | None = None
    paper_title: str | None = None
    model: str = "claude-sonnet-4-6"
    system_prompt: str = ""
    created_at: str = ""
    messages: list = field(default_factory=list)


@dataclass
class ClientEntry:
    """Holds a long-lived ClaudeSDKClient and associated state."""

    client: ClaudeSDKClient
    lock: asyncio.Lock
    connected: bool = False


class SessionStream:
    """Thread-safe buffer of streaming events from a background task.

    Allows multiple subscribers to read events. New subscribers can
    catch up by reading ``accumulated_text`` and subscribing from the
    current ``event_count``.
    """

    def __init__(self):
        self.events: list[dict] = []
        self.done: bool = False
        self.accumulated_text: str = ""
        self.active_tools: list[str] = []
        self._condition: asyncio.Condition = asyncio.Condition()

    async def put(self, event: dict) -> None:
        async with self._condition:
            if event.get("type") == "text" and event.get("text"):
                self.accumulated_text += event["text"]
            elif event.get("type") == "tool_use" and event.get("tool"):
                self.active_tools.append(event["tool"])
            elif event.get("type") == "tool_result":
                self.active_tools = []
            elif event.get("type") == "done":
                self.active_tools = []

            self.events.append(event)
            self._condition.notify_all()

    async def finish(self) -> None:
        """Mark stream as complete — no more events will arrive."""
        async with self._condition:
            # Guarantee subscribers always see a terminal event
            if not self.events or self.events[-1].get("type") not in ("done", "error"):
                self.events.append({"type": "done", "partial": True})
            self.done = True
            self._condition.notify_all()

    async def subscribe(self, from_index: int = 0) -> AsyncIterator[dict]:
        """Yield events starting at *from_index*, blocking for new ones."""
        idx = from_index
        while True:
            # Wait for new events under the lock
            async with self._condition:
                while idx >= len(self.events) and not self.done:
                    await self._condition.wait()

            # Yield outside the lock so I/O doesn't block writers
            while idx < len(self.events):
                event = self.events[idx]
                idx += 1
                yield event
                if event.get("type") == "done":
                    return

            if self.done and idx >= len(self.events):
                return


class ClaudeBridge:
    """Manages communication with Claude Code via the Agent SDK."""

    def __init__(self, data_dir: str, server_root: str | None = None):
        self.data_dir = data_dir
        self.server_root = server_root or os.path.expanduser("~")
        self.sessions_dir = os.path.join(data_dir, "sessions")
        os.makedirs(self.sessions_dir, exist_ok=True)

        # Reviews directory where notebooks and files are created
        # Lives inside data/ alongside sessions/ for clean organization.
        # Path is still under server_root so JupyterLab can find the notebooks.
        self.reviews_dir = os.path.join(data_dir, "reviews")
        os.makedirs(self.reviews_dir, exist_ok=True)

        # Pool of long-lived SDK clients (one per session)
        self._clients: dict[str, ClientEntry] = {}

        # Active background streams (session_id → SessionStream)
        self._streams: dict[str, SessionStream] = {}
        self._stream_tasks: dict[str, asyncio.Task] = {}

    def _get_session_path(self, session_id: str) -> str:
        return os.path.join(self.sessions_dir, f"{session_id}.json")

    def _get_system_prompt(self) -> str:
        """Build the system prompt with the reviews directory path."""
        return PAPER_REVIEW_SYSTEM_PROMPT.format(reviews_dir=self.reviews_dir)

    def create_session(
        self, paper_url: str | None = None, model: str = "claude-sonnet-4-6"
    ) -> SessionInfo:
        """Create a new review session."""
        from datetime import datetime, timezone

        session = SessionInfo(
            session_id=str(uuid.uuid4()),
            paper_url=paper_url,
            model=model,
            system_prompt=self._get_system_prompt(),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._save_session(session)
        return session

    def get_session(self, session_id: str) -> SessionInfo | None:
        path = self._get_session_path(session_id)
        if not os.path.exists(path):
            return None
        with open(path) as f:
            data = json.load(f)
        return SessionInfo(**data)

    def list_sessions(self) -> list[dict]:
        """List all sessions with metadata only."""
        sessions = []
        for fname in os.listdir(self.sessions_dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self.sessions_dir, fname)
            try:
                with open(path) as f:
                    data = json.load(f)
                sid = data["session_id"]
            except (json.JSONDecodeError, KeyError, FileNotFoundError, OSError) as e:
                _debug(f"  Skipping corrupt/missing session file {fname}: {e}")
                continue
            stream = self._streams.get(sid)
            sessions.append(
                {
                    "session_id": sid,
                    "paper_title": data.get("paper_title"),
                    "paper_url": data.get("paper_url"),
                    "model": data.get("model", "claude-sonnet-4-6"),
                    "created_at": data.get("created_at", ""),
                    "message_count": len(data.get("messages", [])),
                    "streaming": bool(stream and not stream.done),
                }
            )
        sessions.sort(key=lambda s: s["created_at"], reverse=True)
        return sessions

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session and close its SDK client."""
        # Cancel any active stream first
        task = self._stream_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()
        self._streams.pop(session_id, None)

        await self._close_client(session_id)
        path = self._get_session_path(session_id)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def _save_session(self, session: SessionInfo):
        path = self._get_session_path(session.session_id)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(session.__dict__, f, indent=2)
        os.replace(tmp_path, path)

    def _build_system_prompt(self, session: SessionInfo) -> str:
        prompt = session.system_prompt or self._get_system_prompt()
        if session.paper_title:
            prompt = f"PAPER UNDER REVIEW: {session.paper_title}\n\n{prompt}"
        return prompt

    @staticmethod
    def _find_claude_cli() -> str | None:
        """Find the claude CLI binary, checking common install locations."""
        import shutil

        # 1. Already on PATH?
        found = shutil.which("claude")
        if found:
            return found

        # 2. nvm installs (most common on macOS)
        nvm_dir = os.environ.get("NVM_DIR", os.path.expanduser("~/.nvm"))
        versions_dir = os.path.join(nvm_dir, "versions", "node")
        if os.path.isdir(versions_dir):
            # Pick the newest node version that has claude
            for ver in sorted(os.listdir(versions_dir), reverse=True):
                candidate = os.path.join(versions_dir, ver, "bin", "claude")
                if os.path.exists(candidate):
                    return candidate

        # 3. Homebrew / system global
        for p in ["/usr/local/bin/claude", "/opt/homebrew/bin/claude"]:
            if os.path.exists(p):
                return p

        return None

    def _build_options(
        self, session: SessionInfo, resume_id: str | None = None
    ) -> ClaudeAgentOptions:
        """Build ClaudeAgentOptions for a session."""
        system_prompt = self._build_system_prompt(session)

        # Resolve CLI path once
        cli_path = self._find_claude_cli()
        if cli_path:
            _debug(f"  Using claude CLI at: {cli_path}")

        # Ensure node is on PATH for the claude shim
        env = {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "128000"}
        if cli_path:
            cli_bin_dir = os.path.dirname(os.path.realpath(cli_path))
            node_bin_dir = os.path.dirname(cli_bin_dir) if cli_bin_dir.endswith("/bin") else cli_bin_dir
            node_bin = os.path.join(os.path.dirname(os.path.realpath(cli_path)), "..", "bin")
            node_bin = os.path.normpath(node_bin)
            if os.path.isdir(node_bin):
                current_path = os.environ.get("PATH", "")
                if node_bin not in current_path:
                    env["PATH"] = f"{node_bin}:{current_path}"

        kwargs = dict(
            model=session.model,
            system_prompt=system_prompt,
            cwd=self.reviews_dir,
            allowed_tools=DEFAULT_TOOLS,
            permission_mode="bypassPermissions",
            include_partial_messages=True,
            max_turns=50,
            max_buffer_size=100 * 1024 * 1024,  # 100 MB — large notebooks exceed 1 MB default
            env=env,
        )

        if cli_path:
            kwargs["cli_path"] = cli_path

        if resume_id:
            kwargs["resume"] = resume_id

        return ClaudeAgentOptions(**kwargs)

    @staticmethod
    def _is_dead_process_error(err: Exception) -> bool:
        """Return True if the error indicates the SDK subprocess died."""
        # CLIConnectionError is raised when writing to a terminated process.
        if isinstance(err, CLIConnectionError):
            return True
        # ProcessError / OSError indicate a dead subprocess.
        # Generic ClaudeSDKError with "terminated" could be user-cancel,
        # so only match specific subclasses.
        if not isinstance(err, (ProcessError, OSError)):
            return False
        msg = str(err).lower()
        return (
            "terminated" in msg
            or ("exit code" in msg and "exit code 0" not in msg)
            or "broken pipe" in msg
            or ("process" in msg and ("dead" in msg or "not running" in msg))
        )

    async def _get_or_create_client(
        self, session: SessionInfo, skip_resume: bool = False
    ) -> ClientEntry:
        """Get an existing client or create a new one for this session."""
        session_id = session.session_id

        if session_id in self._clients:
            entry = self._clients[session_id]
            if entry.connected:
                _debug(f"  Reusing existing client for session {session_id}")
                return entry

        resume_id = session.claude_session_id if not skip_resume else None

        _debug(f"  Creating new SDK client for session {session_id}")
        _debug(f"    model={session.model}, cwd={self.reviews_dir}")
        if resume_id:
            _debug(f"    resuming claude_session_id={resume_id}")
        elif session.claude_session_id and skip_resume:
            _debug(f"    skipping stale resume (was {session.claude_session_id})")

        options = self._build_options(session, resume_id=resume_id)
        client = ClaudeSDKClient(options)

        entry = ClientEntry(
            client=client,
            lock=asyncio.Lock(),
        )

        await client.connect()
        entry.connected = True
        _debug(f"  SDK client connected for session {session_id}")

        self._clients[session_id] = entry
        return entry

    async def _close_client(self, session_id: str) -> None:
        """Disconnect and remove a client from the pool."""
        entry = self._clients.pop(session_id, None)
        if entry and entry.connected:
            try:
                await entry.client.disconnect()
                _debug(f"  SDK client disconnected for session {session_id}")
            except Exception as e:
                _debug(f"  Error disconnecting client {session_id}: {e}")
            entry.connected = False

    async def cancel_session(self, session_id: str) -> bool:
        """Cancel an in-progress response for a session."""
        _debug(f"  Cancelling session {session_id}")
        acted = False

        # 1. Interrupt the SDK client first so send_message stops producing
        entry = self._clients.get(session_id)
        if entry and entry.connected:
            try:
                await entry.client.interrupt()
            except Exception:
                pass
            await self._close_client(session_id)
            acted = True

        # 2. Cancel the background stream task (its finally block handles
        #    stream.finish() and done-event injection)
        task = self._stream_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
            acted = True

        # 3. If there's a stream with no task (edge case), finish it directly
        stream = self._streams.get(session_id)
        if stream and not stream.done:
            await stream.finish()
            acted = True

        return acted

    async def shutdown(self) -> None:
        """Disconnect all active clients. Called on server shutdown."""
        # Cancel running stream tasks
        for task in self._stream_tasks.values():
            if not task.done():
                task.cancel()
        self._stream_tasks.clear()
        self._streams.clear()

        session_ids = list(self._clients.keys())
        for sid in session_ids:
            await self._close_client(sid)
        _debug(f"Shut down {len(session_ids)} SDK clients")

    # ------------------------------------------------------------------
    # Background-stream API: start_message / subscribe / get_stream_status
    # ------------------------------------------------------------------

    async def start_message(
        self,
        session_id: str,
        message: str,
        model: str | None = None,
    ) -> None:
        """Save the user message and start a background streaming task.

        Returns immediately.  Callers should use ``subscribe()`` to read
        events from the resulting stream.
        """
        # Reject if a stream is already running for this session
        existing = self._streams.get(session_id)
        if existing and not existing.done:
            raise RuntimeError("Session already has an active stream")

        session = self.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        # Persist the user question NOW so it can never be lost
        if model:
            session.model = model
        session.messages.append({"role": "user", "content": message})
        self._save_session(session)

        # Create the event buffer and kick off the background task
        stream = SessionStream()
        self._streams[session_id] = stream

        task = asyncio.create_task(
            self._run_stream_task(session_id, message, model, stream)
        )
        self._stream_tasks[session_id] = task

    @staticmethod
    def _build_history_context(messages: list[dict], current_message: str) -> str:
        """Build a context-enriched message from conversation history.

        When a Claude session can't be resumed (process died, session expired),
        we inject the local conversation history so Claude has prior context.
        """
        # All messages except the very last user message (which is current_message)
        history = messages[:-1] if messages else []
        if not history:
            return current_message

        parts = [
            "[Session recovered — prior conversation context follows]\n"
        ]
        for m in history:
            role = "User" if m["role"] == "user" else "Assistant"
            content = m["content"]
            # Truncate very long messages to avoid hitting token limits
            if len(content) > 4000:
                content = content[:4000] + "\n... [truncated]"
            parts.append(f"{role}: {content}")

        parts.append("\n---\n")
        parts.append(f"Continue the conversation. The user's new message is:\n{current_message}")

        return "\n\n".join(parts)

    async def _run_stream_task(
        self,
        session_id: str,
        message: str,
        model: str | None,
        stream: SessionStream,
    ) -> None:
        """Background coroutine that drives the SDK and feeds *stream*."""
        try:
            async for event in self.send_message(
                session_id, message, model=model, _user_msg_persisted=True,
            ):
                await stream.put(event)
        except _StaleResumeError:
            # Zero-turn stale resume — the Claude backend session is gone.
            # send_message already cleared claude_session_id and closed
            # the client.  Retry with a fresh client but inject conversation
            # history so Claude still has prior context.
            _debug(f"  _run_stream_task: retrying with history context for {session_id}")
            session = self.get_session(session_id)
            enriched = message
            if session and session.messages:
                enriched = self._build_history_context(session.messages, message)
                _debug(f"  Injected {len(session.messages) - 1} prior messages as context")
            try:
                async for event in self.send_message(
                    session_id, enriched, model=model, _user_msg_persisted=True,
                ):
                    await stream.put(event)
            except Exception as retry_err:
                _debug(f"  _run_stream_task: retry also failed: {retry_err}")
                await stream.put({"type": "error", "error": str(retry_err)})
        except asyncio.CancelledError:
            _debug(f"  _run_stream_task cancelled for {session_id}")
            await stream.put({"type": "done", "partial": True})
        except Exception as e:
            _debug(f"  _run_stream_task error: {e}")
            await stream.put({"type": "error", "error": str(e)})
        finally:
            await stream.finish()
            self._stream_tasks.pop(session_id, None)
            # Clean up the stream after a delay so late subscribers can catch up
            async def _cleanup():
                await asyncio.sleep(60)
                self._streams.pop(session_id, None)
            asyncio.ensure_future(_cleanup())

    async def subscribe(
        self, session_id: str, from_index: int = 0
    ) -> AsyncIterator[dict]:
        """Yield events from the active stream for *session_id*."""
        stream = self._streams.get(session_id)
        if not stream:
            return
        async for event in stream.subscribe(from_index):
            yield event

    def get_stream_status(self, session_id: str) -> dict:
        """Return the current state of a session's background stream."""
        stream = self._streams.get(session_id)
        if not stream:
            return {"active": False, "event_count": 0}
        return {
            "active": not stream.done,
            "event_count": len(stream.events),
            "accumulated_text": stream.accumulated_text,
            "active_tools": stream.active_tools,
        }

    # ------------------------------------------------------------------

    async def send_message(
        self,
        session_id: str,
        message: str,
        model: str | None = None,
        allowed_tools: list[str] | None = None,
        _user_msg_persisted: bool = False,
    ) -> AsyncIterator[dict]:
        """Send a message via the Agent SDK and stream back events.

        Uses a long-lived ClaudeSDKClient per session instead of spawning
        a subprocess per message. Conversation context is preserved across
        messages within the same client.

        Yields dicts with keys:
          - type: "text" | "tool_use" | "done" | "error"
          - text: (for text events) the text content
          - tool: (for tool events) tool name
          - ...other metadata
        """
        start_time = time.monotonic()
        _debug(f"send_message called: session_id={session_id}, model={model}")

        try:
            session = self.get_session(session_id)
            _debug(f"  get_session returned: {session is not None}")
        except Exception as e:
            _debug(f"  get_session FAILED: {type(e).__name__}: {e}")
            yield {"type": "error", "error": f"Failed to load session: {e}"}
            return

        if not session:
            _debug(f"  Session {session_id} not found!")
            yield {"type": "error", "error": f"Session {session_id} not found"}
            return

        if model:
            session.model = model
        _debug(f"  model={session.model}, claude_session_id={session.claude_session_id}")

        accumulated_text = ""
        claude_session_id = session.claude_session_id
        yielded_done = False
        # Track whether streaming captured text for the CURRENT turn.
        # Reset on each new turn (message_start) so the AssistantMessage
        # fallback works correctly for multi-turn tool interactions.
        turn_text_streamed = False

        try:
            # Get or create the long-lived client
            entry = await self._get_or_create_client(session)

            # If model changed, update on the client
            if model:
                try:
                    await entry.client.set_model(model)
                except Exception:
                    pass  # Not all SDK versions support this

            async with entry.lock:
                _debug(f"  Sending query to SDK client...")
                try:
                    await entry.client.query(message)
                except (ProcessError, ClaudeSDKError, OSError) as query_err:
                    if self._is_dead_process_error(query_err):
                        _debug(f"  Dead process on query ({query_err}), recreating client...")
                        await self._close_client(session_id)
                        session.claude_session_id = None
                        entry = await self._get_or_create_client(session, skip_resume=True)
                        if model:
                            try:
                                await entry.client.set_model(model)
                            except Exception:
                                pass
                        await entry.client.query(message)
                    else:
                        raise

                _debug(f"  Reading response stream...")
                try:
                    response_iter = entry.client.receive_response()
                    # Peek at first message to detect stale resume failures
                    first_msg = await response_iter.__anext__()
                except Exception as resume_err:
                    # Retry for stale resume OR dead process
                    should_retry = (
                        session.claude_session_id is not None
                        or self._is_dead_process_error(resume_err)
                    )
                    if should_retry:
                        _debug(f"  Response failed ({resume_err}), retrying fresh...")
                        await self._close_client(session_id)
                        session.claude_session_id = None
                        entry = await self._get_or_create_client(session, skip_resume=True)
                        if model:
                            try:
                                await entry.client.set_model(model)
                            except Exception:
                                pass
                        await entry.client.query(message)
                        response_iter = entry.client.receive_response()
                        first_msg = await response_iter.__anext__()
                    else:
                        raise

                # Process the first message we already consumed
                async def _chain_first(first, rest):
                    yield first
                    async for item in rest:
                        yield item

                async for msg in _chain_first(first_msg, response_iter):

                    if isinstance(msg, SystemMessage):
                        # Capture session_id from init
                        sid = msg.data.get("session_id")
                        if sid:
                            claude_session_id = sid
                            _debug(f"  Got claude_session_id: {claude_session_id}")

                    elif isinstance(msg, StreamEvent):
                        # Token-by-token streaming
                        event = msg.event
                        event_type = event.get("type")

                        if not claude_session_id and msg.session_id:
                            claude_session_id = msg.session_id

                        if event_type == "content_block_delta":
                            delta = event.get("delta", {})
                            delta_type = delta.get("type")

                            if delta_type == "text_delta":
                                text = delta.get("text", "")
                                if text:
                                    accumulated_text += text
                                    turn_text_streamed = True
                                    yield {"type": "text", "text": text}

                        elif event_type == "content_block_start":
                            content_block = event.get("content_block", {})
                            if content_block.get("type") == "tool_use":
                                yield {
                                    "type": "tool_use",
                                    "tool": content_block.get("name", ""),
                                    "input": content_block.get("input", {}),
                                }

                        elif event_type == "message_start":
                            # A new assistant turn is starting (e.g. after tool results).
                            # Reset per-turn tracking so fallback works for new turn.
                            turn_text_streamed = False
                            yield {"type": "tool_result"}

                    elif isinstance(msg, AssistantMessage):
                        # Complete message — use as fallback if streaming
                        # didn't capture this turn's text
                        for block in msg.content:
                            if isinstance(block, ToolUseBlock):
                                yield {
                                    "type": "tool_use",
                                    "tool": block.name,
                                    "input": block.input,
                                }
                            elif isinstance(block, ToolResultBlock):
                                yield {"type": "tool_result"}
                            elif isinstance(block, TextBlock):
                                if not turn_text_streamed:
                                    # Streaming didn't capture this turn's text
                                    accumulated_text += block.text
                                    yield {"type": "text", "text": block.text}
                        # Reset for next turn
                        turn_text_streamed = False

                    elif isinstance(msg, ResultMessage):
                        # Final result
                        if msg.session_id:
                            claude_session_id = msg.session_id

                        duration = int((time.monotonic() - start_time) * 1000)

                        # Detect zero-turn stale resume: the session resumed
                        # but produced nothing (process was dead / session stale).
                        # Raise so the caller can retry with a fresh session.
                        if (
                            not accumulated_text
                            and msg.num_turns == 0
                            and session.claude_session_id is not None
                        ):
                            _debug(
                                f"  Zero-turn stale resume detected "
                                f"(turns={msg.num_turns}, cost=${msg.total_cost_usd or 0:.4f})"
                            )
                            raise _StaleResumeError(
                                f"Resume of {session.claude_session_id} produced 0 turns"
                            )

                        # ResultMessage.result may have final summary text
                        if msg.result and msg.result not in accumulated_text:
                            accumulated_text += "\n\n" + msg.result
                            yield {"type": "text", "text": "\n\n" + msg.result}

                        yielded_done = True
                        yield {
                            "type": "done",
                            "session_id": claude_session_id,
                            "cost": msg.total_cost_usd,
                            "duration": duration,
                        }
                        _debug(
                            f"  Done: cost=${msg.total_cost_usd or 0:.4f}, "
                            f"duration={duration}ms, turns={msg.num_turns}"
                        )

            # Ensure we always send a done event
            if not yielded_done:
                duration = int((time.monotonic() - start_time) * 1000)
                yield {
                    "type": "done",
                    "session_id": claude_session_id,
                    "partial": True,
                    "duration": duration,
                }

            # Persist session state (reload to pick up user msg from start_message)
            session = self.get_session(session_id) or session
            if claude_session_id:
                session.claude_session_id = claude_session_id
            if not _user_msg_persisted:
                session.messages.append({"role": "user", "content": message})
            if accumulated_text:
                session.messages.append(
                    {"role": "assistant", "content": accumulated_text}
                )
            self._save_session(session)

        except _StaleResumeError:
            # Zero-turn resume — clean up and let the caller retry.
            _debug("  Stale resume: clearing session ID and closing client")
            await self._close_client(session_id)
            session = self.get_session(session_id) or session
            session.claude_session_id = None
            self._save_session(session)
            raise  # Propagate to _run_stream_task for retry

        except CLINotFoundError:
            _debug("  ERROR: Claude Code CLI not found")
            yield {
                "type": "error",
                "error": "Claude Code CLI not found. Install: pip install claude-agent-sdk",
            }
            session = self.get_session(session_id) or session
            if not _user_msg_persisted:
                session.messages.append({"role": "user", "content": message})
            self._save_session(session)
            await self._close_client(session_id)

        except (ClaudeSDKError, ProcessError) as e:
            _debug(f"  SDK error: {type(e).__name__}: {e}")
            logger.exception("SDK error in Claude bridge")
            is_dead = self._is_dead_process_error(e)
            # Persist partial work before yielding error.
            # Clear claude_session_id for dead processes so next attempt
            # doesn't try to resume a dead session.
            session = self.get_session(session_id) or session
            if is_dead:
                session.claude_session_id = None
            elif claude_session_id:
                session.claude_session_id = claude_session_id
            if not _user_msg_persisted:
                session.messages.append({"role": "user", "content": message})
            if accumulated_text:
                session.messages.append(
                    {"role": "assistant", "content": accumulated_text}
                )
            self._save_session(session)
            # Yield a single done event so the frontend keeps partial text,
            # or an error if there's nothing to keep.
            if accumulated_text and not yielded_done:
                yield {"type": "done", "session_id": claude_session_id, "partial": True}
                yielded_done = True
            else:
                error_msg = (
                    "Claude process crashed — please resend your message to retry."
                    if is_dead
                    else str(e)
                )
                yield {"type": "error", "error": error_msg}
            await self._close_client(session_id)

        except Exception as e:
            _debug(f"  ERROR in Claude bridge: {type(e).__name__}: {e}")
            logger.exception("Error in Claude bridge")
            is_dead = self._is_dead_process_error(e)
            session = self.get_session(session_id) or session
            if is_dead:
                session.claude_session_id = None
            elif claude_session_id:
                session.claude_session_id = claude_session_id
            if not _user_msg_persisted:
                session.messages.append({"role": "user", "content": message})
            if accumulated_text:
                session.messages.append(
                    {"role": "assistant", "content": accumulated_text}
                )
            self._save_session(session)
            if accumulated_text and not yielded_done:
                yield {"type": "done", "session_id": claude_session_id, "partial": True}
                yielded_done = True
            else:
                error_msg = (
                    "Claude process crashed — please resend your message to retry."
                    if is_dead
                    else str(e)
                )
                yield {"type": "error", "error": error_msg}
            await self._close_client(session_id)
