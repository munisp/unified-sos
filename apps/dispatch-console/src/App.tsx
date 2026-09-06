import { useEffect, useState } from 'react';

import { Header } from './components/Header';
import { IncidentQueue } from './components/IncidentQueue';
import { MapPanel } from './components/MapPanel';
import { SloBanner } from './components/SloBanner';
import { StreamViewer } from './components/StreamViewer';
import { TrustFundPanel } from './components/TrustFundPanel';
import { UnitRoster } from './components/UnitRoster';
import { ConsoleProvider, useConsole } from './lib/store';

function ConsoleBody() {
  const { loading } = useConsole();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1_000);
    return () => clearInterval(t);
  }, []);

  if (loading) return <main className="app-main">Loading tenant data…</main>;
  return (
    <main className="app-main">
      <SloBanner now={now} />
      <div className="grid">
        <div className="col">
          <IncidentQueue now={now} />
          <MapPanel />
        </div>
        <div className="col">
          <UnitRoster />
          <StreamViewer />
          <TrustFundPanel />
        </div>
      </div>
    </main>
  );
}

export default function App() {
  return (
    <ConsoleProvider>
      <div className="app-shell">
        <Header />
        <ConsoleBody />
      </div>
    </ConsoleProvider>
  );
}
