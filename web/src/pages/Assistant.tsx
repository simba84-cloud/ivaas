import { useMutation, useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import {
  BarChart3,
  Camera,
  ChevronDown,
  Database,
  FileText,
  Gauge,
  SendHorizontal,
  ShieldCheck,
  Sparkles,
  Truck,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { ChatTurn, ToolUse } from "../api/types";
import { staggerDelay, transition } from "../motion";

const SUGGESTIONS = [
  "How many crates went out yesterday, and how many came back?",
  "Which trucks still have crates out this week?",
  "How accurate has the AI count been this week?",
  "Show me today's disputed sessions",
  "Are any cameras offline?",
];

/** The assistant's whole surface area: these five queries and nothing else. The
 * figures are the daily report's and the Balances page's, computed by the same code. */
const TOOLS = [
  { name: "list_sessions", label: "Truck sessions", icon: Truck },
  { name: "daily_report", label: "Daily report", icon: FileText },
  { name: "balances", label: "Balances", icon: BarChart3 },
  { name: "accuracy_report", label: "Accuracy report", icon: Gauge },
  { name: "camera_health", label: "Camera health", icon: Camera },
];
const TOOL_LABEL: Record<string, string> = Object.fromEntries(
  TOOLS.map((t) => [t.name, t.label]),
);

interface Entry extends ChatTurn {
  tools?: ToolUse[];
}

/** Shown while the reply is being worked out. The seconds are real: it says how long, not how far. */
function Thinking() {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setSecs((s) => s + 1), 1000);
    return () => window.clearInterval(id);
  }, []);
  return (
    <motion.div
      initial={{ y: 8 }}
      animate={{ y: 0 }}
      transition={transition.normal}
      role="status"
      aria-label="The assistant is querying platform data"
      className="flex items-center gap-3"
    >
      <span className="inline-flex items-center gap-1 rounded-2xl rounded-bl-sm bg-ground px-3.5 py-3">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            aria-hidden
            className="h-1.5 w-1.5 rounded-full bg-accent"
            style={{ animation: `typing 1.1s ${i * 0.15}s ease-in-out infinite` }}
          />
        ))}
      </span>
      <span className="text-sm text-muted">
        Querying platform data{secs >= 2 && <span className="num text-faint"> · {secs} s</span>}
      </span>
    </motion.div>
  );
}

/** What a reply was computed from: the queries, and the exact parameters they ran with. */
function Sources({ tools }: { tools: ToolUse[] }) {
  const [open, setOpen] = useState(false);
  const names = [...new Set(tools.map((t) => t.name))];
  return (
    <div className="mt-1.5">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex flex-wrap items-center gap-1.5 rounded-md text-xs text-muted transition hover:text-ink"
      >
        <Database size={12} />
        {names.map((name, i) => (
          <motion.span
            key={name}
            initial={{ scale: 0.8 }}
            animate={{ scale: 1 }}
            transition={{ ...transition.elastic, delay: 0.1 + i * 0.06 }}
            className="chip bg-brand-tint text-brand"
          >
            {TOOL_LABEL[name] ?? name}
          </motion.span>
        ))}
        <motion.span animate={{ rotate: open ? 180 : 0 }} transition={transition.fast} className="inline-flex">
          <ChevronDown size={13} />
        </motion.span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.ul
            key="sources"
            initial={{ y: -6 }}
            animate={{ y: 0 }}
            exit={{ y: -6, transition: transition.fast }}
            className="mt-2 space-y-1.5 rounded-xl border border-line bg-surface p-2.5"
          >
            {tools.map((t, i) => (
              <li key={i} className="text-xs">
                <span className="font-semibold text-ink">{TOOL_LABEL[t.name] ?? t.name}</span>
                <span className="num ml-2 text-muted">
                  {Object.keys(t.arguments).length
                    ? Object.entries(t.arguments)
                        .map(([k, v]) => `${k.replace(/_/g, " ")}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
                        .join(" · ")
                    : "no parameters"}
                </span>
              </li>
            ))}
          </motion.ul>
        )}
      </AnimatePresence>
    </div>
  );
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
                    {SUGGESTIONS.map((s, i) => (
                      <motion.button
                        key={s}
                        initial={{ y: 8 }}
                        animate={{ y: 0 }}
                        transition={{ ...transition.normal, delay: staggerDelay(i) }}
                        onClick={() => send(s)}
                        className="rounded-full border border-line bg-surface px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:border-brand hover:bg-brand-tint active:scale-[0.97]"
                      >
                        {s}
                      </motion.button>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {entries.map((m, i) => (
              <motion.div
                key={i}
                initial={{ y: 10, x: m.role === "user" ? 12 : -12 }}
                animate={{ y: 0, x: 0 }}
                transition={transition.spring}
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
                  {m.tools && m.tools.length > 0 && <Sources tools={m.tools} />}
                  {m.role === "assistant" && (!m.tools || m.tools.length === 0) && (
                    <div className="mt-1.5 text-xs text-warn">
                      No platform data was queried for this reply. Treat any figures with caution.
                    </div>
                  )}
                </div>
              </motion.div>
            ))}

            {ask.isPending && <Thinking />}
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
                  <motion.span
                    initial={{ scale: 0.6 }}
                    animate={{ scale: 1 }}
                    transition={transition.elastic}
                    className="chip bg-good/10 px-1.5 py-0 text-[10px] text-good"
                  >
                    used
                  </motion.span>
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
