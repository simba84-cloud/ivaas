import { useMutation, useQuery } from "@tanstack/react-query";
import { Database, SendHorizontal, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { ChatTurn, ToolUse } from "../api/types";
import { PageHeader } from "../components/ui";

const SUGGESTIONS = [
  "How accurate has the AI count been this week?",
  "Which trucks had the biggest variance?",
  "Show me today's disputed sessions",
  "Are any cameras offline?",
  "How many crates did we count per day this week?",
];

const TOOL_LABEL: Record<string, string> = {
  list_sessions: "Truck sessions",
  accuracy_report: "Accuracy report",
  totals_by_plate: "Totals by truck",
  daily_totals: "Daily totals",
  camera_health: "Camera health",
};

interface Entry extends ChatTurn {
  tools?: ToolUse[];
}

export default function Assistant() {
  const status = useQuery({ queryKey: ["assistant-status"], queryFn: api.assistantStatus });
  const [entries, setEntries] = useState<Entry[]>([]);
  const [draft, setDraft] = useState("");
  const bottom = useRef<HTMLDivElement>(null);

  const ask = useMutation({
    mutationFn: (history: ChatTurn[]) => api.chat(history),
    onSuccess: (r) =>
      setEntries((e) => [...e, { role: "assistant", content: r.reply, tools: r.tools_used }]),
  });

  useEffect(() => {
    // braces matter: Chrome's smooth scrollIntoView returns a Promise, and an effect
    // that returns anything other than a cleanup function crashes the component
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [entries, ask.isPending]);

  const send = (text: string) => {
    const content = text.trim();
    if (!content || ask.isPending) return;
    const next: Entry[] = [...entries, { role: "user", content }];
    setEntries(next);
    setDraft("");
    ask.mutate(next.map(({ role, content }) => ({ role, content })));
  };

  const disabled = status.data?.enabled === false;

  return (
    <div className="mx-auto flex h-[calc(100vh-7rem)] max-w-4xl flex-col">
      <PageHeader
        title="Analysis Assistant"
        subtitle={
          status.data?.enabled
            ? `Ask about counts, accuracy, trucks and cameras · ${status.data.model}, running on your own infrastructure`
            : "Ask about counts, accuracy, trucks and cameras"
        }
      />

      <div className="card flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="flex-1 space-y-4 overflow-y-auto p-5">
          {disabled && (
            <div className="rounded-lg border border-warn/30 bg-warn/10 p-4 text-sm text-warn">
              The assistant is not configured. Point <code>IVAAS_LLM_URL</code> at an
              OpenAI-compatible model server (Ollama, vLLM, llama.cpp) and restart the API.
            </div>
          )}

          {entries.length === 0 && !disabled && (
            <div className="py-8 text-center">
              <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-accent-tint text-accent">
                <Sparkles size={22} />
              </div>
              <p className="mt-3 text-sm text-muted">
                Answers come from live platform data. Every reply shows which data it used.
              </p>
              <div className="mt-5 flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    className="rounded-full border border-line bg-surface px-3.5 py-1.5 text-sm text-ink hover:border-brand hover:bg-brand-tint"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {entries.map((m, i) => (
            <div key={i} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
              <div className="max-w-[85%]">
                <div
                  className={`whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
                    m.role === "user"
                      ? "rounded-br-sm bg-brand text-white"
                      : "rounded-bl-sm bg-ground text-ink"
                  }`}
                >
                  {m.content || "(no answer)"}
                </div>
                {m.tools && m.tools.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-muted">
                    <Database size={12} />
                    {[...new Set(m.tools.map((t) => t.name))].map((name) => (
                      <span key={name} className="rounded bg-brand-tint px-1.5 py-0.5">
                        {TOOL_LABEL[name] ?? name}
                      </span>
                    ))}
                  </div>
                )}
                {m.role === "assistant" && (!m.tools || m.tools.length === 0) && (
                  <div className="mt-1.5 text-xs text-warn">
                    No platform data was queried for this reply. Treat any figures with caution.
                  </div>
                )}
              </div>
            </div>
          ))}

          {ask.isPending && (
            <div className="flex items-center gap-2 text-sm text-muted">
              <span className="h-2 w-2 animate-pulse rounded-full bg-accent" />
              Analysing…
            </div>
          )}
          {ask.isError && (
            <div className="rounded-lg bg-bad/10 px-4 py-2 text-sm text-bad">
              {(ask.error as Error).message}
            </div>
          )}
          <div ref={bottom} />
        </div>

        <form
          className="flex gap-2 border-t border-line bg-surface p-3"
          onSubmit={(e) => {
            e.preventDefault();
            send(draft);
          }}
        >
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            disabled={disabled}
            placeholder="Ask about crate counts, accuracy, trucks or cameras…"
            aria-label="Message"
            className="flex-1 rounded-lg border border-line px-3.5 py-2.5 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/10 disabled:bg-ground"
          />
          <button className="btn-accent" disabled={disabled || ask.isPending || !draft.trim()}>
            <SendHorizontal size={16} />
            Send
          </button>
        </form>
      </div>
    </div>
  );
}
