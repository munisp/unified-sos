import { useEffect, useMemo, useState } from 'react';
import { listServices } from '../lib/api';
import { formatNaira } from '../lib/format';
import { useAppStore } from '../lib/store';
import { Link } from '../lib/router';
import { CategoryIcon } from '../components/CategoryIcon';
import { UssdNote } from '../components/UssdNote';
import type { ServiceCatalogEntry, ServiceCategory, StateId } from '../lib/types';

const CATEGORY_LABELS: Record<ServiceCategory, string> = {
  REVENUE: 'Revenue & tax',
  LANDS: 'Land & titles',
  MINING: 'Mining',
  AGRICULTURE: 'Agriculture',
  TRANSPORT: 'Transport',
  MARKET: 'Markets',
  HEALTH: 'Health',
  EDUCATION: 'Education',
  ENVIRONMENT: 'Environment',
  FORESTRY: 'Forestry',
  INVESTMENT: 'Investment & PPP',
};

export const CATEGORY_ORDER: ServiceCategory[] = [
  'REVENUE', 'LANDS', 'MINING', 'AGRICULTURE', 'TRANSPORT', 'MARKET',
  'HEALTH', 'EDUCATION', 'ENVIRONMENT', 'FORESTRY', 'INVESTMENT',
];

export function Home({ stateId }: { stateId: StateId }) {
  const [services, setServices] = useState<ServiceCatalogEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [category, setCategory] = useState<ServiceCategory | null>(null);

  useEffect(() => {
    let live = true;
    listServices(stateId)
      .then(({ data }) => live && setServices(data.filter((s) => s.active)))
      .catch(() => live && setError('Could not load the service catalog.'));
    return () => {
      live = false;
    };
  }, [stateId]);

  const grouped = useMemo(() => {
    const map = new Map<ServiceCategory, ServiceCatalogEntry[]>();
    for (const s of services ?? []) {
      const list = map.get(s.category) ?? [];
      list.push(s);
      map.set(s.category, list);
    }
    return map;
  }, [services]);

  if (error) return <p className="notice notice-bad" role="alert">{error}</p>;
  if (!services) return <p role="status">Loading services…</p>;

  if (category) {
    const list = grouped.get(category) ?? [];
    return (
      <section aria-labelledby="cat-heading">
        <button onClick={() => setCategory(null)} aria-label="Back to categories">← All categories</button>
        <h1 id="cat-heading">{CATEGORY_LABELS[category]}</h1>
        <ul style={{ listStyle: 'none', padding: 0, margin: 0 }} className="stack">
          {list.map((s) => (
            <li key={s.service_code} className="card">
              <div className="row">
                <div>
                  <strong>{s.name}</strong>
                  <div className="small muted">{s.mda} · SLA {s.sla_days} days</div>
                  <div className="small">
                    Fee: <span className="fee">{s.base_fee_kobo ? formatNaira(s.base_fee_kobo) : 'Free'}</span>
                    {s.expedited_fee_kobo > 0 && (
                      <span className="muted"> · expedited {formatNaira(s.expedited_fee_kobo)}</span>
                    )}
                  </div>
                </div>
                <Link className="btn-primary" style={{ borderRadius: 12, padding: '0.6rem 0.9rem', textDecoration: 'none' }} to={`/request/${s.service_code}`}>
                  Apply
                </Link>
              </div>
            </li>
          ))}
          {list.length === 0 && <p className="muted">No services in this category yet.</p>}
        </ul>
        <UssdNote stateId={stateId} />
      </section>
    );
  }

  return (
    <section aria-labelledby="catalog-heading">
      <h1 id="catalog-heading">What do you need today?</h1>
      <p className="muted">Browse state services by category.</p>
      <div className="grid">
        {CATEGORY_ORDER.map((cat) => {
          const count = grouped.get(cat)?.length ?? 0;
          return (
            <button key={cat} className="cat-tile" onClick={() => setCategory(cat)}>
              <CategoryIcon category={cat} />
              <span style={{ fontWeight: 600 }}>{CATEGORY_LABELS[cat]}</span>
              <span className="small muted">{count} service{count === 1 ? '' : 's'}</span>
            </button>
          );
        })}
      </div>
      <UssdNote stateId={stateId} />
    </section>
  );
}
