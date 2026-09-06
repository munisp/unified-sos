import { useState } from 'react';

import { useConsole } from '../lib/store';
import { useStreamSession } from '../lib/useStreamSession';
import type { CameraStream } from '../lib/types';

function SimulatedFrame({ stream }: { stream: CameraStream }) {
  // Deterministic simulated feed frame (demo mode — no real WebRTC media).
  return (
    <div className="sim-frame" role="img" aria-label={`simulated feed for ${stream.label}`}>
      <svg viewBox="0 0 320 180">
        <rect width="320" height="180" fill="#33291f" />
        {Array.from({ length: 8 }, (_, i) => (
          <line
            key={i}
            x1={0}
            y1={20 + i * 20}
            x2={320}
            y2={14 + i * 22}
            stroke="#6f6154"
            strokeWidth={1}
            opacity={0.6}
          />
        ))}
        <text x={12} y={24} fill="#f3ece1" fontSize={12}>
          {stream.kind.toUpperCase()} · {stream.stream_id}
        </text>
        <text x={12} y={168} fill="#8c5230" fontSize={11}>
          SIMULATED FEED — demo mode
        </text>
      </svg>
    </div>
  );
}

function StreamCard({ stream }: { stream: CameraStream }) {
  const { session, connecting, demo, error, connect, disconnect } = useStreamSession(
    stream.online ? stream : null,
  );
  return (
    <li className="stream-card" data-testid={`stream-${stream.stream_id}`}>
      <div className="stream-head">
        <strong>{stream.label}</strong>
        <span className={`badge ${stream.online ? 'badge-ok' : 'badge-bad'}`}>
          {stream.online ? stream.kind : 'offline'}
        </span>
      </div>
      {session ? (
        <>
          {demo ? (
            <SimulatedFrame stream={stream} />
          ) : (
            <video aria-label={`live feed ${stream.label}`} autoPlay muted playsInline />
          )}
          <p className="muted">session {session.session_id}</p>
          <button onClick={disconnect}>Stop</button>
        </>
      ) : (
        <button
          className="btn-primary"
          disabled={!stream.online || connecting}
          onClick={() => void connect()}
        >
          {connecting ? 'Connecting…' : stream.online ? 'View live' : 'Offline'}
        </button>
      )}
      {error && <p className="error">{error}</p>}
    </li>
  );
}

export function StreamViewer() {
  const { streams } = useConsole();
  const [filter, setFilter] = useState<'all' | 'cctv' | 'drone'>('all');
  const visible = streams.filter((s) => filter === 'all' || s.kind === filter);
  return (
    <section className="panel" aria-label="Live streams">
      <h2>Live streams</h2>
      <div className="stream-filters" role="tablist">
        {(['all', 'cctv', 'drone'] as const).map((f) => (
          <button
            key={f}
            role="tab"
            aria-selected={filter === f}
            className={filter === f ? 'active' : ''}
            onClick={() => setFilter(f)}
          >
            {f}
          </button>
        ))}
      </div>
      <ul className="stream-grid">
        {visible.map((s) => (
          <StreamCard key={s.stream_id} stream={s} />
        ))}
      </ul>
    </section>
  );
}
