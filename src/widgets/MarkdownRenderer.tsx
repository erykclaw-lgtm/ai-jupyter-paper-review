import * as React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkMath from 'remark-math';
import remarkGfm from 'remark-gfm';
import rehypeKatex from 'rehype-katex';
import rehypeHighlight from 'rehype-highlight';

interface MarkdownRendererProps {
  content: string;
}

// Stable plugin arrays — declared outside the component so they're created once
const REMARK_PLUGINS = [remarkGfm, remarkMath];
const REHYPE_PLUGINS = [rehypeKatex, rehypeHighlight];

/**
 * Normalize LaTeX math delimiters to the `$`/`$$` forms that remark-math
 * understands.
 *
 * Claude emits `$...$` and `$$...$$` (rendered fine), but GPT/Codex emit
 * `\(...\)` (inline) and `\[...\]` (display). remark-math ignores those, so
 * the raw LaTeX leaks through as text. We rewrite the bracket/paren forms to
 * dollar forms. Claude's content has no `\[`/`\(`, so this is a no-op for it.
 *
 * Code spans/blocks are stashed first so we never rewrite delimiters that are
 * literal code. The `(?<!\\)` guards avoid mangling an escaped `\\[` (LaTeX
 * line break followed by a bracket).
 */
function normalizeMathDelimiters(text: string): string {
  if (text.indexOf('\\[') === -1 && text.indexOf('\\(') === -1) {
    return text; // fast path: nothing to convert (e.g. all Claude output)
  }

  const stash: string[] = [];
  const protect = (s: string): string => {
    stash.push(s);
    return `\uE000${stash.length - 1}\uE000`;
  };

  let t = text
    .replace(/```[\s\S]*?```/g, protect) // fenced code blocks
    .replace(/`[^`\n]*`/g, protect); // inline code

  t = t
    .replace(/(?<!\\)\\\[/g, '$$$$') // \[ -> $$
    .replace(/(?<!\\)\\\]/g, '$$$$') // \] -> $$
    .replace(/(?<!\\)\\\(/g, '$') // \( -> $
    .replace(/(?<!\\)\\\)/g, '$'); // \) -> $

  return t.replace(/\uE000(\d+)\uE000/g, (_, i) => stash[Number(i)]);
}

export const MarkdownRenderer = React.memo(function MarkdownRenderer({
  content,
}: MarkdownRendererProps): React.ReactElement {
  const normalized = React.useMemo(
    () => normalizeMathDelimiters(content),
    [content]
  );
  return (
    <ReactMarkdown
      remarkPlugins={REMARK_PLUGINS}
      rehypePlugins={REHYPE_PLUGINS}
    >
      {normalized}
    </ReactMarkdown>
  );
});
