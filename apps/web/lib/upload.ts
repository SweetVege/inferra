import { api } from "@/lib/api";
import type { Dataset } from "@/lib/types";

interface Ticket {
  mode: "direct" | "form";
  upload_id: string | null;
  url: string | null;
}

const FALLBACK_LIMIT = 4 * 1024 * 1024;

function put(url: string, file: File, onProgress: (fraction: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => (xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error(`Storage returned ${xhr.status}`)));
    xhr.onerror = () => reject(new Error("network"));
    xhr.send(file);
  });
}

/** Upload a data file. With object storage the browser sends it straight to the bucket (large files
 * never pass through the web proxy); otherwise, or if the bucket refuses the browser (CORS not set
 * up), it falls back to a regular multipart upload. */
export async function uploadDataset(ws: string, file: File, onProgress: (fraction: number) => void = () => {}): Promise<Dataset> {
  const ticket = await api.post<Ticket>(`/workspaces/${ws}/datasets/uploads`, { filename: file.name, size: file.size });
  if (ticket.mode === "direct" && ticket.url && ticket.upload_id) {
    let sent = false;
    try {
      await put(ticket.url, file, onProgress);
      sent = true;
    } catch {
      // Fall through to the multipart upload below.
    }
    if (sent) return api.post<Dataset>(`/workspaces/${ws}/datasets/uploads/${ticket.upload_id}/complete`, { filename: file.name });
    // Hosting proxies cap request bodies (a few MB on some platforms), so only small files can fall back.
    if (file.size > FALLBACK_LIMIT) {
      throw new Error(
        "The upload to storage was refused. Large files need the storage bucket's CORS policy to allow this site (see docs/DEPLOY.md); files under 4 MB upload without it.",
      );
    }
  }
  const form = new FormData();
  form.append("file", file);
  return api.post<Dataset>(`/workspaces/${ws}/datasets`, form);
}
