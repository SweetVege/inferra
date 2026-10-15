"use client";

import { ArrowRight, ShieldCheck, Sparkles, Workflow } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, type FormEvent } from "react";

import { Logo } from "@/components/logo";
import { Button, ErrorBanner } from "@/components/ui";
import { api } from "@/lib/api";
import type { Providers } from "@/lib/types";

const POINTS = [
  { icon: Sparkles, title: "Ask in plain English", text: "Describe the question. The Orchestrator turns it into a plan you approve." },
  { icon: Workflow, title: "A team of agents", text: "Data, Analyst, and Insight agents run real queries and statistics on your data." },
  { icon: ShieldCheck, title: "Verified, then reviewed", text: "Every number is checked against tool output before you accept an insight." },
];

function GitHubMark() {
  return (
    <svg viewBox="0 0 16 16" className="size-4" fill="currentColor" aria-hidden>
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
    </svg>
  );
}

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [providers, setProviders] = useState<Providers | null>(null);
  const [login, setLogin] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(params.get("error") ? "Sign-in failed. Please try again." : null);

  useEffect(() => {
    api.get<Providers>("/auth/providers").then(setProviders, () => setError("The Inferra API is not reachable."));
  }, []);

  async function run(kind: string, action: () => Promise<unknown>) {
    setBusy(kind);
    setError(null);
    try {
      await action();
      router.replace("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed.");
      setBusy(null);
    }
  }

  function devLogin(e: FormEvent) {
    e.preventDefault();
    void run("dev", () => api.post("/auth/dev-login", { login }));
  }

  return (
    <div className="w-full max-w-sm space-y-4">
      {error && <ErrorBanner message={error} />}
      {providers?.github && (
        <a
          href="/api/auth/github/login"
          className="flex h-10 w-full items-center justify-center gap-2 rounded-lg bg-ink text-sm font-medium text-bg hover:opacity-90"
        >
          <GitHubMark /> Continue with GitHub
        </a>
      )}
      {providers?.dev_login && (
        <form onSubmit={devLogin} className="space-y-2">
          <label htmlFor="login" className="text-sm font-medium">
            Local development sign-in
          </label>
          <div className="flex gap-2">
            <input
              id="login"
              value={login}
              onChange={(e) => setLogin(e.target.value)}
              placeholder="your-name"
              pattern="[A-Za-z0-9\-]{2,40}"
              required
              className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 text-sm outline-none focus:border-accent"
            />
            <Button variant="primary" type="submit" loading={busy === "dev"}>
              Sign in
            </Button>
          </div>
        </form>
      )}
      <div className="flex items-center gap-3 text-xs text-ink-3">
        <span className="h-px flex-1 bg-line" /> or <span className="h-px flex-1 bg-line" />
      </div>
      <Button className="w-full" loading={busy === "demo"} onClick={() => void run("demo", () => api.post("/auth/demo"))}>
        Explore the demo workspace <ArrowRight className="size-4" aria-hidden />
      </Button>
      <p className="text-center text-xs text-ink-3">The demo is read-only and shows a completed analysis.</p>
    </div>
  );
}

export default function LoginPage() {
  return (
    <main className="grid min-h-screen lg:grid-cols-2">
      <section className="hidden flex-col justify-between border-r border-line bg-surface p-12 lg:flex">
        <Logo className="text-lg" />
        <div className="max-w-md space-y-8">
          <h1 className="text-3xl font-semibold leading-tight tracking-tight">
            Your AI data science team, with the receipts.
          </h1>
          <ul className="space-y-5">
            {POINTS.map(({ icon: Icon, title, text }) => (
              <li key={title} className="flex gap-3">
                <span className="mt-0.5 inline-flex size-8 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent">
                  <Icon className="size-4" aria-hidden />
                </span>
                <div>
                  <p className="font-medium">{title}</p>
                  <p className="text-sm text-ink-2">{text}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
        <p className="text-xs text-ink-3">Language models reason. Tools compute. People decide.</p>
      </section>
      <section className="flex flex-col items-center justify-center gap-8 px-4 py-12">
        <div className="space-y-2 text-center">
          <Logo className="text-lg lg:hidden" />
          <h2 className="text-xl font-semibold">Sign in to Inferra</h2>
        </div>
        <Suspense>
          <LoginForm />
        </Suspense>
      </section>
    </main>
  );
}
