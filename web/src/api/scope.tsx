/**
 * Which bay the portal is looking at.
 *
 * Every page used to take `bays[0]`, which quietly assumed one bay existed and
 * that it was the right one. The selection lives here instead, remembered per
 * browser, so a deployment with several sites shows one bay at a time rather
 * than blending them into a single set of figures.
 */
import { useQuery } from "@tanstack/react-query";
import { type ReactNode, createContext, useContext, useEffect, useMemo, useState } from "react";
import { api } from "./client";
import type { Bay, Site } from "./types";

const KEY = "ivaas.bay";

interface Scope {
  sites: Site[];
  bays: Bay[];
  bay: Bay | undefined;
  site: Site | undefined;
  setBay: (id: string) => void;
  /** more than one bay exists, so the choice is worth showing */
  multi: boolean;
  loading: boolean;
}

const ScopeContext = createContext<Scope | null>(null);

export function ScopeProvider({ children }: { children: ReactNode }) {
  const sites = useQuery({ queryKey: ["sites"], queryFn: api.sites });
  const bays = useQuery({ queryKey: ["bays"], queryFn: api.bays });
  const [chosen, setChosen] = useState<string | null>(() => {
    try {
      return localStorage.getItem(KEY);
    } catch {
      return null;
    }
  });

  const list = useMemo(() => bays.data ?? [], [bays.data]);
  // a remembered bay that no longer exists must not strand the portal on nothing
  const bay = list.find((b) => b.id === chosen) ?? list[0];

  useEffect(() => {
    if (!bay || bay.id === chosen) return;
    try {
      localStorage.setItem(KEY, bay.id);
    } catch {
      /* storage blocked: the choice lasts for this page */
    }
    setChosen(bay.id);
  }, [bay, chosen]);

  const setBay = (id: string) => {
    try {
      localStorage.setItem(KEY, id);
    } catch {
      /* storage blocked */
    }
    setChosen(id);
  };

  const value: Scope = {
    sites: sites.data ?? [],
    bays: list,
    bay,
    site: (sites.data ?? []).find((s) => s.id === bay?.site_id),
    setBay,
    multi: list.length > 1,
    loading: bays.isLoading,
  };
  return <ScopeContext.Provider value={value}>{children}</ScopeContext.Provider>;
}

export function useScope(): Scope {
  const ctx = useContext(ScopeContext);
  if (!ctx) throw new Error("useScope must be used inside a ScopeProvider");
  return ctx;
}
