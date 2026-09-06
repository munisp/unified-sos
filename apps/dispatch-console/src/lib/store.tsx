// Console store: tenant-scoped incidents/units/streams/trust-fund with
// fail-soft demo fixtures and optimistic queue actions.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import {
  dispatchUnit,
  isDemoMode,
  listIncidents,
  listStreams,
  listUnits,
  trustFundAuditFeed,
} from './api';
import type {
  CameraStream,
  Incident,
  StateId,
  TrustFundEntry,
  Unit,
} from './types';

export interface ConsoleState {
  stateId: StateId;
  setStateId: (s: StateId) => void;
  incidents: Incident[];
  units: Unit[];
  streams: CameraStream[];
  trustFund: TrustFundEntry[];
  demoMode: boolean;
  loading: boolean;
  assignUnit: (incidentId: string, unitId: string) => Promise<void>;
  escalateIncident: (incidentId: string) => void;
  closeIncident: (incidentId: string) => void;
}

const Ctx = createContext<ConsoleState | null>(null);

export function ConsoleProvider({ children }: { children: ReactNode }) {
  const [stateId, setStateId] = useState<StateId>('lagos');
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  const [streams, setStreams] = useState<CameraStream[]>([]);
  const [trustFund, setTrustFund] = useState<TrustFundEntry[]>([]);
  const [demoMode, setDemoMode] = useState(isDemoMode());
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void (async () => {
      const [inc, uni, str, tf] = await Promise.all([
        listIncidents(stateId),
        listUnits(stateId),
        listStreams(stateId),
        trustFundAuditFeed(stateId),
      ]);
      if (cancelled) return;
      setIncidents(inc.data);
      setUnits(uni.data);
      setStreams(str.data);
      setTrustFund(tf.data);
      setDemoMode(inc.demo || uni.demo || str.demo || tf.demo);
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [stateId]);

  const assignUnit = useCallback(
    async (incidentId: string, unitId: string) => {
      const incident = incidents.find((i) => i.incident_id === incidentId);
      const unit = units.find((u) => u.unit_id === unitId);
      if (!incident || !unit) return;
      if (incident.status !== 'open' && incident.status !== 'escalated') return;
      if (unit.status !== 'available') return;
      const { event } = await dispatchUnit(incident, unit);
      const latency = Math.max(
        0,
        Math.round((Date.parse(event.dispatched_at) - Date.parse(incident.reported_at)) / 1000),
      );
      setIncidents((prev) =>
        prev.map((i) =>
          i.incident_id === incidentId
            ? { ...i, status: 'dispatched', assigned_unit_id: unitId, dispatch_latency_s: latency }
            : i,
        ),
      );
      setUnits((prev) =>
        prev.map((u) => (u.unit_id === unitId ? { ...u, status: 'enroute' } : u)),
      );
    },
    [incidents, units],
  );

  const escalateIncident = useCallback((incidentId: string) => {
    setIncidents((prev) =>
      prev.map((i) =>
        i.incident_id === incidentId && (i.status === 'open' || i.status === 'dispatched')
          ? { ...i, status: 'escalated', priority: 'P1' }
          : i,
      ),
    );
  }, []);

  const closeIncident = useCallback((incidentId: string) => {
    setIncidents((prev) =>
      prev.map((i) => (i.incident_id === incidentId ? { ...i, status: 'closed' } : i)),
    );
    setUnits((prev) =>
      prev.map((u) =>
        incidents.find((i) => i.incident_id === incidentId)?.assigned_unit_id === u.unit_id
          ? { ...u, status: 'available' }
          : u,
      ),
    );
  }, [incidents]);

  const value = useMemo<ConsoleState>(
    () => ({
      stateId,
      setStateId,
      incidents,
      units,
      streams,
      trustFund,
      demoMode,
      loading,
      assignUnit,
      escalateIncident,
      closeIncident,
    }),
    [stateId, incidents, units, streams, trustFund, demoMode, loading, assignUnit, escalateIncident, closeIncident],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useConsole(): ConsoleState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useConsole must be used within ConsoleProvider');
  return ctx;
}
