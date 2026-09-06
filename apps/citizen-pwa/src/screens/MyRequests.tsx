import { useCallback, useEffect, useState } from 'react';
import { flushQueue, listQueue } from '../lib/offlineQueue';
import { getServiceRequest } from '../lib/api';
import { formatDate, formatNaira } from '../lib/format';
import { useAppStore } from '../lib/store';
import { Link } from '../lib/router';
import { UssdNote } from '../components/UssdNote';
import type { StateId } from '../lib/types';

const STATUS_BADGE: Record<string, string> = {
  SUBMITTED: 'badge-neutral',
  IN_REVIEW: 'badge-warn',
  APPROVED: 'badge-ok',
  COMPLETED: 'badge-ok',
  REJECTED: 'badge-bad',
};

export function MyRequests({ stateId }: { stateId: StateId }) {
  const { requests, updateRequest } = useAppStore();
  const [pending, setPending] = useState(listQueue().length);
  const [syncing, setSyncing] = useState(false);

  const refreshPending = useCallback(() => setPending(listQueue().length), []);

  const sync = useCallback(async () => {
    setSyncing(true);
    try {
      await flushQueue(async (item) => {
        const res = await fetch(`${(import.meta.env.VITE_API_BASE_URL as string) ?? ''}${item.url}`, {
          method: item.method,
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(item.body),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
      });
      // Refresh server-side status for tracked requests.
      for (const r of requests) {
        if (r.request_id.startsWith('LOCAL-')) continue;
        try {
          const fresh = await getServiceRequest(stateId, r.request_id);
          updateRequest(fresh);
        } catch {
          /* keep local copy */
        }
      }
    } finally {
      refreshPending();
      setSyncing(false);
    }
  }, [requests, stateId, updateRequest, refreshPending]);

  useEffect(() => {
    const onOnline = () => void sync();
    window.addEventListener('online', onOnline);
    return () => window.removeEventListener('online', onOnline);
  }, [sync]);

  return (
    <section aria-labelledby="req-heading">
      <h1 id="req-heading">My requests</h1>
      {pending > 0 && (
        <p className="notice notice-warn" role="status">
          {pending} submission{pending === 1 ? '' : 's'} waiting to sync.
          <button style={{ marginLeft: '0.6rem' }} onClick={() => void sync()} disabled={syncing}>
            {syncing ? 'Syncing…' : 'Sync now'}
          </button>
        </p>
      )}
      {requests.length === 0 ? (
        <p className="muted">
          Nothing yet. <Link to="/">Browse services</Link> to make your first request.
        </p>
      ) : (
        <ul style={{ listStyle: 'none', padding: 0 }} className="stack">
          {requests.map((r) => (
            <li key={r.request_id} className="card">
              <div className="row">
                <strong>{r.service_code}</strong>
                <span className={`badge ${STATUS_BADGE[r.status] ?? 'badge-neutral'}`}>{r.status}</span>
              </div>
              <div className="small muted">
                <span className="hash">{r.request_id}</span> · {formatDate(r.created_at)}
                {r.fee_kobo > 0 && <> · {formatNaira(r.fee_kobo)}</>}
              </div>
              {r.timeline.length > 0 && (
                <ol className="timeline" aria-label="Status timeline">
                  {r.timeline.map((ev, i) => (
                    <li key={i}>
                      <strong className="small">{ev.status}</strong>{' '}
                      <span className="small muted">{ev.at ? formatDate(ev.at) : ''} {ev.note}</span>
                    </li>
                  ))}
                </ol>
              )}
            </li>
          ))}
        </ul>
      )}
      <UssdNote stateId={stateId} />
    </section>
  );
}
