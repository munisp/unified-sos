import { describe, expect, it } from 'vitest';
import { enqueue, flushQueue, listQueue, markAttempt, removeFromQueue } from '../lib/offlineQueue';

function memStorage(): Storage {
  const map = new Map<string, string>();
  return {
    getItem: (k) => map.get(k) ?? null,
    setItem: (k, v) => void map.set(k, String(v)),
    removeItem: (k) => void map.delete(k),
    clear: () => map.clear(),
    key: (i) => [...map.keys()][i] ?? null,
    get length() { return map.size; },
  };
}

describe('offline queue', () => {
  it('enqueues and lists submissions FIFO', () => {
    const s = memStorage();
    let n = 0;
    const id = () => `id-${++n}`;
    enqueue({ url: '/a', method: 'POST', body: { x: 1 } }, s, id);
    enqueue({ url: '/b', method: 'POST', body: { y: 2 } }, s, id);
    const q = listQueue(s);
    expect(q.map((i) => i.queue_id)).toEqual(['id-1', 'id-2']);
    expect(q[0].attempts).toBe(0);
    expect(q[0].enqueued_at).toBeTruthy();
  });

  it('removes entries and tracks attempts with last error', () => {
    const s = memStorage();
    const e = enqueue({ url: '/a', method: 'POST', body: {} }, s, () => 'id-1');
    markAttempt(e.queue_id, 'offline', s);
    expect(listQueue(s)[0].attempts).toBe(1);
    expect(listQueue(s)[0].last_error).toBe('offline');
    removeFromQueue(e.queue_id, s);
    expect(listQueue(s)).toEqual([]);
  });

  it('flushes successfully sent entries and keeps failures for retry', async () => {
    const s = memStorage();
    let n = 0;
    const id = () => `id-${++n}`;
    enqueue({ url: '/ok', method: 'POST', body: {} }, s, id);
    enqueue({ url: '/fail', method: 'POST', body: {} }, s, id);
    const result = await flushQueue(async (item) => {
      if (item.url === '/fail') throw new Error('network down');
    }, s);
    expect(result.sent).toEqual(['id-1']);
    expect(result.failed).toEqual(['id-2']);
    const remaining = listQueue(s);
    expect(remaining).toHaveLength(1);
    expect(remaining[0].url).toBe('/fail');
    expect(remaining[0].attempts).toBe(1);
  });

  it('survives corrupt storage gracefully', () => {
    const s = memStorage();
    s.setItem('sos-citizen:offline-queue', '{not json');
    expect(listQueue(s)).toEqual([]);
  });
});
