"use client";

import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

export function ThemeToggle() {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    let saved: string | null = null;
    try {
      saved = localStorage.getItem("inferra:theme");
    } catch {}
    const isDark = saved ? saved === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    setDark(isDark);
    if (saved) document.documentElement.dataset.theme = saved;
  }, []);
  function toggle() {
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("inferra:theme", next);
    } catch {}
    setDark(!dark);
  }
  return (
    <button onClick={toggle} className="rounded-lg p-2 text-ink-3 hover:bg-surface-2 hover:text-ink" aria-label="Toggle theme">
      {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </button>
  );
}
