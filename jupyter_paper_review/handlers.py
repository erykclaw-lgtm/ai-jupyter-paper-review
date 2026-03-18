"""Tornado request handlers for the Paper Review API."""

import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from jupyter_server.base.handlers import APIHandler
from tornado import web

from .claude_bridge import ClaudeBridge
from .session_manager import SessionManager

logger = logging.getLogger(__name__)


def _debug(msg: str):
    """Write debug messages to stderr so they always show up in server output."""
    print(f"[paper-review] {msg}", file=sys.stderr, flush=True)

# Singleton bridge instance
_bridge: ClaudeBridge | None = None
_session_mgr: SessionManager | None = None
_server_root: str | None = None


def _get_bridge(data_dir: str | None = None, server_root: str | None = None) -> ClaudeBridge:
    global _bridge, _server_root
    if _bridge is None:
        if data_dir is None:
            data_dir = os.path.join(os.getcwd(), "data")
        if server_root is None:
            server_root = os.getcwd()
        _server_root = server_root
        _bridge = ClaudeBridge(data_dir, server_root=server_root)
    return _bridge


def _get_session_mgr(data_dir: str | None = None) -> SessionManager:
    global _session_mgr
    if _session_mgr is None:
        _session_mgr = SessionManager(_get_bridge(data_dir))
    return _session_mgr


def _sse_headers(handler):
    """Set standard SSE headers on *handler*."""
    handler.set_header("Content-Type", "text/event-stream")
    handler.set_header("Cache-Control", "no-cache")
    handler.set_header("Connection", "keep-alive")
    handler.set_header("X-Accel-Buffering", "no")


async def _write_stream(handler, bridge, session_id, from_index=0):
    """Subscribe to *session_id*'s stream and write SSE events."""
    event_count = 0
    try:
        async for event in bridge.subscribe(session_id, from_index):
            event_count += 1
            handler.write(f"data: {json.dumps(event)}\n\n")
            await handler.flush()
    except Exception:
        # Client disconnected — that's fine, background task keeps running
        pass
    _debug(f"  SSE writer done, sent {event_count} events (from={from_index})")


class ChatHandler(APIHandler):
    """POST /api/paper-review/chat — Start a background stream and subscribe."""

    @web.authenticated
    async def post(self):
        _debug("ChatHandler.post() called")
        body = self.get_json_body()
        session_id = body.get("session_id")
        message = body.get("message", "")
        model = body.get("model")

        _debug(f"  session_id={session_id}, model={model}, message_len={len(message)}")

        if not session_id:
            self.set_status(400)
            self.finish(json.dumps({"error": "session_id is required"}))
            return

        if not message:
            self.set_status(400)
            self.finish(json.dumps({"error": "message is required"}))
            return

        bridge = _get_bridge()

        # Start background task (saves user message immediately)
        try:
            await bridge.start_message(session_id, message, model=model)
        except RuntimeError as e:
            self.set_status(409)
            self.finish(json.dumps({"error": str(e)}))
            return
        except ValueError as e:
            self.set_status(404)
            self.finish(json.dumps({"error": str(e)}))
            return

        _sse_headers(self)
        self.write(": connected\n\n")
        await self.flush()
        _debug("  SSE headers flushed, subscribing to background stream")

        await _write_stream(self, bridge, session_id)
        self.finish()


class SubscribeHandler(APIHandler):
    """GET /api/paper-review/subscribe/<session_id> — Reconnect to an active stream."""

    @web.authenticated
    async def get(self, session_id):
        bridge = _get_bridge()
        from_index = int(self.get_argument("from", "0"))

        status = bridge.get_stream_status(session_id)
        if not status["active"] and status["event_count"] == 0:
            self.set_status(404)
            self.finish(json.dumps({"error": "No active stream for this session"}))
            return

        _sse_headers(self)
        self.write(": connected\n\n")
        await self.flush()

        await _write_stream(self, bridge, session_id, from_index)
        self.finish()


class StreamStatusHandler(APIHandler):
    """GET /api/paper-review/stream-status/<session_id> — Check stream state."""

    @web.authenticated
    async def get(self, session_id):
        bridge = _get_bridge()
        status = bridge.get_stream_status(session_id)
        self.finish(json.dumps(status))


class SessionsHandler(APIHandler):
    """GET/POST /api/paper-review/sessions — List or create sessions."""

    @web.authenticated
    async def get(self):
        mgr = _get_session_mgr()
        sessions = mgr.list_all()
        self.finish(json.dumps({"sessions": sessions}))

    @web.authenticated
    async def post(self):
        body = self.get_json_body()
        paper_url = body.get("paper_url")
        model = body.get("model", "claude-sonnet-4-6")

        mgr = _get_session_mgr()
        session = mgr.create(paper_url=paper_url, model=model)
        self.finish(json.dumps({"session_id": session.session_id}))


class SessionHandler(APIHandler):
    """GET/DELETE /api/paper-review/sessions/:id — Get or delete a session."""

    @web.authenticated
    async def get(self, session_id: str):
        mgr = _get_session_mgr()
        session = mgr.get(session_id)
        if not session:
            self.set_status(404)
            self.finish(json.dumps({"error": "Session not found"}))
            return
        self.finish(json.dumps(session.__dict__))

    @web.authenticated
    async def patch(self, session_id: str):
        """Update session metadata (e.g. title)."""
        body = self.get_json_body()
        bridge = _get_bridge()
        session = bridge.get_session(session_id)
        if not session:
            self.set_status(404)
            self.finish(json.dumps({"error": "Session not found"}))
            return

        if "paper_title" in body:
            session.paper_title = body["paper_title"]
        if "model" in body:
            session.model = body["model"]

        bridge._save_session(session)
        self.finish(json.dumps({"updated": True}))

    @web.authenticated
    async def delete(self, session_id: str):
        mgr = _get_session_mgr()
        deleted = await mgr.delete(session_id)
        if not deleted:
            self.set_status(404)
            self.finish(json.dumps({"error": "Session not found"}))
            return
        self.finish(json.dumps({"deleted": True}))


class ModelsHandler(APIHandler):
    """GET /api/paper-review/models — List available Claude models."""

    @web.authenticated
    async def get(self):
        models = [
            {
                "id": "claude-opus-4-6",
                "name": "Claude Opus 4.6",
                "tier": "opus",
            },
            {
                "id": "claude-sonnet-4-6",
                "name": "Claude Sonnet 4.6",
                "tier": "sonnet",
            },
            {
                "id": "claude-haiku-4-5-20251001",
                "name": "Claude Haiku 4.5",
                "tier": "haiku",
            },
        ]
        self.finish(json.dumps({"models": models}))


class NotebooksHandler(APIHandler):
    """GET /api/paper-review/notebooks — List .ipynb files in the reviews directory."""

    @web.authenticated
    async def get(self):
        bridge = _get_bridge()
        reviews_dir = bridge.reviews_dir
        notebooks = []
        if os.path.isdir(reviews_dir):
            for entry in sorted(Path(reviews_dir).rglob("*.ipynb")):
                # Skip checkpoint files
                if ".ipynb_checkpoints" in str(entry):
                    continue
                stat = entry.stat()
                # Get the path relative to the Jupyter server root
                # so it can be used with JupyterLab's file opener
                try:
                    rel_path = str(entry.relative_to(bridge.server_root))
                except ValueError:
                    rel_path = str(entry)

                notebooks.append({
                    "name": entry.name,
                    "path": rel_path,
                    "last_modified": stat.st_mtime,
                    "size": stat.st_size,
                })

            # Sort by modification time, newest first
            notebooks.sort(key=lambda n: n["last_modified"], reverse=True)

            # Convert mtime to ISO format
            from datetime import datetime, timezone
            for nb in notebooks:
                nb["last_modified"] = datetime.fromtimestamp(
                    nb["last_modified"], tz=timezone.utc
                ).isoformat()

        self.finish(json.dumps({"notebooks": notebooks}))


class CancelHandler(APIHandler):
    """POST /api/paper-review/cancel — Cancel an in-progress Claude response."""

    @web.authenticated
    async def post(self):
        body = self.get_json_body()
        session_id = body.get("session_id")
        if not session_id:
            self.set_status(400)
            self.finish(json.dumps({"error": "session_id is required"}))
            return

        bridge = _get_bridge()
        cancelled = await bridge.cancel_session(session_id)
        self.finish(json.dumps({"cancelled": cancelled}))


def _notebook_to_latex(abs_path: str) -> tuple[str, dict]:
    """Convert a notebook to LaTeX using nbconvert. Returns (tex_body, resources)."""
    import nbformat
    from nbconvert import LatexExporter

    nb = nbformat.read(abs_path, as_version=4)

    # Strip leading "---" (horizontal rule) from markdown cells.
    # Pandoc misinterprets a cell starting with "---" as YAML front matter,
    # which causes a JSONDecodeError in the pandoc filter pipeline.
    for cell in nb.cells:
        if cell.cell_type == "markdown" and cell.source.startswith("---"):
            cell.source = cell.source.lstrip("-").lstrip()

    exporter = LatexExporter()
    return exporter.from_notebook_node(nb)


import re

# LaTeX snippet injected right after \begin{document} to handle Unicode chars
# that Latin Modern lacks (e.g. ✓ ✗). Tectonic uses XeTeX so fontspec is loaded.
_UNICODE_FONT_PATCH = r"""
% -- paper-review patch: Unicode fallback for symbols missing in Latin Modern --
\usepackage{newunicodechar}
\newfontfamily\fallbackfont{DejaVu Sans}[Scale=MatchLowercase]
\newunicodechar{✓}{{\fallbackfont ✓}}
\newunicodechar{✗}{{\fallbackfont ✗}}
\newunicodechar{✔}{{\fallbackfont ✔}}
\newunicodechar{✘}{{\fallbackfont ✘}}
\newunicodechar{→}{{\fallbackfont →}}
\newunicodechar{←}{{\fallbackfont ←}}
\newunicodechar{⟹}{{\fallbackfont ⟹}}
% -- end patch --
"""


def _patch_tex(tex_body: str) -> str:
    """Fix common issues in nbconvert-generated LaTeX before compilation."""
    # 1. Inject Unicode fallback font definitions after \begin{document}
    #    We insert before \begin{document} so the commands are in the preamble.
    tex_body = tex_body.replace(
        r"\begin{document}",
        _UNICODE_FONT_PATCH + r"\begin{document}",
    )

    # 2. Remove the default "Notebook" title + date injected by nbconvert.
    tex_body = tex_body.replace(r"\maketitle", "")

    # 3. Fix amsmath nesting: \[...\begin{align}...\end{align}...\]
    #    Pandoc sometimes wraps align inside display-math delimiters.
    #    Remove the outer \[...\] when it just wraps an align environment.
    tex_body = re.sub(
        r"\\\[\s*\\begin\{(align\*?|aligned\*?|gather\*?|equation\*?)\}",
        r"\\begin{\1}",
        tex_body,
    )
    tex_body = re.sub(
        r"\\end\{(align\*?|aligned\*?|gather\*?|equation\*?)\}\s*\\\]",
        r"\\end{\1}",
        tex_body,
    )

    return tex_body


def _compile_tex_to_pdf(tex_body: str, resources: dict) -> bytes:
    """Compile a LaTeX string to PDF using tectonic. Returns PDF bytes."""
    tex_body = _patch_tex(tex_body)

    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = os.path.join(tmpdir, "notebook.tex")
        pdf_path = os.path.join(tmpdir, "notebook.pdf")

        with open(tex_path, "w") as f:
            f.write(tex_body)

        # Write extracted images/outputs so tectonic can find them
        for fname, data in resources.get("outputs", {}).items():
            with open(os.path.join(tmpdir, fname), "wb") as f:
                f.write(data)

        result = subprocess.run(
            ["tectonic", "-X", "compile", "-Z", "continue-on-errors", tex_path],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if not os.path.exists(pdf_path):
            raise RuntimeError(
                f"tectonic failed (exit {result.returncode}):\n{result.stderr}"
            )

        with open(pdf_path, "rb") as f:
            return f.read()


class ExportPdfHandler(APIHandler):
    """POST /api/paper-review/export-pdf — notebook → LaTeX → tectonic → PDF."""

    @web.authenticated
    async def post(self):
        body = self.get_json_body()
        notebook_path = body.get("path")  # relative to server root

        if not notebook_path:
            self.set_status(400)
            self.finish(json.dumps({"error": "path is required"}))
            return

        bridge = _get_bridge()
        abs_path = os.path.join(bridge.server_root, notebook_path)

        if not os.path.exists(abs_path):
            self.set_status(404)
            self.finish(json.dumps({"error": "Notebook not found"}))
            return

        try:
            _debug(f"Exporting PDF: {notebook_path}")

            loop = asyncio.get_event_loop()

            _debug("  Converting notebook → LaTeX")
            tex_body, resources = await loop.run_in_executor(
                None, lambda: _notebook_to_latex(abs_path)
            )

            _debug("  Compiling LaTeX → PDF via tectonic")
            pdf_data = await loop.run_in_executor(
                None, lambda: _compile_tex_to_pdf(tex_body, resources)
            )

            stem = Path(notebook_path).stem
            self.set_header("Content-Type", "application/pdf")
            self.set_header(
                "Content-Disposition",
                f'attachment; filename="{stem}.pdf"',
            )
            self.write(pdf_data)
            _debug(f"PDF export complete: {stem}.pdf ({len(pdf_data)} bytes)")

        except Exception as e:
            _debug(f"PDF export error: {e}")
            logger.exception("Error exporting PDF")
            self.set_status(500)
            self.finish(json.dumps({"error": f"PDF export failed: {str(e)}"}))


class ExportLatexHandler(APIHandler):
    """POST /api/paper-review/export-latex — Export a notebook as .tex."""

    @web.authenticated
    async def post(self):
        body = self.get_json_body()
        notebook_path = body.get("path")

        if not notebook_path:
            self.set_status(400)
            self.finish(json.dumps({"error": "path is required"}))
            return

        bridge = _get_bridge()
        abs_path = os.path.join(bridge.server_root, notebook_path)

        if not os.path.exists(abs_path):
            self.set_status(404)
            self.finish(json.dumps({"error": "Notebook not found"}))
            return

        try:
            _debug(f"Exporting LaTeX: {notebook_path}")

            loop = asyncio.get_event_loop()
            tex_body, _resources = await loop.run_in_executor(
                None, lambda: _notebook_to_latex(abs_path)
            )

            stem = Path(notebook_path).stem
            self.set_header("Content-Type", "application/x-tex")
            self.set_header(
                "Content-Disposition",
                f'attachment; filename="{stem}.tex"',
            )
            self.write(tex_body)
            _debug(f"LaTeX export complete: {stem}.tex")

        except Exception as e:
            _debug(f"LaTeX export error: {e}")
            logger.exception("Error exporting LaTeX")
            self.set_status(500)
            self.finish(json.dumps({"error": f"LaTeX export failed: {str(e)}"}))


def setup_handlers(web_app):
    """Register all API handlers."""
    host_pattern = ".*$"
    base_url = web_app.settings["base_url"]
    route = lambda path: f"{base_url}api/paper-review/{path}"

    server_root = os.path.expanduser(
        web_app.settings.get("server_root_dir", os.getcwd())
    )
    data_dir = os.path.join(server_root, "data")

    # Initialize bridge with data dir and server root
    bridge = _get_bridge(data_dir, server_root=server_root)

    # Register shutdown hook to clean up SDK clients
    import atexit

    def _shutdown_bridge():
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(bridge.shutdown())
            else:
                loop.run_until_complete(bridge.shutdown())
        except Exception as e:
            _debug(f"Error during bridge shutdown: {e}")

    atexit.register(_shutdown_bridge)

    handlers = [
        (route("chat"), ChatHandler),
        (route(r"subscribe/(.+)"), SubscribeHandler),
        (route(r"stream-status/(.+)"), StreamStatusHandler),
        (route("cancel"), CancelHandler),
        (route("sessions"), SessionsHandler),
        (route(r"sessions/(.+)"), SessionHandler),
        (route("models"), ModelsHandler),
        (route("notebooks"), NotebooksHandler),
        (route("export-pdf"), ExportPdfHandler),
        (route("export-latex"), ExportLatexHandler),
    ]
    web_app.add_handlers(host_pattern, handlers)
    logger.info(f"Paper Review API handlers registered at {base_url}api/paper-review/")
