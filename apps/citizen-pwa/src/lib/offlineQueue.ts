// Offline request queue (localStorage-backed, deterministic ordering).
// The service worker (src/sw.ts) mirrors this for background replay of
// POSTs made while the page was closed; this queue drives in-app UX.

export interface QueuedSubmission {
  queue_id: string;
  url: string;
  method: string;
  body: unknown;
  enqueued_at: string;
  attempts: number;
  last_error?: string;
}

const STORAGE_KEY = 'sos-citizen:offline-queue';

function readAll(storage: Storage = window.localStorage): QueuedSubmission[] {
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as QueuedSubmission[]) : [];
  } catch {
    return [];
  }
}

function writeAll(items: QueuedSubmission[], storage: Storage = window.localStorage): void {
  storage.setItem(STORAGE_KEY, JSON.stringify(items));
}

export function enqueue(
  item: Omit<QueuedSubmission, 'queue_id' | 'enqueued_at' | 'attempts'>,
  storage: Storage = window.localStorage,
  idGen: () => string = defaultId,
): QueuedSubmission {
  const entry: QueuedSubmission = {
    ...item,
    queue_id: idGen(),
    enqueued_at: new Date().toISOString(),
    attempts: 0,
  };
  const all = readAll(storage);
  all.push(entry);
  writeAll(all, storage);
  return entry;
}

export function listQueue(storage: Storage = window.localStorage): QueuedSubmission[] {
  return readAll(storage);
}

export function removeFromQueue(queueId: string, storage: Storage = window.localStorage): void {
  writeAll(readAll(storage).filter((q) => q.queue_id !== queueId), storage);
}

export function markAttempt(
  queueId: string,
  error: string,
  storage: Storage = window.localStorage,
): void {
  writeAll(
    readAll(storage).map((q) =>
      q.queue_id === queueId ? { ...q, attempts: q.attempts + 1, last_error: error } : q,
    ),
    storage,
  );
}

/** Flush the queue FIFO against a sender; returns per-entry outcomes. */
export async function flushQueue(
  send: (item: QueuedSubmission) => Promise<void>,
  storage: Storage = window.localStorage,
): Promise<{ sent: string[]; failed: string[] }> {
  const sent: string[] = [];
  const failed: string[] = [];
  for (const item of readAll(storage)) {
    try {
      await send(item);
      removeFromQueue(item.queue_id, storage);
      sent.push(item.queue_id);
    } catch (err) {
      markAttempt(item.queue_id, err instanceof Error ? err.message : String(err), storage);
      failed.push(item.queue_id);
    }
  }
  return { sent, failed };
}

let counter = 0;
function defaultId(): string {
  counter += 1;
  return `q-${Date.now()}-${counter}`;
}
