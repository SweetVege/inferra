"use client";

import { FileX } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Logo } from "@/components/logo";
import { ReportView } from "@/components/report-view";
import { ThemeToggle } from "@/components/theme-toggle";
import { EmptyState, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import type { ReportContent } from "@/lib/types";

/** A shared report: readable by anyone with the link, no account needed. */
export default function SharedReportPage() {
  const { token } = useParams<{ token: string }>();
  const [report, setReport] = useState<ReportContent | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<ReportContent>(`/public/reports/${token}`).then(
      (r) => {
        setReport(r);
        document.title = `${r.title} · Inferra`;
      },
      (e) => setError(e.message),
    );
  }, [token]);

  return (
    <div className="min-h-screen">
      <header className="border-b border-line bg-surface print:hidden">
        <div className="mx-auto flex max-w-4xl items-center justify-between gap-3 px-4 py-3">
          <Link href="/login" aria-label="Inferra">
            <Logo />
          </Link>
          <div className="flex items-center gap-2">
            <span className="hidden text-xs text-ink-3 sm:inline">Shared read-only report</span>
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-4xl px-4 py-8 md:py-12">
        {error && (
          <EmptyState icon={FileX} title="Report not available">
            {error}
          </EmptyState>
        )}
        {!report && !error && <Spinner />}
        {report && <ReportView content={report} />}
        <p className="mt-10 text-center text-xs text-ink-3 print:hidden">
          Made with Inferra, an AI data science workbench.{" "}
          <Link href="/login" className="text-accent hover:underline">
            Explore the demo
          </Link>
        </p>
      </main>
    </div>
  );
}
