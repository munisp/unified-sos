// Types mirroring contracts/openapi/mod-citizen-portal.yaml and mod-transparency.yaml.

export type StateId = 'lagos' | 'ogun' | 'osun' | 'benue' | 'nasarawa' | 'taraba';

export const STATES: { id: StateId; name: string; ussd: string }[] = [
  { id: 'lagos', name: 'Lagos', ussd: '*347*11#' },
  { id: 'ogun', name: 'Ogun', ussd: '*347*12#' },
  { id: 'osun', name: 'Osun', ussd: '*347*13#' },
  { id: 'benue', name: 'Benue', ussd: '*347*14#' },
  { id: 'nasarawa', name: 'Nasarawa', ussd: '*347*15#' },
  { id: 'taraba', name: 'Taraba', ussd: '*347*16#' },
];

export type ServiceCategory =
  | 'REVENUE' | 'LANDS' | 'HEALTH' | 'EDUCATION' | 'MARKET' | 'MINING'
  | 'AGRICULTURE' | 'TRANSPORT' | 'ENVIRONMENT' | 'FORESTRY' | 'INVESTMENT';

export interface ServiceCatalogEntry {
  service_code: string;
  state_id: string;
  name: string;
  category: ServiceCategory;
  mda: string;
  base_fee_kobo: number;
  expedited_fee_kobo: number;
  sla_days: number;
  active: boolean;
  module: string;
  endpoint_hint: string;
}

export type Priority = 'STANDARD' | 'EXPEDITED';
export type RequestStatus = 'SUBMITTED' | 'IN_REVIEW' | 'APPROVED' | 'REJECTED' | 'COMPLETED';

export interface StatusEvent {
  status: string;
  at: string;
  note: string;
}

export interface ServiceRequest {
  request_id: string;
  state_id: string;
  wallet_id: string;
  service_code: string;
  form_payload: Record<string, string>;
  priority: Priority;
  fee_kobo: number;
  status: RequestStatus;
  timeline: StatusEvent[];
  created_at: string;
}

export interface ServiceRequestCreate {
  state_id: string;
  wallet_id: string;
  service_code: string;
  form_payload?: Record<string, string>;
  priority?: Priority;
}

export interface WalletRead {
  wallet_id: string;
  state_id: string;
  masked_nin: string;
  keycloak_realm: string;
  keycloak_client_id: string;
  status: string;
}

// --- mod-transparency ---
export interface TrustFundEntry {
  entry_id: string;
  kind: 'donation' | 'disbursement';
  amount_kobo: number;
  donor_alias_hash?: string | null;
  purpose_label?: string | null;
  occurred_at: string;
  cursor: string;
}

export interface TrustFundFeed {
  state_id: string;
  balance_kobo: number;
  entries: TrustFundEntry[];
  head_cursor: string;
}

export interface ConcessionEscrowStatement {
  statement_id: string;
  contract_ref_hash: string;
  period: string;
  gross_collections_kobo: number;
  state_share_kobo: number;
  concessionaire_share_kobo: number;
  applied_state_share_bps: number;
  reconciled: boolean;
  cursor: string;
}

export interface ProcurementAuditDigest {
  seq: number;
  action: string;
  subject_ref_hash: string;
  prev_hash: string;
  entry_hash: string;
  at: string;
}

export interface ProcurementAuditVerification {
  state_id: string;
  chain_valid: boolean;
  entries_checked: number;
  head_hash: string;
  first_invalid_seq?: number | null;
}

// --- mod-gis-lands deed verification (cadastre-parcels.yaml) ---
export interface DeedVerification {
  c_of_o_number: string;
  parcel_uin?: string;
  valid: boolean;
  title_type?: string;
  holder_hash?: string;
  signature_chain_valid?: boolean;
  detail?: string;
}

// --- FSPIOP payment stub ---
export interface PaymentQuote {
  quote_id: string;
  ticket_ref: string;
  amount_kobo: number;
  payee_fsp: string;
  expires_at: string;
  status: 'QUOTED' | 'COMPLETED' | 'FAILED';
}
