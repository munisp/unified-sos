import { STATES, type StateId } from '../lib/types';
import { useAppStore } from '../lib/store';
import { t } from '../lib/i18n';

export function StateSelector() {
  const { selectState } = useAppStore();
  return (
    <main aria-labelledby="state-heading">
      <h1 id="state-heading">{t('chooseState')}</h1>
      <p className="muted">
        SOS Citizen works in six states. Pick yours to see its services, payments and public
        transparency feeds.
      </p>
      <div className="state-grid" role="list">
        {STATES.map((s) => (
          <button
            key={s.id}
            role="listitem"
            className="state-tile"
            onClick={() => selectState(s.id as StateId)}
          >
            {s.name}
            <span className="small muted" style={{ display: 'block', fontWeight: 400 }}>
              USSD {s.ussd}
            </span>
          </button>
        ))}
      </div>
    </main>
  );
}
