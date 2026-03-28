import { markdownToHtml } from '../utils/markdown';

interface Props { content: string; className?: string; }

export default function MarkdownBlock({ content, className }: Props) {
  if (!content) return null;
  return <div className={className} dangerouslySetInnerHTML={{ __html: markdownToHtml(content) }} />;
}
