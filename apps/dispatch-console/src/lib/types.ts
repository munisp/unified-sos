// Types mirroring services/mod-police-cad (app/domain.py, app/main.py).

export type StateId = 'lagos' | 'ogun' | 'osun' | 'benue' | 'nasarawa' | 'taraba';

export const STATES: { id: StateId; name: string }[] = [
  { id: 'lagos', name: 'Lagos' },
  { id: 'ogun', name: 'Ogun' },
  { id: 'osun', name: 'Osun' },
  { id: 'benue', name: 'Benue' },
  { id: 'nasarawa', name: 'Nasarawa' },
  { id: 'taraba', name: 'Taraba' },
];

export type IncidentStatus = 'open' | 'dispatched' | 'escalated' | 'closed';
export type IncidentPriority = 'P1' | 'P2' | 'P3';
export type IncidentCategory = 'robbery' | 'medical' | 'fire' | 'disturbance' | 'accident';

export interface Incident {
  incident_id: string;
  tenant_state_id: StateId;
  agency: string; // e.g. AMOTEKUN | SO-SAFE | BSCPG | LNSC | NPF
  category: IncidentCategory;
  priority: IncidentPriority;
  latitude: number;
  longitude: number;
  status: IncidentStatus;
  reported_at: string; // ISO
  assigned_unit_id?: string;
  dispatch_latency_s?: number; // seconds from report to dispatch, once dispatched
}

export type UnitStatus = 'available' | 'enroute' | 'on-scene';

export interface Unit {
  unit_id: string;
  tenant_state_id: StateId;
  agency: string;
  call_sign: string;
  status: UnitStatus;
  personnel_count: number;
  biometric_enrolled: boolean; // ghost-worker payroll control
  latitude: number;
  longitude: number;
  registered_at: string;
}

export interface DispatchEvent {
  event_id: string;
  incident_id: string;
  unit_id: string;
  latitude: number;
  longitude: number;
  dispatched_at: string;
  geofence_verified: boolean;
}

export type TrustFundEntryKind = 'donation' | 'disbursement';

export interface TrustFundEntry {
  entry_id: string;
  tenant_state_id: StateId;
  kind: TrustFundEntryKind;
  ref: string; // donor_ref or purpose
  amount_kobo: number;
  at: string; // ISO
  prev_hash: string;
  hash: string; // hash-chain over (prev_hash | entry payload)
}

export type StreamKind = 'cctv' | 'drone';

export interface CameraStream {
  stream_id: string;
  tenant_state_id: StateId;
  kind: StreamKind;
  label: string;
  latitude: number;
  longitude: number;
  online: boolean;
}

export interface StreamSession {
  session_id: string;
  stream_id: string;
  offer_url: string; // WebRTC signalling endpoint (placeholder in demo mode)
  ice_servers: string[];
}

export const DISPATCH_SLO_S = 30; // dispatch latency SLO (seconds)
