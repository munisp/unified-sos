import { isPlaceholderLogo, monogram, useBranding } from '../lib/branding';
import { STATES, type StateId } from '../lib/types';
import { useConsole } from '../lib/store';

export function Header() {
  const { stateId, setStateId, demoMode } = useConsole();
  const brandingResult = useBranding(stateId);
  const branding = brandingResult?.branding;
  return (
    <header className="app-header">
      <div className="brand">
        {branding && !isPlaceholderLogo(branding) ? (
          <img className="brand-logo" src={branding.logo_url} alt="" width={32} height={32} />
        ) : (
          <span className="brand-monogram" aria-hidden="true" data-testid="brand-monogram">
            {branding ? monogram(branding.display_name) : 'SO'}
          </span>
        )}
        <div>
          <h1>{branding ? branding.portal_title : 'Dispatch Console'}</h1>
          <p className="tagline">{branding ? branding.tagline : '112 emergency CAD — Safe City operations'}</p>
          <p className="attribution">Powered by SOS</p>
        </div>
      </div>
      <div className="header-controls">
        {demoMode && <span className="badge badge-warn">demo mode</span>}
        <label className="tenant-picker">
          <span className="sr-only">State tenant</span>
          <select
            aria-label="State tenant"
            value={stateId}
            onChange={(e) => setStateId(e.target.value as StateId)}
          >
            {STATES.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
      </div>
    </header>
  );
}
