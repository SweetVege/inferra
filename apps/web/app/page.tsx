"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { Spinner } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { Workspace } from "@/lib/types";

export default function Home() {
  const router = useRouter();
  useEffect(() => {
    api.get<Workspace[]>("/workspaces").then(
      (workspaces) => {
        let saved: string | null = null;
        try {
          saved = localStorage.getItem("inferra:workspace");
        } catch {}
        const target = workspaces.find((w) => w.id === saved) ?? workspaces[0];
        router.replace(target ? `/w/${target.id}` : "/login");
      },
      (err) => {
        if (err instanceof ApiError && err.status === 401) router.replace("/login");
      },
    );
  }, [router]);
  return (
    <main className="flex min-h-screen items-center justify-center">
      <Spinner />
    </main>
  );
}
