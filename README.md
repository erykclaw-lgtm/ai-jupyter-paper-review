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

### 1. Clone and install the extension

```bash
git clone <repo-url>
cd ai-jupyter-paper-review

# Install the JupyterLab extension and server extension
pip install -e .
```

### 2. Create the Paper Review kernel

This creates a Python virtual environment with scientific computing packages and registers it as a Jupyter kernel:

```bash
./create_kernel.sh
```

This installs: NumPy, SciPy, Matplotlib, Pandas, scikit-learn, Seaborn, SymPy, JAX, transformers, and (where available) PyTorch.

### 3. Build the frontend

```bash
# Install JS dependencies
jlpm install

# Build the extension
jlpm build
```

### 4. Start JupyterLab

```bash
jupyter lab
```

The Paper Review panel will auto-open in the right sidebar.

## Usage

1. **Create a session** using the "+ New" button in the Sessions area
2. **Send a paper URL** in the chat or use the **Prompts** menu for common actions:
   - *Initialize Session* — fetch and begin reviewing a paper from URL
   - *Deep Review* — produce a comprehensive pedagogical review notebook
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
├── create_kernel.sh              # Kernel setup script
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

The extension supports multiple Claude models, selectable from the sidebar dropdown:

- Claude Opus 4.6
- Claude Sonnet 4.6 (default)
- Claude Haiku 4.5

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
