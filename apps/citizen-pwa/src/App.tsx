import { useEffect, useState, type ReactNode } from 'react';
import { isDemoMode, onDemoModeChange } from './lib/api';
import { isPlaceholderLogo, monogram, useBranding } from './lib/branding';
import { useHashRoute, Link } from './lib/router';
import { useAppStore } from './lib/store';
import { STATES } from './lib/types';
import { StateSelector } from './screens/StateSelector';
import { Home } from './screens/Home';
import { ServiceRequestForm } from './screens/ServiceRequestForm';
import { MyRequests } from './screens/MyRequests';
import { Payments } from './screens/Payments';
import { Transparency } from './screens/Transparency';
import { VerifyDeed } from './screens/VerifyDeed';
import { Profile } from './screens/Profile';

const NAV = [
  { to: '/', label: 'Services', match: /^\/($|request|services)/ },
  { to: '/requests', label: 'Requests', match: /^\/requests/ },
  { to: '/pay', label: 'Pay', match: /^\/pay/ },
  { to: '/transparency', label: 'Transparency', match: /^\/transparency/ },
  { to: '/verify-deed', label: 'Verify', match: /^\/verify-deed/ },
  { to: '/profile', label: 'Profile', match: /^\/profile/ },
];

export function App() {
  const route = useHashRoute();
  const { stateId, applyDefaultLocale } = useAppStore();
  const brandingResult = useBranding(stateId);
  const [demo, setDemo] = useState(isDemoMode());
  const [online, setOnline] = useState(() => (typeof navigator === 'undefined' ? true : navigator.onLine));

  useEffect(() => onDemoModeChange(setDemo), []);
  // Honor the tenant's default locale until the user picks a language.
  useEffect(() => {
    if (brandingResult) applyDefaultLocale(brandingResult.branding.default_locale);
  }, [brandingResult, applyDefaultLocale]);
  useEffect(() => {
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener('online', on);
    window.addEventListener('offline', off);
    return () => {
      window.removeEventListener('online', on);
      window.removeEventListener('offline', off);
    };
  }, []);

  if (!stateId) return <StateSelector />;

  const state = STATES.find((s) => s.id === stateId);
  const requestMatch = route.match(/^\/request\/([A-Z0-9-]+)/);

  let screen: ReactNode;
  if (requestMatch) screen = <ServiceRequestForm stateId={stateId} serviceCode={requestMatch[1]} />;
  else if (route.startsWith('/requests')) screen = <MyRequests stateId={stateId} />;
  else if (route.startsWith('/pay')) screen = <Payments stateId={stateId} />;
  else if (route.startsWith('/transparency')) screen = <Transparency stateId={stateId} />;
  else if (route.startsWith('/verify-deed')) screen = <VerifyDeed stateId={stateId} />;
  else if (route.startsWith('/profile')) screen = <Profile stateId={stateId} />;
  else screen = <Home stateId={stateId} />;

  return (
    <div className="app-shell">
      <a href="#main" className="sr-only">Skip to content</a>
      <header className="app-header">
        <span className="brand">
          {brandingResult && !isPlaceholderLogo(brandingResult.branding) ? (
            <img src={brandingResult.branding.logo_url} alt="" width={28} height={28} />
          ) : (
            <span className="brand-monogram" aria-hidden="true" data-testid="brand-monogram">
              {brandingResult ? monogram(brandingResult.branding.display_name) : 'SOS'}
            </span>
          )}
          <span className="brand-text">
            {brandingResult ? brandingResult.branding.portal_title : 'SOS Citizen'}
            {brandingResult && <span className="brand-tagline">{brandingResult.branding.tagline}</span>}
          </span>
        </span>
        <Link to="/profile" className="state-chip" style={{ textTransform: 'capitalize' }}>
          {state?.name ?? stateId}
        </Link>
      </header>
      {!online && (
        <p className="notice notice-warn" role="status" style={{ margin: '0.6rem 1rem 0' }}>
          You are offline. Submissions will be queued and sent when you reconnect.
        </p>
      )}
      {demo && (
        <p className="notice" role="status" style={{ margin: '0.6rem 1rem 0' }}>
          Demo mode — live services unreachable, showing sample data.
        </p>
      )}
      <main id="main">{screen}</main>
      {brandingResult && (
        <footer className="app-footer">
          <span>
            Support:{' '}
            <a href={`mailto:${brandingResult.branding.support_email}`}>
              {brandingResult.branding.support_email}
            </a>{' '}
            · {brandingResult.branding.support_phone}
          </span>
          <span className="muted small">{brandingResult.branding.custom_domain}</span>
        </footer>
      )}
      <nav className="app-nav" aria-label="Primary">
        {NAV.map((n) => (
          <Link key={n.to} to={n.to} aria-current={n.match.test(route) ? 'page' : undefined}>
            {n.label}
          </Link>
        ))}
      </nav>
    </div>
  );
}
