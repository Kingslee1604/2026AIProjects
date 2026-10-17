"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import type { RunEvent } from "./runs";

export type StreamState = "connecting" | "live" | "reconnecting" | "ended";

const REOPEN_MS = 2000;
const REFRESH_MS = 300;

/** Follows a run live (server-sent events). Each event refreshes the run's data, at most every
 * 300 ms. After a dropped connection the browser reconnects with Last-Event-ID; if it gives up,
 * or the browser was offline, we reopen from the last event received: no gaps and no
 * duplicates either way. */
export function useRunStream(runId: string) {
  const queryClient = useQueryClient();
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [state, setState] = useState<StreamState>("connecting");
  const last = useRef(0); // survives a remount (React strict mode), so nothing is added twice

  useEffect(() => {
    let source: EventSource | null = null;
    let finished = false;
    let reopen: ReturnType<typeof setTimeout> | undefined;
    let refresh: ReturnType<typeof setTimeout> | undefined;

    function refreshSoon() {
      refresh ??= setTimeout(() => {
        refresh = undefined;
        void queryClient.invalidateQueries({ queryKey: ["runs", runId] });
      }, REFRESH_MS);
    }

    function open() {
      source = new EventSource(
        `/api/v1/runs/${encodeURIComponent(runId)}/stream?after=${last.current}`,
      );
      source.onopen = () => setState("live");
      source.addEventListener("run_event", (message) => {
        const event = JSON.parse((message as MessageEvent<string>).data) as RunEvent;
        if (event.seq <= last.current) return;
        last.current = event.seq;
        setEvents((current) => [...current, event]);
        refreshSoon();
      });
      source.addEventListener("end", () => {
        finished = true;
        source?.close();
        setState("ended");
        refreshSoon();
      });
      source.onerror = () => {
        if (finished || !navigator.onLine) return;
        setState("reconnecting");
        // CONNECTING: the browser is already retrying. CLOSED: it gave up, so reopen ourselves.
        if (source?.readyState === EventSource.CLOSED) reopen = setTimeout(open, REOPEN_MS);
      };
    }

    // A connection can look open for minutes after the network is gone: when the browser says
    // it is offline, say so at once, and resume from the last event as soon as it is back.
    function onOffline() {
      if (finished) return;
      source?.close();
      clearTimeout(reopen);
      setState("reconnecting");
    }
    function onOnline() {
      if (finished) return;
      source?.close();
      clearTimeout(reopen);
      open();
    }

    open();
    window.addEventListener("offline", onOffline);
    window.addEventListener("online", onOnline);
    return () => {
      finished = true;
      source?.close();
      clearTimeout(reopen);
      clearTimeout(refresh);
      window.removeEventListener("offline", onOffline);
      window.removeEventListener("online", onOnline);
    };
  }, [runId, queryClient]);

  return { events, state };
}
