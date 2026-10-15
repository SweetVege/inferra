"use client";

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { api, ApiError } from "@/lib/api";
import type { Meta, User, Workspace } from "@/lib/types";

interface SessionValue {
  user: User;
  workspaces: Workspace[];
  meta: Meta;
  refreshWorkspaces: () => Promise<void>;
  logout: () => Promise<void>;
}

const SessionContext = createContext<SessionValue | null>(null);

export function SessionProvider({ children, fallback }: { children: ReactNode; fallback: ReactNode }) {
  const router = useRouter();
  const [value, setValue] = useState<Omit<SessionValue, "refreshWorkspaces" | "logout"> | null>(null);

  const load = useCallback(async () => {
    try {
      const [user, workspaces, meta] = await Promise.all([
        api.get<User>("/auth/me"),
        api.get<Workspace[]>("/workspaces"),
        api.get<Meta>("/meta"),
      ]);
      setValue({ user, workspaces, meta });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) router.replace("/login");
      else throw err;
    }
  }, [router]);

  useEffect(() => {
    void load();
  }, [load]);

  const refreshWorkspaces = useCallback(async () => {
    const workspaces = await api.get<Workspace[]>("/workspaces");
    setValue((v) => (v ? { ...v, workspaces } : v));
  }, []);

  const logout = useCallback(async () => {
    await api.post("/auth/logout");
    router.replace("/login");
  }, [router]);

  if (!value) return <>{fallback}</>;
  return (
    <SessionContext.Provider value={{ ...value, refreshWorkspaces, logout }}>{children}</SessionContext.Provider>
  );
}

export function useSession(): SessionValue {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used inside SessionProvider");
  return ctx;
}

export function useWorkspace(id: string): Workspace {
  const { workspaces } = useSession();
  const ws = workspaces.find((w) => w.id === id);
  if (!ws) throw new ApiError(404, "Workspace not found");
  return ws;
}

export function canEdit(ws: Workspace): boolean {
  return ws.role === "owner" || ws.role === "editor";
}
