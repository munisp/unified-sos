import { sloVerdict } from '../lib/slo';
import { useConsole } from '../lib/store';
import { DISPATCH_SLO_S } from '../lib/types';

export function SloBanner({ now }: { now: number }) {
  const { incidents } = useConsole();
  const verdict = sloVerdict(incidents, now);
  if (verdict.sampleSize === 0) return null;
  return (
    <div
      role="status"
      className={`slo-banner ${verdict.breached ? 'slo-breached' : 'slo-ok'}`}
    >
      {verdict.breached ? '⚠ SLO breach' : 'SLO on track'} — p95 dispatch latency{' '}
      <strong>{verdict.p95Seconds}s</strong> vs {DISPATCH_SLO_S}s target (
      {verdict.sampleSize} open incidents)
    </div>
  );
}
