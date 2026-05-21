"""Bridge to OpenAI's Codex CLI via the openai-codex Python SDK.

Mirrors the public interface of ClaudeBridge so the same handlers and
event protocol work for both providers. Sessions are persisted in the
same JSON format on disk — only the resume-id field differs
(claude_session_id vs codex_thread_id).
"""

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass
from typing import AsyncIterator

try:
    from openai_codex import (
        AppServerConfig,
        AsyncCodex,
        AsyncThread,
        AsyncTurnHandle,
        LocalImageInput,
        TextInput,
    )
    from openai_codex.errors import AppServerError, TransportClosedError
    from openai_codex.generated.v2_all import SandboxMode
    CODEX_AVAILABLE = True
except ImportError:
    CODEX_AVAILABLE = False
    AppServerConfig = None  # type: ignore
    AsyncCodex = None  # type: ignore
    SandboxMode = None  # type: ignore

# Reuse storage primitives + session model from claude_bridge so both
# providers read/write the same on-disk format.
from .claude_bridge import (
    PAPER_REVIEW_SYSTEM_PROMPT,
    SessionInfo,
    SessionStream,
    save_attachments,
)

logger = logging.getLogger(__name__)


def _debug(msg: str):
    print(f"[codex-bridge] {msg}", file=sys.stderr, flush=True)


# Item types that we surface as "tool_use" to the frontend. Maps the
# Codex item.type strings to the friendly tool name we show in a chip.
_TOOL_ITEM_TYPES = {
    "commandExecution": "Bash",
    "webSearch": "WebSearch",
    "fileChange": "Edit",
    "mcpToolCall": "MCP",
    "dynamicToolCall": "Tool",
    "collabAgentToolCall": "Agent",
    "imageView": "ImageView",
    "imageGeneration": "ImageGen",
    "plan": "Plan",
}


@dataclass
class _ThreadEntry:
    """A connected Codex client + active thread for one of our sessions."""

    codex: "AsyncCodex"
    thread: "AsyncThread"
    lock: asyncio.Lock


class CodexBridge:
    """Manages communication with Codex CLI via the openai-codex SDK.

    Public methods (and event dict shape) match ClaudeBridge so the
    handlers can dispatch by model without caring which provider runs.
    """

    # Cache the model list so we don't spin up a JSON-RPC connection on
    # every ModelsHandler hit. SDK fetches are ~1s; cache for 30 min.
    _MODEL_CACHE_TTL_S = 1800

    def __init__(self, data_dir: str, server_root: str | None = None):
        if not CODEX_AVAILABLE:
            raise RuntimeError(
                "openai-codex SDK not installed. "
                "Install with: pip install openai-codex"
            )
        self.data_dir = data_dir
        self.server_root = server_root or os.path.expanduser("~")
        self.sessions_dir = os.path.join(data_dir, "sessions")
        os.makedirs(self.sessions_dir, exist_ok=True)
        self.reviews_dir = os.path.join(data_dir, "reviews")
        os.makedirs(self.reviews_dir, exist_ok=True)

        # One long-lived Codex client + thread per session
        self._entries: dict[str, _ThreadEntry] = {}
        # Active in-flight turn handles, for cancellation
        self._active_turns: dict[str, AsyncTurnHandle] = {}
        # Active background streams (session_id → SessionStream)
        self._streams: dict[str, SessionStream] = {}
        self._stream_tasks: dict[str, asyncio.Task] = {}

        # Cached model list
        self._models_cache: list[dict] | None = None
        self._models_cache_ts: float = 0.0
        self._models_cache_lock = asyncio.Lock()

    async def list_models(self) -> list[dict]:
        """Return the live list of models the Codex SDK supports.

        Cached for _MODEL_CACHE_TTL_S to avoid spinning up a subprocess
        on every request.
        """
        async with self._models_cache_lock:
            now = time.monotonic()
            if (
                self._models_cache is not None
                and now - self._models_cache_ts < self._MODEL_CACHE_TTL_S
            ):
                return self._models_cache

            try:
                async with AsyncCodex(config=AppServerConfig()) as codex:
                    result = await codex.models(include_hidden=False)
                models = []
                for m in result.data:
                    if getattr(m, "hidden", False):
                        continue
                    models.append({
                        "id": m.id,
                        "name": getattr(m, "display_name", None) or m.id,
                        "tier": "gpt5",
                        "description": getattr(m, "description", None) or "",
                        "is_default": bool(getattr(m, "is_default", False)),
                    })
                # Sort: default first, then by id descending (newer versions first)
                models.sort(key=lambda x: (not x["is_default"], x["id"]), reverse=False)
                self._models_cache = models
                self._models_cache_ts = now
                return models
            except Exception as e:
                _debug(f"  Failed to fetch live model list: {e}")
                # Conservative fallback so the UI still shows something useful
                if self._models_cache is not None:
                    return self._models_cache
                return [
                    {"id": "gpt-5.5", "name": "GPT-5.5", "tier": "gpt5", "is_default": True},
                    {"id": "gpt-5.4", "name": "GPT-5.4", "tier": "gpt5", "is_default": False},
                ]

    # ------------------------------------------------------------------
    # Session JSON I/O — must match ClaudeBridge's format exactly so
    # sessions show up in the UI list regardless of provider.
    # ------------------------------------------------------------------
    def _get_session_path(self, session_id: str) -> str:
        return os.path.join(self.sessions_dir, f"{session_id}.json")

    def get_session(self, session_id: str) -> SessionInfo | None:
        path = self._get_session_path(session_id)
        if not os.path.exists(path):
            return None
        with open(path) as f:
            data = json.load(f)
        valid_fields = set(SessionInfo.__dataclass_fields__.keys())
        data = {k: v for k, v in data.items() if k in valid_fields}
        return SessionInfo(**data)

    def _save_session(self, session: SessionInfo):
        path = self._get_session_path(session.session_id)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(session.__dict__, f, indent=2)
        os.replace(tmp_path, path)

    # ------------------------------------------------------------------
    # Client lifecycle
    # ------------------------------------------------------------------
    def _build_system_prompt(self, session: SessionInfo) -> str:
        prompt = session.system_prompt or PAPER_REVIEW_SYSTEM_PROMPT.format(
            reviews_dir=self.reviews_dir
        )
        if session.paper_title:
            prompt = f"PAPER UNDER REVIEW: {session.paper_title}\n\n{prompt}"
        return prompt

    async def _get_or_create_entry(self, session: SessionInfo) -> _ThreadEntry:
        """Return a connected Codex client + thread for *session*."""
        sid = session.session_id
        existing = self._entries.get(sid)
        if existing is not None:
            return existing

        _debug(f"  Creating Codex client for session {sid} (model={session.model})")
        # Run the codex subprocess rooted at reviews_dir so its file ops can't
        # escape the workspace (defense in depth alongside sandbox mode).
        codex = AsyncCodex(config=AppServerConfig(cwd=self.reviews_dir))
        await codex.__aenter__()

        # Build thread-start kwargs. The system prompt goes into
        # developer_instructions (appended to Codex's own defaults), and the
        # sandbox is restricted to workspace_write so writes are confined to
        # reviews_dir.
        start_kwargs: dict = {
            "model": session.model,
            "cwd": self.reviews_dir,
            "developer_instructions": self._build_system_prompt(session),
        }
        if SandboxMode is not None:
            start_kwargs["sandbox"] = SandboxMode.workspace_write

        try:
            if session.codex_thread_id:
                _debug(f"  Resuming codex_thread_id={session.codex_thread_id}")
                try:
                    thread = await codex.thread_resume(
                        session.codex_thread_id, model=session.model
                    )
                except Exception as e:
                    _debug(f"  Resume failed ({e}); starting fresh thread")
                    thread = await codex.thread_start(**start_kwargs)
            else:
                thread = await codex.thread_start(**start_kwargs)

            entry = _ThreadEntry(codex=codex, thread=thread, lock=asyncio.Lock())
            self._entries[sid] = entry
            return entry
        except Exception:
            # Clean up on failure
            try:
                await codex.__aexit__(None, None, None)
            except Exception:
                pass
            raise

    async def _close_entry(self, session_id: str) -> None:
        entry = self._entries.pop(session_id, None)
        if entry is None:
            return
        try:
            await entry.codex.__aexit__(None, None, None)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Background-stream API: start_message / subscribe / cancel_session
    # ------------------------------------------------------------------
    async def start_message(
        self,
        session_id: str,
        message: str,
        model: str | None = None,
        images: list[dict] | None = None,
    ) -> None:
        """Save the user message and kick off the background streaming task.

        *images* is an optional list of ``{"data": <base64>, "media_type": ...}``
        pasted images, sent to Codex as ``LocalImageInput`` items.
        """
        existing = self._streams.get(session_id)
        if existing and not existing.done:
            raise RuntimeError("Session already has an active stream")

        session = self.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        attachments = save_attachments(self.reviews_dir, session_id, images)

        if model:
            session.model = model
        user_msg: dict = {"role": "user", "content": message}
        if attachments:
            user_msg["attachments"] = [
                {"rel_path": a["rel_path"], "media_type": a["media_type"]}
                for a in attachments
            ]
        session.messages.append(user_msg)
        self._save_session(session)

        stream = SessionStream()
        self._streams[session_id] = stream

        task = asyncio.create_task(
            self._run_stream_task(session_id, message, stream, attachments)
        )
        self._stream_tasks[session_id] = task

    async def subscribe(
        self, session_id: str, from_index: int = 0
    ) -> AsyncIterator[dict]:
        stream = self._streams.get(session_id)
        if not stream:
            return
        async for event in stream.subscribe(from_index):
            yield event

    def get_stream_status(self, session_id: str) -> dict:
        stream = self._streams.get(session_id)
        if not stream:
            return {"active": False, "event_count": 0}
        return {
            "active": not stream.done,
            "event_count": len(stream.events),
            "accumulated_text": stream.accumulated_text,
            "active_tools": stream.active_tools,
        }

    async def cancel_session(self, session_id: str) -> bool:
        _debug(f"  Cancelling session {session_id}")
        acted = False

        # Interrupt the live turn if there is one
        turn = self._active_turns.pop(session_id, None)
        if turn is not None:
            try:
                await turn.interrupt()
                acted = True
            except Exception:
                pass

        await self._close_entry(session_id)

        task = self._stream_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
            acted = True

        stream = self._streams.get(session_id)
        if stream and not stream.done:
            await stream.finish()
            acted = True

        return acted

    async def shutdown(self) -> None:
        for task in self._stream_tasks.values():
            if not task.done():
                task.cancel()
        self._stream_tasks.clear()
        self._streams.clear()

        ids = list(self._entries.keys())
        for sid in ids:
            await self._close_entry(sid)
        _debug(f"Shut down {len(ids)} Codex clients")

    # ------------------------------------------------------------------
    # Event mapping
    # ------------------------------------------------------------------
    @staticmethod
    def _item_type(payload) -> str | None:
        """Pull the discriminator type off an item-bearing notification."""
        item = getattr(payload, "item", None)
        if item is None:
            return None
        # ThreadItem is a RootModel — unwrap to get the concrete variant
        root = getattr(item, "root", item)
        return getattr(root, "type", None)

    async def _run_stream_task(
        self,
        session_id: str,
        message: str,
        stream: SessionStream,
        attachments: list[dict] | None = None,
    ) -> None:
        """Drive a single Codex turn and feed events into *stream*."""
        start_time = time.monotonic()
        accumulated_text = ""
        active_tool_chip: str | None = None
        try:
            session = self.get_session(session_id)
            if not session:
                await stream.put({"type": "error", "error": "Session not found"})
                return

            entry = await self._get_or_create_entry(session)
            # Build the turn input: text plus any pasted images as local files.
            turn_input: list = [TextInput(message)]
            for a in (attachments or []):
                turn_input.append(LocalImageInput(path=a["path"]))
            async with entry.lock:
                turn = await entry.thread.turn(turn_input)
                self._active_turns[session_id] = turn

                try:
                    async for event in turn.stream():
                        method = event.method
                        payload = getattr(event, "payload", None)

                        if method == "item/agentMessage/delta":
                            delta = getattr(payload, "delta", "") or ""
                            if delta:
                                accumulated_text += delta
                                await stream.put({"type": "text", "text": delta})

                        elif method == "item/started":
                            item_type = self._item_type(payload)
                            tool_name = _TOOL_ITEM_TYPES.get(item_type or "")
                            if tool_name:
                                active_tool_chip = tool_name
                                await stream.put({
                                    "type": "tool_use",
                                    "tool": tool_name,
                                    "input": {},
                                })

                        elif method == "item/completed":
                            item_type = self._item_type(payload)
                            if item_type in _TOOL_ITEM_TYPES:
                                await stream.put({"type": "tool_result"})
                                active_tool_chip = None

                        elif method == "turn/failed":
                            err_msg = "Codex turn failed"
                            err_obj = getattr(payload, "error", None)
                            if err_obj is not None:
                                err_msg = getattr(err_obj, "message", err_msg) or err_msg
                            await stream.put({"type": "error", "error": err_msg})

                        elif method == "turn/completed":
                            break
                finally:
                    self._active_turns.pop(session_id, None)

            # Persist final result
            duration = int((time.monotonic() - start_time) * 1000)
            session = self.get_session(session_id) or session
            session.codex_thread_id = entry.thread.id
            if accumulated_text:
                session.messages.append(
                    {"role": "assistant", "content": accumulated_text}
                )
            self._save_session(session)

            await stream.put({
                "type": "done",
                "session_id": entry.thread.id,
                "duration": duration,
            })

        except asyncio.CancelledError:
            _debug(f"  Codex stream cancelled for {session_id}")
            if accumulated_text:
                try:
                    sess = self.get_session(session_id)
                    if sess:
                        sess.messages.append(
                            {"role": "assistant", "content": accumulated_text}
                        )
                        # Don't preserve thread id — turn was interrupted
                        sess.codex_thread_id = None
                        self._save_session(sess)
                except Exception:
                    pass
            await stream.put({"type": "done", "partial": True})

        except (AppServerError, TransportClosedError) as e:
            _debug(f"  Codex SDK error: {type(e).__name__}: {e}")
            try:
                sess = self.get_session(session_id)
                if sess:
                    if accumulated_text:
                        sess.messages.append(
                            {"role": "assistant", "content": accumulated_text}
                        )
                    # Unknown thread state on transport/server errors
                    sess.codex_thread_id = None
                    self._save_session(sess)
            except Exception:
                pass
            await self._close_entry(session_id)
            if accumulated_text:
                await stream.put({"type": "done", "partial": True})
            else:
                await stream.put({"type": "error", "error": str(e)})

        except Exception as e:
            _debug(f"  Codex bridge error: {type(e).__name__}: {e}")
            logger.exception("Codex bridge error")
            try:
                sess = self.get_session(session_id)
                if sess and accumulated_text:
                    sess.messages.append(
                        {"role": "assistant", "content": accumulated_text}
                    )
                    self._save_session(sess)
            except Exception:
                pass
            if accumulated_text:
                await stream.put({"type": "done", "partial": True})
            else:
                await stream.put({"type": "error", "error": str(e)})

        finally:
            await stream.finish()
            self._stream_tasks.pop(session_id, None)

            async def _cleanup():
                await asyncio.sleep(60)
                self._streams.pop(session_id, None)

            asyncio.ensure_future(_cleanup())
