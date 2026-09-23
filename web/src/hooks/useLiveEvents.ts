import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { getToken, onAuthChange } from "../auth/session";

/** Subscribes to the API event stream and refreshes cached queries on change. */
export function useLiveEvents(): boolean {
  const queryClient = useQueryClient();
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let ws: WebSocket | undefined;
    let retry: number | undefined;
    let ping: number | undefined;
    let stopped = false;

    const connect = () => {
      const token = getToken();
      if (!token) return; // reconnect when a login happens (see onAuthChange below)
      const scheme = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${scheme}://${location.host}/ws/events?token=${encodeURIComponent(token)}`);
      ws.onopen = () => {
        setConnected(true);
        ping = window.setInterval(() => ws?.send("ping"), 25_000);
      };
      ws.onmessage = (e) => {
        let subject = "";
        try {
          subject = JSON.parse(e.data).subject ?? "";
        } catch {
          /* ignore */
        }
        if (subject.startsWith("ivaas.analysis")) {
          queryClient.invalidateQueries({ queryKey: ["analyses"] });
          queryClient.invalidateQueries({ queryKey: ["analysis"] });
        } else {
          queryClient.invalidateQueries({ queryKey: ["sessions"] });
          queryClient.invalidateQueries({ queryKey: ["summary"] });
          queryClient.invalidateQueries({ queryKey: ["cameras"] });
        }
      };
      ws.onclose = (e) => {
        setConnected(false);
        window.clearInterval(ping);
        if (!stopped && e.code !== 4401) retry = window.setTimeout(connect, 3_000);
      };
    };

    connect();
    const unsubscribe = onAuthChange(() => {
      ws?.close();
      connect();
    });
    return () => {
      stopped = true;
      unsubscribe();
      window.clearTimeout(retry);
      window.clearInterval(ping);
      ws?.close();
    };
  }, [queryClient]);

  return connected;
}
