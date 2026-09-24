import { useMutation, useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  Camera,
  Database,
  Gauge,
  SendHorizontal,
  ShieldCheck,
  Sparkles,
  Truck,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { ChatTurn, ToolUse } from "../api/types";

const SUGGESTIONS = [
  "How accurate has the AI count been this week?",
  "Which trucks had the biggest variance?",
  "Show me today's disputed sessions",
  "Are any cameras offline?",
  "How many crates did we count per day this week?",
];

/** The assistant's whole surface area: these five queries and nothing else. */
const TOOLS = [
  { name: "list_sessions", label: "Truck sessions", icon: Truck },
  { name: "accuracy_report", label: "Accuracy report", icon: Gauge },
  { name: "totals_by_plate", label: "Totals by truck", icon: BarChart3 },
  { name: "daily_totals", label: "Daily totals", icon: BarChart3 },
  { name: "camera_health", label: "Camera health", icon: Camera },
];
const TOOL_LABEL: Record<string, string> = Object.fromEntries(
  TOOLS.map((t) => [t.name, t.label]),
);

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
  const used = new Set(entries.flatMap((e) => e.tools?.map((t) => t.name) ?? []));

  return (
    <div className="flex h-[calc(100vh-6.5rem)] flex-col">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight text-ink">Analysis assistant</h1>
          <p className="mt-0.5 text-sm text-muted">
            Answers computed from live platform data, never from the model's memory
          </p>
        </div>
        {status.data?.model && (
          <span className="chip num bg-brand-tint text-brand">
            <ShieldCheck size={12} /> {status.data.model} · on your own infrastructure
          </span>
        )}
      </div>

      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[1fr_17rem]">
        {/* conversation */}
        <section className="card flex min-h-0 flex-col overflow-hidden">
          <div className="flex-1 space-y-4 overflow-y-auto p-4">
            {disabled && (
              <div className="rounded-lg border border-warn/30 bg-warn/10 p-4 text-sm text-warn">
                The assistant is not configured. Point <code>IVAAS_LLM_URL</code> at an
                OpenAI-compatible model server (Ollama, vLLM, llama.cpp) and restart the API.
              </div>
            )}

            {entries.length === 0 && !disabled && (
              <div className="grid h-full place-items-center px-4">
                <div className="max-w-md text-center">
                  <div className="mx-auto grid h-11 w-11 place-items-center rounded-xl bg-accent-tint text-accent">
                    <Sparkles size={20} />
                  </div>
                  <h2 className="mt-3 text-base font-bold text-ink">Ask about the operation</h2>
                  <p className="mt-1 text-sm text-muted">
                    Every reply lists the data it queried. If nothing was queried, you are told.
                  </p>
                  <div className="mt-5 flex flex-wrap justify-center gap-1.5">
                    {SUGGESTIONS.map((s) => (
                      <button
                        key={s}
                        onClick={() => send(s)}
                        className="rounded-full border border-line bg-surface px-3 py-1.5 text-xs font-medium text-ink transition hover:border-brand hover:bg-brand-tint"
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {entries.map((m, i) => (
              <div
                key={i}
                className={m.role === "user" ? "flex justify-end" : "flex justify-start"}
              >
                <div className="max-w-[85%]">
                  <div
                    className={`whitespace-pre-wrap rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed ${
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
                        <span key={name} className="chip bg-brand-tint text-brand">
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
                Querying platform data…
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
            className="flex gap-2 border-t border-line p-3"
            onSubmit={(e) => {
              e.preventDefault();
              send(draft);
            }}
          >
            <input
              id="assistant-draft"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              disabled={disabled}
              placeholder="Ask about crate counts, accuracy, trucks or cameras…"
              aria-label="Message"
              className="input flex-1"
            />
            <button className="btn-accent" disabled={disabled || ask.isPending || !draft.trim()}>
              <SendHorizontal size={15} />
              Send
            </button>
          </form>
        </section>

        {/* what it can reach: the guardrail, stated plainly */}
        <aside className="card hidden flex-col overflow-hidden lg:flex">
          <div className="panel-head">
            <h2 className="panel-title">Data it can read</h2>
          </div>
          <ul className="flex-1 divide-y divide-line overflow-y-auto">
            {TOOLS.map(({ name, label, icon: Icon }) => (
              <li key={name} className="flex items-center gap-2.5 px-3 py-2.5">
                <Icon size={14} className="flex-none text-muted" />
                <span className="flex-1 text-xs font-medium text-ink">{label}</span>
                {used.has(name) && (
                  <span className="chip bg-good/10 px-1.5 py-0 text-[10px] text-good">used</span>
                )}
              </li>
            ))}
          </ul>
          <p className="border-t border-line px-3 py-2.5 text-[11px] leading-relaxed text-muted">
            Read-only. There is no SQL, no write access and no code execution, so the assistant
            cannot change anything on the platform.
          </p>
        </aside>
      </div>
    </div>
  );
}
