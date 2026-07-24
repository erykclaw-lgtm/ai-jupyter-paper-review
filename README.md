# AI Jupyter Paper Review

A JupyterLab extension that uses Claude as an AI backend for producing deep, pedagogical reviews of academic papers. Claude reads papers, derives equations step-by-step, writes annotated code demonstrations, and outputs everything as executable Jupyter notebooks.

## What It Does

Give Claude a paper URL (arXiv, DOI, PDF) and it produces a comprehensive tutorial-length review notebook that includes:

- **Step-by-step mathematical derivations** with every intermediate step shown and annotated
- **Executable code demonstrations** (PyTorch, NumPy, JAX) illustrating key mechanisms
- **Pedagogical explanations** written in the voice of a seminar instructor
- **Background prerequisites** section covering any advanced concepts used
- **Related work synthesis** via web search for blog posts, follow-up papers, and errata
- **Exercises with answers** at the end of each notebook for self-assessment

The extension runs as a sidebar panel in JupyterLab with a chat interface, session management, a prompt bank for common actions, and a notebook browser.

## Architecture

```
JupyterLab (browser)
  └─ Paper Review sidebar panel (React)
       ├─ Chat panel (SSE streaming)
       ├─ Session manager
       ├─ Prompt bank
       └─ Notebook browser

Jupyter Server (Python)
  └─ jupyter_paper_review extension
       ├─ Tornado API handlers (REST + SSE)
       ├─ Claude Bridge (claude-agent-sdk)
       │    └─ Long-lived ClaudeSDKClient per session
       └─ Session persistence (JSON files)

Claude Code (subprocess)
  └─ Managed by claude-agent-sdk
       ├─ Tools: Read, Write, Edit, Bash, WebSearch, WebFetch, Grep, Glob
       ├─ Works in reviews/ directory
       └─ Context preserved across messages (no restart per message)
```

The Claude Agent SDK maintains a persistent Claude Code process per session, so conversation context is preserved across messages without restarting.

## Requirements

- **Python 3.10+**
- **Node.js 18+** (for building the frontend extension)
- **Claude Code CLI** installed and authenticated (`npm install -g @anthropic-ai/claude-code`)
- **JupyterLab 4.x**

## Setup

```bash
git clone <repo-url>
cd ai-jupyter-paper-review

./setup.sh
```

`setup.sh` is idempotent and does everything in one shot:

1. Installs the server + lab extension into the server's Python (`pip install -e .`).
2. Installs the optional **openai-codex** SDK from PyPI so GPT/Codex models work. Without it, only Claude models appear.
3. Installs JS deps and builds the frontend (`jlpm install && jlpm build`).
4. Creates the **paper-review** kernel venv (NumPy, SciPy, Matplotlib, Pandas, scikit-learn, Seaborn, SymPy, JAX, transformers, and where available PyTorch).

Then start the app:

```bash
./start.sh
```

The Paper Review panel will auto-open in the right sidebar.

## Usage

1. **Create a session** using the "+ New" button in the Sessions area
2. **Send a paper URL** in the chat or use the **Prompts** menu for common actions:
   - *Guided Walkthrough* — step-by-step review of one paper with math derivations, intuition, and code demos
   - *Multi-Paper Survey* — synthesize several papers into one unified survey/review notebook
   - *Quick Summary* — shorter overview of a paper
   - *Search Related Work* — find related papers and resources
3. **Watch Claude work** — the chat shows streaming text, tool usage indicators, and progress
4. **View notebooks** in the Notebooks tab — click to open in JupyterLab's main area
5. **Rename sessions** by double-clicking the session title
6. **Notebooks auto-refresh** when Claude edits them (file watcher detects changes)

## Project Structure

```
ai-jupyter-paper-review/
├── src/                          # Frontend (TypeScript/React)
│   ├── index.ts                  # JupyterLab plugin entry point
│   ├── panel.tsx                 # Main panel component
│   ├── services/api.ts           # API client (SSE streaming)
│   └── widgets/
│       ├── ChatPanel.tsx          # Chat interface with streaming
│       ├── SessionList.tsx        # Session management (CRUD, rename)
│       ├── PromptBank.tsx         # Quick-action prompt templates
│       ├── NotebookList.tsx       # Notebook browser
│       ├── ModelSelector.tsx      # Model selection dropdown
│       └── MarkdownRenderer.tsx   # Markdown + LaTeX + code rendering
├── jupyter_paper_review/         # Backend (Python)
│   ├── __init__.py               # Extension registration
│   ├── handlers.py               # Tornado API handlers
│   ├── claude_bridge.py          # Claude Agent SDK integration
│   └── session_manager.py        # Session CRUD wrapper
├── data/                         # Runtime data (gitignored)
│   ├── reviews/                  # Generated review notebooks
│   └── sessions/                 # Session state files
├── style/index.css               # Styles (JupyterLab theme vars)
├── jupyter-config/               # Jupyter server config
├── setup.sh                      # One-shot environment setup
├── create_kernel.sh              # Kernel setup script (called by setup.sh)
├── start.sh                      # Dev startup script (miniforge-specific)
├── package.json                  # JS dependencies
├── pyproject.toml                # Python package config
└── tsconfig.json                 # TypeScript config
```

## Development

For active development with hot-reload:

```bash
# Terminal 1: Watch TypeScript and rebuild on changes
jlpm watch

# Terminal 2: Start JupyterLab
jupyter lab
```

The `jlpm watch` command runs both `tsc -w` (TypeScript compiler) and `jupyter labextension watch .` (webpack rebuild) in parallel.

### Rebuilding after changes

- **Frontend (TypeScript/CSS):** `jlpm build` then reload JupyterLab in browser
- **Backend (Python):** Restart JupyterLab server (Python changes need server restart)
- **Both:** `jlpm build && jupyter lab`

## Configuration

### Models

Models are discovered dynamically and selectable from the sidebar dropdown — no
hardcoded list to maintain:

- **Claude** models come from the Anthropic models API (authenticated via the
  Claude CLI's stored OAuth credentials), filtered to the latest release per
  family (e.g. Opus, Sonnet, Fable, Haiku).
- **GPT/Codex** models come from the openai-codex SDK, likewise filtered to the
  latest per variant family (base, mini, codex, ...).

New releases appear in the dropdown automatically (the backend caches discovery
for 30 minutes and the panel refreshes every 15 minutes, or on tab return). If
live discovery is unavailable (offline, expired credentials), a static fallback
list is shown instead.

### System Prompt

The review instructions are defined in `jupyter_paper_review/claude_bridge.py` in the `PAPER_REVIEW_SYSTEM_PROMPT` variable. This controls the depth, style, and format of reviews.

### Claude Code Permissions

Claude Code runs with `bypassPermissions` mode, giving it full access to read, write, and execute within the `reviews/` directory. The allowed tools are: WebSearch, WebFetch, Bash, Read, Edit, Write, Grep, Glob.

## Data Storage

All runtime data is stored in `data/` and is gitignored:

- **Sessions:** `data/sessions/*.json` — chat history and session metadata
- **Notebooks:** `data/reviews/*.ipynb` — generated review notebooks
- **Exercise answers:** `data/reviews/*.answers` — companion answer files

## License

MIT
