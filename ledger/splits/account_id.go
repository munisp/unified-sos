package splits

import "fmt"

// 128-bit account identifier layout (ledger/chart-of-accounts.md,
// docs/architecture/adr/ADR-002):
//
//	[ Bits 0..15:   State Tenant ID   ]
//	[ Bits 16..31:  MDA Category      ]
//	[ Bits 32..47:  Account Class     ]  (4-digit chart code, e.g. 3001)
//	[ Bits 48..127: Unique Entity ID  ]  (80 bits)
const (
	stateShift  = 0
	mdaShift    = 16
	classShift  = 32
	entityShift = 48

	mask16 uint64 = 0xFFFF
	// mask48 selects the low 48 bits (the portion of the entity ID that
	// lives in the high limb of the account ID).
	mask48 uint64 = 0x0000_FFFF_FFFF_FFFF
)

// State tenant IDs per ledger/chart-of-accounts.md.
const (
	StateLagos    uint16 = 0x0001
	StateOgun     uint16 = 0x0002
	StateOsun     uint16 = 0x0003
	StateBenue    uint16 = 0x0004
	StateNasarawa uint16 = 0x0005
	StateTaraba   uint16 = 0x0006
)

// Well-known account class codes (ledger/chart-of-accounts.md).
const (
	ClassPayerClearing             uint16 = 1001
	ClassMDARetention              uint16 = 2010
	ClassLocalGovernmentSharePool  uint16 = 2020
	ClassConcessionaireEscrow      uint16 = 2099
	ClassConsolidatedRevenueFund   uint16 = 3001
	ClassSecurityTrustFund         uint16 = 4001
	ClassTransportUnionCommission  uint16 = 4002
	ClassFederalRoyaltyPassThrough uint16 = 5001
)

// AccountParts is the decoded form of a 128-bit account identifier.
type AccountParts struct {
	StateTenant  uint16  // bits 0..15
	MDACategory  uint16  // bits 16..31
	AccountClass uint16  // bits 32..47
	Entity       Uint128 // bits 48..127 (fits in 80 bits)
}

// EntityFromUint64 builds an 80-bit entity identifier from a 64-bit
// sequence number.
func EntityFromUint64(v uint64) Uint128 { return Uint128{Lo: v} }

// BuildAccountID packs the chart-of-accounts fields into a 128-bit
// account identifier. entity must fit in 80 bits (Entity.Hi <= 0xFFFF).
func BuildAccountID(stateTenant, mdaCategory, accountClass uint16, entity Uint128) (Uint128, error) {
	if entity.Hi > mask16 {
		return Uint128{}, fmt.Errorf("splits: entity ID %#v exceeds 80 bits", entity)
	}
	id := Uint128{
		Lo: (entity.Lo << entityShift) |
			(uint64(accountClass) << classShift) |
			(uint64(mdaCategory) << mdaShift) |
			(uint64(stateTenant) << stateShift),
		Hi: (entity.Hi << entityShift) | (entity.Lo >> (64 - entityShift)),
	}
	return id, nil
}

// MustBuildAccountID is BuildAccountID that panics on error; intended
// for static chart-of-accounts bootstrap constants.
func MustBuildAccountID(stateTenant, mdaCategory, accountClass uint16, entity Uint128) Uint128 {
	id, err := BuildAccountID(stateTenant, mdaCategory, accountClass, entity)
	if err != nil {
		panic(err)
	}
	return id
}

// DecodeAccountID unpacks a 128-bit account identifier into its chart-
// of-accounts fields.
func DecodeAccountID(id Uint128) AccountParts {
	return AccountParts{
		StateTenant:  uint16((id.Lo >> stateShift) & mask16),
		MDACategory:  uint16((id.Lo >> mdaShift) & mask16),
		AccountClass: uint16((id.Lo >> classShift) & mask16),
		Entity: Uint128{
			Lo: (id.Lo >> entityShift) | ((id.Hi & mask48) << (64 - entityShift)),
			Hi: id.Hi >> entityShift,
		},
	}
}
