import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { THEME_BOOT_SCRIPT } from "@/lib/theme-boot";

import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "AgentForge",
  description: "Evidence-grounded, human-supervised AI workflows over private documents.",
  robots: { index: false, follow: false },
  icons: { icon: "/icon.svg" },
};

export const viewport: Viewport = { themeColor: "#0c0b1d" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // The boot script sets data-theme before React hydrates, hence suppressHydrationWarning.
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body className="min-h-screen antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
