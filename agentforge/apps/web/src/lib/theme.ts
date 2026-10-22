"use client";

import { useCallback, useSyncExternalStore } from "react";

import { THEME_KEY as KEY } from "./theme-boot";

export type Theme = "dark" | "light";

function current(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Dark by default; the choice is remembered in this browser only. */
export function useTheme(): [Theme, (theme: Theme) => void] {
  const theme = useSyncExternalStore(subscribe, current, () => "dark" as Theme);
  const setTheme = useCallback((next: Theme) => {
    if (next === "light") document.documentElement.dataset.theme = "light";
    else delete document.documentElement.dataset.theme;
    try {
      localStorage.setItem(KEY, next);
    } catch {
      // storage unavailable (private mode): the choice lasts for this page only
    }
    listeners.forEach((listener) => listener());
  }, []);
  return [theme, setTheme];
}
