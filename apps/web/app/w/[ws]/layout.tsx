"use client";

import { BellRing, Brain, Database, FileText, Gauge, History, Lightbulb, LogOut, Network, Sparkles } from "lucide-react";
import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { Logo } from "@/components/logo";
import { SessionProvider, useSession } from "@/components/session";
import { ThemeToggle } from "@/components/theme-toggle";
import { Badge, cx, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import type { Overview } from "@/lib/types";

function Shell({ children }: { children: ReactNode }) {
  const { ws } = useParams<{ ws: string }>();
  const pathname = usePathname();
  const router = useRouter();
  const { user, workspaces, logout } = useSession();
  const current = workspaces.find((w) => w.id === ws);
  const [alerts, setAlerts] = useState(0);

  // Open alerts drive the badge on Monitors; refresh on navigation and every minute.
  useEffect(() => {
    if (!current) return;
    const load = () => api.get<Overview>(`/workspaces/${current.id}/overview`).then((o) => setAlerts(o.open_alerts), () => {});
    load();
    const timer = setInterval(load, 60_000);
    return () => clearInterval(timer);
  }, [current, pathname]);

  useEffect(() => {
    if (!current) router.replace("/");
    else
      try {
        localStorage.setItem("inferra:workspace", current.id);
      } catch {}
  }, [current, router]);

  if (!current) return null;
  const base = `/w/${ws}`;
  const nav = [
    { href: base, label: "Ask", icon: Sparkles, active: pathname === base },
    { href: `${base}/runs`, label: "Runs", icon: History, active: pathname.startsWith(`${base}/runs`) },
    { href: `${base}/insights`, label: "Insights", icon: Lightbulb, active: pathname.startsWith(`${base}/insights`) },
    { href: `${base}/reports`, label: "Reports", icon: FileText, active: pathname.startsWith(`${base}/reports`) },
    { href: `${base}/monitors`, label: "Monitors", icon: BellRing, active: pathname.startsWith(`${base}/monitors`), count: alerts },
    { href: `${base}/metrics`, label: "Metrics", icon: Gauge, active: pathname.startsWith(`${base}/metrics`) },
    { href: `${base}/datasets`, label: "Datasets", icon: Database, active: pathname.startsWith(`${base}/datasets`) },
    { href: `${base}/data-models`, label: "Data models", icon: Network, active: pathname.startsWith(`${base}/data-models`) },
    { href: `${base}/models`, label: "ML models", icon: Brain, active: pathname.startsWith(`${base}/models`) },
  ];

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <aside className="flex shrink-0 flex-col gap-4 border-b border-line bg-surface px-3 py-3 md:sticky md:top-0 md:h-screen md:w-60 md:border-r md:border-b-0 md:py-5">
        <div className="flex items-center justify-between px-2">
          <Link href={base}>
            <Logo />
          </Link>
          <ThemeToggle />
        </div>
        <select
          value={current.id}
          onChange={(e) => router.push(`/w/${e.target.value}`)}
          className="h-9 rounded-lg border border-line bg-surface px-2 text-sm"
          aria-label="Workspace"
        >
          {workspaces.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
            </option>
          ))}
        </select>
        <nav className="flex gap-1 overflow-x-auto md:flex-col">
          {nav.map(({ href, label, icon: Icon, active, count }) => (
            <Link
              key={href}
              href={href}
              className={cx(
                "flex shrink-0 items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium whitespace-nowrap",
                active ? "bg-accent-soft text-accent" : "text-ink-2 hover:bg-surface-2 hover:text-ink",
              )}
            >
              <Icon className="size-4" aria-hidden />
              {label}
              {count ? (
                <span className="tabular ml-auto rounded-full bg-bad px-1.5 text-[11px] leading-[18px] font-semibold text-bg" aria-label={`${count} open alerts`}>
                  {count}
                </span>
              ) : null}
            </Link>
          ))}
        </nav>
        <div className="mt-auto hidden space-y-3 px-2 md:block">
          {current.role === "viewer" && <Badge tone="warn">Read-only workspace</Badge>}
          <div className="flex items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              {user.avatar_url ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={user.avatar_url} alt="" className="size-7 rounded-full" />
              ) : (
                <span className="inline-flex size-7 items-center justify-center rounded-full bg-surface-2 text-xs font-semibold">
                  {user.login.slice(0, 1).toUpperCase()}
                </span>
              )}
              <span className="truncate text-sm">{user.name ?? user.login}</span>
            </div>
            <button onClick={() => void logout()} className="rounded-lg p-2 text-ink-3 hover:bg-surface-2 hover:text-ink" aria-label="Sign out">
              <LogOut className="size-4" />
            </button>
          </div>
        </div>
      </aside>
      <main className="min-w-0 flex-1 px-4 py-6 md:px-10 md:py-8">{children}</main>
    </div>
  );
}

export default function WorkspaceLayout({ children }: { children: ReactNode }) {
  return (
    <SessionProvider
      fallback={
        <div className="flex min-h-screen items-center justify-center">
          <Spinner />
        </div>
      }
    >
      <Shell>{children}</Shell>
    </SessionProvider>
  );
}
