import {
  ILayoutRestorer,
  JupyterFrontEnd,
  JupyterFrontEndPlugin,
} from '@jupyterlab/application';
import { ICommandPalette, MainAreaWidget } from '@jupyterlab/apputils';
import { IDocumentManager } from '@jupyterlab/docmanager';
import { Widget } from '@lumino/widgets';

import * as React from 'react';
import * as ReactDOM from 'react-dom';
import { PaperReviewPanel } from './panel';

const PLUGIN_ID = '@paper-review/labextension:plugin';
const COMMAND_ID = 'paper-review:open';

/**
 * Poll interval (ms) for checking if open notebooks have been
 * modified on disk by Claude Code.
 */
const FILE_POLL_INTERVAL = 2000;

class PaperReviewWidget extends Widget {
  private _root: HTMLDivElement;

  constructor() {
    super();
    this.id = 'paper-review-panel';
    this.title.label = 'Paper Review';
    this.title.closable = true;
    this.addClass('pr-widget');

    this._root = document.createElement('div');
    this._root.className = 'pr-widget-root';
    this.node.appendChild(this._root);

    ReactDOM.render(
      React.createElement(PaperReviewPanel, {}),
      this._root
    );
  }

  dispose(): void {
    ReactDOM.unmountComponentAtNode(this._root);
    super.dispose();
  }
}

/**
 * Start a background poller that detects when open notebooks have been
 * modified on disk (e.g. by Claude Code) and auto-reverts them so the
 * user sees changes immediately.
 *
 * Only reverts for EXTERNAL changes (Claude editing the file).
 * Skips revert when the user saves from JupyterLab (context already in sync).
 */
function startFileWatcher(
  app: JupyterFrontEnd,
  docManager: IDocumentManager
): void {
  // Track last-known modified times per path
  const knownMtimes = new Map<string, string>();

  setInterval(async () => {
    // Iterate over all open widgets in the main area
    const iter = app.shell.widgets('main');
    let result = iter.next();
    while (!result.done) {
      const w = result.value;
      const context = docManager.contextForWidget(w);
      if (context && context.path.endsWith('.ipynb')) {
        try {
          // Check the file's last_modified via the contents API
          const resolved = await docManager.services.contents.get(
            context.path,
            { content: false }
          );
          const diskMtime = resolved.last_modified;
          const prevMtime = knownMtimes.get(context.path);

          if (prevMtime && diskMtime !== prevMtime) {
            // Disk mtime changed. But was it us (user save) or external (Claude)?
            // If JupyterLab's context already knows this mtime, the user
            // saved from the UI — the content is already in sync, skip revert.
            const contextMtime = (context as any).contentsModel?.last_modified;
            if (contextMtime === diskMtime) {
              console.log(
                `[paper-review] File saved by user: ${context.path}, skipping revert`
              );
            } else {
              // External change — revert to pick up Claude's edits
              console.log(
                `[paper-review] External change: ${context.path}, reverting...`
              );

              // Save scroll position before revert (revert reloads the
              // whole notebook which resets scroll to top)
              const scrollable = w.node.querySelector('.jp-WindowedPanel-outer');
              const scrollTop = scrollable ? scrollable.scrollTop : 0;

              await context.revert();

              // Restore scroll position after the DOM settles.
              // Use a short delay because JupyterLab's windowed notebook
              // rendering needs time to rebuild the virtual DOM.
              if (scrollable && scrollTop > 0) {
                setTimeout(() => {
                  scrollable.scrollTop = scrollTop;
                }, 150);
              }
            }
          }

          knownMtimes.set(context.path, diskMtime);
        } catch (err) {
          // File may have been deleted or not accessible — ignore
        }
      }
      result = iter.next();
    }
  }, FILE_POLL_INTERVAL);
}

const plugin: JupyterFrontEndPlugin<void> = {
  id: PLUGIN_ID,
  autoStart: true,
  requires: [IDocumentManager],
  optional: [ICommandPalette, ILayoutRestorer],
  activate: (
    app: JupyterFrontEnd,
    docManager: IDocumentManager,
    palette: ICommandPalette | null,
    restorer: ILayoutRestorer | null
  ) => {
    console.log('Paper Review extension activated');

    let widget: PaperReviewWidget | null = null;

    app.commands.addCommand(COMMAND_ID, {
      label: 'Paper Review',
      caption: 'Open the Paper Review panel',
      execute: () => {
        if (!widget || widget.isDisposed) {
          widget = new PaperReviewWidget();
        }
        if (!widget.isAttached) {
          app.shell.add(widget, 'right', { rank: 1000 });
        }
        app.shell.activateById(widget.id);
      },
    });

    if (palette) {
      palette.addItem({ command: COMMAND_ID, category: 'Paper Review' });
    }

    // Auto-open on startup
    app.restored.then(() => {
      app.commands.execute(COMMAND_ID);
    });

    // Start watching for external file changes (from Claude Code)
    startFileWatcher(app, docManager);
  },
};

export default plugin;
