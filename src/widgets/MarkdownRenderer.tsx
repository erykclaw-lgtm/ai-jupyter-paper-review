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

export const MarkdownRenderer = React.memo(function MarkdownRenderer({
  content,
}: MarkdownRendererProps): React.ReactElement {
  return (
    <ReactMarkdown
      remarkPlugins={REMARK_PLUGINS}
      rehypePlugins={REHYPE_PLUGINS}
    >
      {content}
    </ReactMarkdown>
  );
});
