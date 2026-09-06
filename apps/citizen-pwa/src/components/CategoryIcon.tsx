// Simple stroke icons per service category domain.
import type { ServiceCategory } from '../lib/types';

const PATHS: Record<ServiceCategory, string> = {
  REVENUE: 'M4 20h16M6 16V9m4 7V5m4 11v-8m4 8V7',
  LANDS: 'M3 20l6-14 5 8 3-4 4 10H3z',
  MINING: 'M12 3l7 7-7 11-7-11 7-7zM5 10h14',
  AGRICULTURE: 'M12 21V9m0 0C12 5 9 3 5 3c0 4 3 6 7 6zm0 0c0-4 3-6 7-6 0 4-3 6-7 6z',
  TRANSPORT: 'M5 17V7a2 2 0 012-2h10a2 2 0 012 2v10m-14 0h14m-14 0v2m14-2v2M8 10h8',
  MARKET: 'M4 9l1-4h14l1 4M4 9h16v11H4V9zm6 4h4',
  HEALTH: 'M12 4v16M4 12h16',
  EDUCATION: 'M3 9l9-4 9 4-9 4-9-4zm4 2.5V16c0 1.5 2.5 3 5 3s5-1.5 5-3v-4.5',
  ENVIRONMENT: 'M12 21c-5 0-8-3.5-8-8 0-6 5-9 8-10 3 1 8 4 8 10 0 4.5-3 8-8 8zm0-14v10',
  FORESTRY: 'M12 3L6 11h3l-4 6h5v4h4v-4h5l-4-6h3l-6-8z',
  INVESTMENT: 'M4 20V10m5 10V4m5 16v-9m5 9V8',
};

export function CategoryIcon({ category, className }: { category: ServiceCategory; className?: string }) {
  return (
    <svg
      className={className ?? 'cat-icon'}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[category]} />
    </svg>
  );
}
