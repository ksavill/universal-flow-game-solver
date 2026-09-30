import { LOCAL_VERSION } from "../core/localPuzzle";

export type SavedLocalPuzzle = { id: string; title: string; text: string; colors: Record<string, string>; createdAt: number };
export type FeedbackReport = {
  id: string; createdAt: number; version: string; description: string;
  stage: string; outcome: string; error?: string; puzzleText: string;
  puzzleHash: string; diagnostics: Record<string, unknown>; attachment?: { mime: "image/jpeg"; dataUrl: string };
};

export const REPORT_POLICY = { maxReports: 50, maxBytes: 5 * 1024 * 1024, maxReportBytes: 256 * 1024, ttlMs: 30 * 24 * 60 * 60 * 1000 };
const bytes = (value: unknown) => new TextEncoder().encode(JSON.stringify(value)).length;

export function retainReports(reports: FeedbackReport[], now: number): FeedbackReport[] {
  const ordered = reports.filter(report => report.createdAt > now - REPORT_POLICY.ttlMs).sort((a, b) => b.createdAt - a.createdAt);
  let total = 0;
  return ordered.filter((report, index) => {
    const size = bytes(report);
    if (index >= REPORT_POLICY.maxReports || size > REPORT_POLICY.maxReportBytes || total + size > REPORT_POLICY.maxBytes) return false;
    total += size; return true;
  });
}

function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("flow-solver-local-v1", 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore("puzzles", { keyPath: "id" });
      request.result.createObjectStore("reports", { keyPath: "id" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new Error("Local storage is unavailable. Use the download buttons instead."));
    request.onblocked = () => reject(new Error("Close other local prototype tabs and retry storage."));
  });
}

async function transaction<T>(store: "puzzles" | "reports", action: (target: IDBObjectStore, result: (value: T) => void) => void): Promise<T> {
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(store, "readwrite");
    let value: T;
    tx.oncomplete = () => { db.close(); resolve(value); };
    tx.onerror = tx.onabort = () => { db.close(); reject(new Error("Could not save locally. Browser storage may be full; download a copy instead.")); };
    action(tx.objectStore(store), result => value = result);
  });
}

export async function listLocalPuzzles(): Promise<SavedLocalPuzzle[]> {
  return transaction("puzzles", (store, done) => { store.getAll().onsuccess = event => done((event.target as IDBRequest<SavedLocalPuzzle[]>).result.sort((a, b) => b.createdAt - a.createdAt)); });
}

export async function saveLocalPuzzle(puzzle: SavedLocalPuzzle): Promise<void> {
  return transaction("puzzles", (store, done) => {
    store.getAll().onsuccess = event => {
      const values = (event.target as IDBRequest<SavedLocalPuzzle[]>).result;
      if (values.length >= 100 && !values.some(value => value.id === puzzle.id)) { store.transaction.abort(); return; }
      store.put(puzzle); done(undefined);
    };
  });
}

export async function deleteLocalPuzzle(id: string): Promise<void> {
  return transaction("puzzles", (store, done) => { store.delete(id); done(undefined); });
}

export async function listLocalReports(): Promise<FeedbackReport[]> {
  return transaction("reports", (store, done) => {
    store.getAll().onsuccess = event => {
      const all = (event.target as IDBRequest<FeedbackReport[]>).result;
      const retained = retainReports(all, Date.now());
      const ids = new Set(retained.map(report => report.id));
      all.filter(report => !ids.has(report.id)).forEach(report => store.delete(report.id));
      done(retained);
    };
  });
}

export async function saveLocalReport(report: FeedbackReport): Promise<void> {
  if (bytes(report) > REPORT_POLICY.maxReportBytes) throw new Error("Report exceeds 256 KB. Turn off the image attachment or shorten the description.");
  return transaction("reports", (store, done) => {
    store.getAll().onsuccess = event => {
      const all = (event.target as IDBRequest<FeedbackReport[]>).result;
      const retained = retainReports([...all.filter(value => value.id !== report.id), report], Date.now());
      const ids = new Set(retained.map(value => value.id));
      all.filter(value => !ids.has(value.id)).forEach(value => store.delete(value.id));
      store.put(report); done(undefined);
    };
  });
}

export async function deleteLocalReport(id: string): Promise<void> {
  return transaction("reports", (store, done) => { store.delete(id); done(undefined); });
}

export async function makeFeedbackReport(input: Omit<FeedbackReport, "id" | "createdAt" | "version" | "puzzleHash">): Promise<FeedbackReport> {
  const description = input.description.trim();
  if (!description || description.length > 2000) throw new Error("Describe the problem in 1–2,000 characters.");
  const canonical = input.puzzleText.split(/\r?\n/).map(line => line.trim()).filter(line => line && (!line.startsWith("#") || /^#\s*(type|fill):/.test(line) || /^[.#A-Z]+$/.test(line))).join("\n");
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical));
  return { ...input, description, id: crypto.randomUUID(), createdAt: Date.now(), version: LOCAL_VERSION, puzzleHash: [...new Uint8Array(hash)].map(value => value.toString(16).padStart(2, "0")).join("") };
}
