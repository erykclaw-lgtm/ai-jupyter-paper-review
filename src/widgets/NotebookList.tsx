import * as React from 'react';
import { useState, useEffect, useCallback } from 'react';
import { listNotebooks, NotebookInfo, openNotebook, exportPdf, exportLatex } from '../services/api';

export function NotebookList(): React.ReactElement {
  const [notebooks, setNotebooks] = useState<NotebookInfo[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const [exportingPath, setExportingPath] = useState<string | null>(null);
  const [exportingLatexPath, setExportingLatexPath] = useState<string | null>(null);

  const loadNotebooks = useCallback(async () => {
    setIsLoading(true);
    setError('');
    try {
      const data = await listNotebooks();
      setNotebooks(data);
    } catch (err) {
      setError('Failed to load notebooks');
      console.error('Failed to load notebooks:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadNotebooks();
    // Refresh every 30 seconds in case Claude creates new notebooks
    const interval = setInterval(loadNotebooks, 30000);
    return () => clearInterval(interval);
  }, [loadNotebooks]);

  const handleOpen = useCallback(async (path: string) => {
    try {
      await openNotebook(path);
    } catch (err) {
      console.error('Failed to open notebook:', err);
    }
  }, []);

  const handleExportPdf = useCallback(async (e: React.MouseEvent, path: string) => {
    e.stopPropagation();
    setExportingPath(path);
    setError('');
    try {
      await exportPdf(path);
    } catch (err) {
      setError(`PDF export failed: ${err instanceof Error ? err.message : String(err)}`);
      console.error('Failed to export PDF:', err);
    } finally {
      setExportingPath(null);
    }
  }, []);

  const handleExportLatex = useCallback(async (e: React.MouseEvent, path: string) => {
    e.stopPropagation();
    setExportingLatexPath(path);
    setError('');
    try {
      await exportLatex(path);
    } catch (err) {
      setError(`LaTeX export failed: ${err instanceof Error ? err.message : String(err)}`);
      console.error('Failed to export LaTeX:', err);
    } finally {
      setExportingLatexPath(null);
    }
  }, []);

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

  const formatSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div className="pr-notebook-list">
      <div className="pr-notebook-header">
        <span className="pr-label">Review Notebooks</span>
        <button
          className="pr-btn-small"
          onClick={loadNotebooks}
          disabled={isLoading}
        >
          {isLoading ? '...' : '\u21BB Refresh'}
        </button>
      </div>

      {error && <div className="pr-notebook-error">{error}</div>}

      <div className="pr-notebook-items">
        {notebooks.length === 0 && !isLoading && (
          <div className="pr-notebook-empty">
            <p>No notebooks yet.</p>
            <p className="pr-hint">
              Use the "Create Review Notebook" prompt or ask Claude to create one
              for your paper.
            </p>
          </div>
        )}

        {notebooks.map(nb => (
          <div
            key={nb.path}
            className="pr-notebook-item"
            onClick={() => handleOpen(nb.path)}
          >
            <div className="pr-notebook-icon">{'\uD83D\uDCD3'}</div>
            <div className="pr-notebook-info">
              <div className="pr-notebook-name">{nb.name}</div>
              <div className="pr-notebook-meta">
                {formatDate(nb.last_modified)}
                {nb.size > 0 && ` \u00B7 ${formatSize(nb.size)}`}
              </div>
            </div>
            <button
              className="pr-notebook-export-btn"
              onClick={e => handleExportLatex(e, nb.path)}
              disabled={exportingLatexPath === nb.path}
              title="Export as LaTeX"
            >
              {exportingLatexPath === nb.path ? '...' : 'TeX'}
            </button>
            <button
              className="pr-notebook-export-btn"
              onClick={e => handleExportPdf(e, nb.path)}
              disabled={exportingPath === nb.path}
              title="Export as PDF"
            >
              {exportingPath === nb.path ? '...' : 'PDF'}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
