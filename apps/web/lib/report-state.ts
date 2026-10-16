export const REPORT_STATE = {
  draft: { label: "Draft", tone: "neutral" },
  published: { label: "Published", tone: "good" },
  changed: { label: "Unpublished changes", tone: "warn" },
} as const;
