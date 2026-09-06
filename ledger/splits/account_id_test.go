package splits

import "testing"

func TestAccountIDRoundTrip(t *testing.T) {
	cases := []AccountParts{
		{StateTenant: StateOgun, MDACategory: 0, AccountClass: ClassConsolidatedRevenueFund, Entity: Uint128{}},
		{StateTenant: StateLagos, MDACategory: 7, AccountClass: ClassPayerClearing, Entity: EntityFromUint64(42)},
		{StateTenant: StateTaraba, MDACategory: 0xFFFF, AccountClass: 9999, Entity: Uint128{Lo: 0xFFFF_FFFF_FFFF_FFFF, Hi: 0xFFFF}},
		{StateTenant: 0, MDACategory: 0, AccountClass: 0, Entity: Uint128{Lo: 1, Hi: 0x1234}},
	}
	for _, tc := range cases {
		id, err := BuildAccountID(tc.StateTenant, tc.MDACategory, tc.AccountClass, tc.Entity)
		if err != nil {
			t.Fatalf("BuildAccountID(%+v): %v", tc, err)
		}
		got := DecodeAccountID(id)
		if got != tc {
			t.Fatalf("round trip mismatch: want %+v got %+v (id=%#v)", tc, got, id)
		}
	}
}

func TestAccountIDBitLayout(t *testing.T) {
	// Entity 1, state Ogun, MDA 3, class 3001: verify exact bit packing.
	id, err := BuildAccountID(StateOgun, 3, ClassConsolidatedRevenueFund, EntityFromUint64(1))
	if err != nil {
		t.Fatal(err)
	}
	wantLo := uint64(1)<<48 | uint64(ClassConsolidatedRevenueFund)<<32 | uint64(3)<<16 | uint64(StateOgun)
	if id.Lo != wantLo || id.Hi != 0 {
		t.Fatalf("bit layout: got %#v, want Lo=%#x Hi=0", id, wantLo)
	}

	// Entity bits above 64 spill into the high limb at bit 112.
	entity := Uint128{Lo: 0xABCD_EF01_2345_6789, Hi: 0x0077}
	id, err = BuildAccountID(StateNasarawa, 9, ClassMDARetention, entity)
	if err != nil {
		t.Fatal(err)
	}
	got := DecodeAccountID(id)
	if got.Entity != entity {
		t.Fatalf("entity spill round trip: want %#v got %#v", entity, got.Entity)
	}
}

func TestBuildAccountIDRejectsOversizedEntity(t *testing.T) {
	if _, err := BuildAccountID(StateOgun, 0, 3001, Uint128{Hi: 0x1_0000}); err == nil {
		t.Fatal("expected error for entity exceeding 80 bits")
	}
}

func TestDistinctAccountsForStates(t *testing.T) {
	a := MustBuildAccountID(StateLagos, 0, 3001, Uint128{})
	b := MustBuildAccountID(StateOgun, 0, 3001, Uint128{})
	if a == b {
		t.Fatal("accounts for different state tenants must differ")
	}
}
