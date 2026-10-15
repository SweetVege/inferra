export function Logo({ className }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 font-semibold tracking-tight ${className ?? ""}`}>
      <svg viewBox="0 0 24 24" className="size-6" aria-hidden>
        <rect width="24" height="24" rx="6" fill="var(--accent)" />
        <path d="M6 16.5 10 11l3 3 5-7" fill="none" stroke="var(--accent-text)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="18" cy="7" r="1.6" fill="var(--accent-text)" />
      </svg>
      Inferra
    </span>
  );
}
