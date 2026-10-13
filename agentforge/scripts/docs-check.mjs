// Parses every ```mermaid block in Markdown files under the given paths with mermaid.parse().
// Usage: node scripts/docs-check.mjs <file-or-dir> [...]
// This is a syntax check; it does not render layouts.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>", { pretendToBeVisual: true });
for (const key of [
  "window",
  "document",
  "navigator",
  "Element",
  "HTMLElement",
  "SVGElement",
  "DOMParser",
  "Node",
]) {
  try {
    globalThis[key] = dom.window[key] ?? dom.window;
  } catch {
    // Some globals (e.g. navigator) are read-only in newer Node versions; jsdom's window covers them.
  }
}
const { default: mermaid } = await import("mermaid");
mermaid.initialize({ startOnLoad: false });

// Directories named "archive" hold frozen historical documents and are not validated.
function markdownFiles(path) {
  if (statSync(path).isFile()) return path.endsWith(".md") ? [path] : [];
  return readdirSync(path)
    .filter((name) => name !== "archive")
    .flatMap((name) => markdownFiles(join(path, name)));
}

function mermaidBlocks(text) {
  const blocks = [];
  let current = null;
  text.split(/\r?\n/).forEach((line, index) => {
    if (current === null && /^```mermaid\s*$/.test(line)) current = { line: index + 1, body: [] };
    else if (current !== null && /^```\s*$/.test(line)) {
      blocks.push(current);
      current = null;
    } else if (current !== null) current.body.push(line);
  });
  return blocks;
}

const files = process.argv.slice(2).flatMap(markdownFiles);
let total = 0;
let failed = 0;
for (const file of files) {
  for (const block of mermaidBlocks(readFileSync(file, "utf8"))) {
    total++;
    try {
      await mermaid.parse(block.body.join("\n"));
    } catch (error) {
      failed++;
      const message = String(error?.message ?? error)
        .split("\n")
        .slice(0, 3)
        .join("\n    ");
      console.error(`FAIL ${file}:${block.line}\n    ${message}`);
    }
  }
}
console.log(`docs-check: ${total} Mermaid diagram(s) in ${files.length} file(s), ${failed} failed`);
process.exit(failed ? 1 : 0);
