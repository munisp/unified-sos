// Deterministic demo fixtures, used when the API is unreachable (fail-soft).
// The catalog mirrors DEFAULT_CATALOG in services/mod-citizen-portal/app/service.py.

import type {
  ConcessionEscrowStatement,
  ProcurementAuditDigest,
  ProcurementAuditVerification,
  ServiceCatalogEntry,
  ServiceCategory,
  StateId,
  TrustFundFeed,
  WalletRead,
} from './types';

type Seed = [string, string, ServiceCategory, string, number, number, string, string];

const SEED: Seed[] = [
  ['REV-TAX-ID', 'Tax ID & PAYE registration', 'REVENUE', 'Board of Internal Revenue', 0, 50_000, 'mod-rev-core', '/rev/v1/taxpayers'],
  ['LAND-COFO', 'Certificate of Occupancy', 'LANDS', 'Lands Bureau', 500_000, 1_500_000, 'mod-gis-lands', '/lands/v1/titles'],
  ['HLT-PHC-REG', 'Primary healthcare registration', 'HEALTH', 'Ministry of Health', 0, 20_000, 'mod-health', '/health/v1/facilities'],
  ['EDU-SCH-TRANS', 'School transfer & records', 'EDUCATION', 'Ministry of Education', 10_000, 50_000, 'mod-education', '/edu/v1/transfers'],
  ['MKT-STALL', 'Market stall allocation', 'MARKET', 'Market Development Authority', 100_000, 250_000, 'mod-market', '/market/v1/stalls'],
  ['MIN-EPERMIT', 'Mining e-permit & levy account', 'MINING', 'Ministry of Solid Minerals', 250_000, 750_000, 'mod-mining', '/mining/v1/permits'],
  ['AGR-WAYBILL', 'Agricultural produce waybill', 'AGRICULTURE', 'Ministry of Agriculture', 25_000, 75_000, 'mod-agri-waybill', '/agri/v1/waybills'],
  ['TRN-WIM-FINE', 'Transport WIM violation fine payment', 'TRANSPORT', 'Transport Ministry (WIM)', 50_000, 0, 'mod-transport-wim', '/wim/v1/violations'],
  ['ENV-PERMIT', 'Environmental impact permit', 'ENVIRONMENT', 'Environmental Protection Agency', 300_000, 900_000, 'mod-environment', '/env/v1/permits'],
  ['FOR-TTP', 'Forestry timber transit permit', 'FORESTRY', 'Forestry Commission', 150_000, 450_000, 'mod-forestry', '/forestry/v1/transit-permits'],
  ['PPP-DISCLOSURE', 'PPP project disclosure lookup', 'INVESTMENT', 'PPP / Investment Office', 0, 0, 'mod-ppp-investment', '/ppp/v1/disclosures'],
];

export function fixtureCatalog(stateId: StateId): ServiceCatalogEntry[] {
  return SEED.map(([code, name, category, mda, base, exp, module, hint]) => ({
    service_code: code,
    state_id: stateId,
    name,
    category,
    mda,
    base_fee_kobo: base,
    expedited_fee_kobo: exp,
    sla_days: 14,
    active: true,
    module,
    endpoint_hint: hint,
  }));
}

export function fixtureWallet(stateId: StateId): WalletRead {
  return {
    wallet_id: 'WLT-DEMO01',
    state_id: stateId,
    masked_nin: '**** **** **34',
    keycloak_realm: `sos-${stateId}`,
    keycloak_client_id: 'citizen-portal',
    status: 'ACTIVE',
  };
}

export function fixtureTrustFundFeed(stateId: StateId): TrustFundFeed {
  return {
    state_id: stateId,
    balance_kobo: 128_450_000_00,
    head_cursor: 'f3a9c1d7e2b4a8f0c6d1e9b3a5c7d2f4e8a0b6c4d2f8a6e0c4b2d8f6a4e2c0b8',
    entries: [
      {
        entry_id: 'TFE-000003',
        kind: 'disbursement',
        amount_kobo: 40_000_000_00,
        purpose_label: 'Patrol vehicles for area commands',
        occurred_at: '2026-01-28T09:30:00Z',
        cursor: 'f3a9c1d7e2b4a8f0c6d1e9b3a5c7d2f4e8a0b6c4d2f8a6e0c4b2d8f6a4e2c0b8',
      },
      {
        entry_id: 'TFE-000002',
        kind: 'donation',
        amount_kobo: 150_000_000_00,
        donor_alias_hash: '9b2f7c41aa08e6d2c15f9a30b84d2e77c0f1a6b9d3e5c8a2f4b6d8e0a2c4f6b8',
        occurred_at: '2026-01-15T11:05:00Z',
        cursor: 'b84d2e77c0f1a6b9d3e5c8a2f4b6d8e0a2c4f6b8f3a9c1d7e2b4a8f0c6d1e9b3',
      },
      {
        entry_id: 'TFE-000001',
        kind: 'donation',
        amount_kobo: 18_450_000_00,
        donor_alias_hash: '51aa2c90d4e6f8a0b2c4d6e8f0a2b4c6d8e0f2a4b6c8d0e2f4a6b8c0d2e4f6a8',
        occurred_at: '2026-01-02T08:00:00Z',
        cursor: '9b2f7c41aa08e6d2c15f9a30b84d2e77c0f1a6b9d3e5c8a2f4b6d8e0a2c4f6b8',
      },
    ],
  };
}

export function fixtureEscrowStatements(): ConcessionEscrowStatement[] {
  return [
    {
      statement_id: 'STL-PUB-0002',
      contract_ref_hash: 'c1d7e2b4a8f0c6d1e9b3a5c7d2f4e8a0b6c4d2f8a6e0c4b2d8f6a4e2c0b8f3a9',
      period: '2026-01',
      gross_collections_kobo: 82_300_000_00,
      state_share_kobo: 24_690_000_00,
      concessionaire_share_kobo: 57_610_000_00,
      applied_state_share_bps: 3000,
      reconciled: true,
      cursor: 'd2f4e8a0b6c4d2f8a6e0c4b2d8f6a4e2c0b8f3a9c1d7e2b4a8f0c6d1e9b3a5c7',
    },
    {
      statement_id: 'STL-PUB-0001',
      contract_ref_hash: 'c1d7e2b4a8f0c6d1e9b3a5c7d2f4e8a0b6c4d2f8a6e0c4b2d8f6a4e2c0b8f3a9',
      period: '2025-12',
      gross_collections_kobo: 76_800_000_00,
      state_share_kobo: 23_040_000_00,
      concessionaire_share_kobo: 53_760_000_00,
      applied_state_share_bps: 3000,
      reconciled: true,
      cursor: 'e8a0b6c4d2f8a6e0c4b2d8f6a4e2c0b8f3a9c1d7e2b4a8f0c6d1e9b3a5c7d2f4',
    },
  ];
}

export function fixtureAuditDigests(): ProcurementAuditDigest[] {
  return [
    {
      seq: 3,
      action: 'CONTRACT_AWARDED',
      subject_ref_hash: 'a5c7d2f4e8a0b6c4d2f8a6e0c4b2d8f6a4e2c0b8f3a9c1d7e2b4a8f0c6d1e9b3',
      prev_hash: 'b6c4d2f8a6e0c4b2d8f6a4e2c0b8f3a9c1d7e2b4a8f0c6d1e9b3a5c7d2f4e8a0',
      entry_hash: 'c6d1e9b3a5c7d2f4e8a0b6c4d2f8a6e0c4b2d8f6a4e2c0b8f3a9c1d7e2b4a8f0',
      at: '2026-01-30T14:12:00Z',
    },
    {
      seq: 2,
      action: 'BID_OPENED',
      subject_ref_hash: 'a5c7d2f4e8a0b6c4d2f8a6e0c4b2d8f6a4e2c0b8f3a9c1d7e2b4a8f0c6d1e9b3',
      prev_hash: 'd8f6a4e2c0b8f3a9c1d7e2b4a8f0c6d1e9b3a5c7d2f4e8a0b6c4d2f8a6e0c4b2',
      entry_hash: 'b6c4d2f8a6e0c4b2d8f6a4e2c0b8f3a9c1d7e2b4a8f0c6d1e9b3a5c7d2f4e8a0',
      at: '2026-01-20T10:00:00Z',
    },
    {
      seq: 1,
      action: 'TENDER_PUBLISHED',
      subject_ref_hash: 'a5c7d2f4e8a0b6c4d2f8a6e0c4b2d8f6a4e2c0b8f3a9c1d7e2b4a8f0c6d1e9b3',
      prev_hash: 'GENESIS',
      entry_hash: 'd8f6a4e2c0b8f3a9c1d7e2b4a8f0c6d1e9b3a5c7d2f4e8a0b6c4d2f8a6e0c4b2',
      at: '2026-01-05T09:00:00Z',
    },
  ];
}

export function fixtureAuditVerification(stateId: StateId): ProcurementAuditVerification {
  return {
    state_id: stateId,
    chain_valid: true,
    entries_checked: 3,
    head_hash: 'c6d1e9b3a5c7d2f4e8a0b6c4d2f8a6e0c4b2d8f6a4e2c0b8f3a9c1d7e2b4a8f0',
    first_invalid_seq: null,
  };
}
