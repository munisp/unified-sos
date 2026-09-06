package revenue

import "testing"

func TestValidateSTIN(t *testing.T) {
	cases := []struct {
		stin, state string
		wantErr     bool
	}{
		{"NG-NAS-2026-892104", "nasarawa", false},
		{"NG-LAG-2020-000001", "lagos", false},
		{"NG-OGU-2026-123456", "ogun", false},
		{"NG-NAS-2026-892104", "ogun", true},     // cross-tenant STIN
		{"NG-NAS-2026-89210", "nasarawa", true},  // 5 digits
		{"NG-NAS-202-892104", "nasarawa", true},  // 3-digit year
		{"ng-nas-2026-892104", "nasarawa", true}, // lowercase
		{"NG-NAS-2026-8921047", "nasarawa", true},
		{"XX-NAS-2026-892104", "nasarawa", true},
		{"", "nasarawa", true},
		{"NG-XXX-2026-892104", "kano", true}, // unknown tenant
	}
	for _, tc := range cases {
		err := ValidateSTIN(tc.stin, tc.state)
		if (err != nil) != tc.wantErr {
			t.Errorf("ValidateSTIN(%q, %q) err=%v, wantErr=%v", tc.stin, tc.state, err, tc.wantErr)
		}
	}
}
