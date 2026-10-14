export interface Segment {
  text: string;
  highlight: boolean;
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Normalizes curly quotes and dashes the same way the server's quote check does. */
function loosen(word: string): string {
  return escapeRegExp(word)
    .replace(/['‘’]/g, "['‘’]")
    .replace(/["“”]/g, '["“”]')
    .replace(/[-–—]/g, "[-–—]");
}

/**
 * Splits `content` around the first occurrence of `quote`, ignoring case, extra whitespace,
 * line breaks, Markdown emphasis, and typographic quotes. Returns one plain segment when the
 * quote is not found (the passage is still shown, just without a highlight).
 */
export function highlightQuote(content: string, quote: string): Segment[] {
  const words = quote
    .replace(/^[\s"'.,;:]+|[\s"'.,;:]+$/g, "")
    .split(/\s+/)
    .filter(Boolean);
  if (words.length === 0) return [{ text: content, highlight: false }];
  const pattern = new RegExp(words.map(loosen).join("[\\s*_]+"), "i");
  const match = pattern.exec(content);
  if (!match) return [{ text: content, highlight: false }];
  const start = match.index;
  const end = start + match[0].length;
  return [
    { text: content.slice(0, start), highlight: false },
    { text: content.slice(start, end), highlight: true },
    { text: content.slice(end), highlight: false },
  ].filter((s) => s.text.length > 0);
}
