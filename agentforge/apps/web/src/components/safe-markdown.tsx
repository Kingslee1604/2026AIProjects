import Markdown, { type Components } from "react-markdown";
import rehypeSanitize from "rehype-sanitize";

/**
 * Renders model output and document text (security baseline S7):
 * - raw HTML is never rendered (react-markdown escapes it; rehype-sanitize is a second layer)
 * - images are never loaded: a remote image URL could leak data to its host, so only the alt
 *   text is shown
 * - links open in a new tab without a referrer or opener
 */
const components: Components = {
  img: ({ alt }) => <span className="text-muted italic">[image: {alt || "no description"}]</span>,
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="underline">
      {children}
    </a>
  ),
};

export function SafeMarkdown({ children }: { children: string }) {
  return (
    <Markdown skipHtml rehypePlugins={[rehypeSanitize]} components={components}>
      {children}
    </Markdown>
  );
}
