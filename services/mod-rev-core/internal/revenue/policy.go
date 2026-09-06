package revenue

import (
	"embed"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/munisp/unified-sos/ledger/splits"
)

// Policy catalog: the per-state revenue-head registry, loaded from
// gazetted policy packs (contracts/policy-packs/revenue-split.schema.json)
// via the ledger/splits loader. No tax policy is hard-coded in the binary
// (CONTRIBUTING.md rule 1); the embedded seeds under seed/ mirror the
// gazetted packs so the service boots and tests run standalone, and
// REV_CORE_POLICY_DIR overrides them at deploy time.

//go:embed seed
var seedFS embed.FS

// PolicyCatalog indexes validated policy packs by state and revenue head.
type PolicyCatalog struct {
	packs map[string]map[string]*splits.PolicyPack // state → revenue head → pack
}

// NewPolicyCatalog returns an empty catalog.
func NewPolicyCatalog() *PolicyCatalog {
	return &PolicyCatalog{packs: make(map[string]map[string]*splits.PolicyPack)}
}

// Add validates ownership and registers a pack. Packs whose
// tenant_state_id disagrees with their content are already rejected by
// the splits loader; here we only index.
func (c *PolicyCatalog) Add(pack *splits.PolicyPack) {
	if c.packs[pack.TenantStateID] == nil {
		c.packs[pack.TenantStateID] = make(map[string]*splits.PolicyPack)
	}
	c.packs[pack.TenantStateID][pack.RevenueHead] = pack
}

// Lookup returns the pack governing (state, revenueHead).
func (c *PolicyCatalog) Lookup(state, revenueHead string) (*splits.PolicyPack, bool) {
	pack, ok := c.packs[state][revenueHead]
	return pack, ok
}

// RevenueHeads lists the catalogued revenue heads for a state.
func (c *PolicyCatalog) RevenueHeads(state string) []string {
	var heads []string
	for h := range c.packs[state] {
		heads = append(heads, h)
	}
	sort.Strings(heads)
	return heads
}

// LoadDir parses every *.json policy pack in dir into the catalog.
func (c *PolicyCatalog) LoadDir(dir string) error {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return fmt.Errorf("revenue: read policy dir %s: %w", dir, err)
	}
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".json") {
			continue
		}
		pack, err := splits.LoadPolicyPackFile(filepath.Join(dir, e.Name()))
		if err != nil {
			return fmt.Errorf("revenue: load policy %s: %w", e.Name(), err)
		}
		c.Add(pack)
	}
	return nil
}

// LoadEmbedded parses the seed policy packs embedded in the binary.
func (c *PolicyCatalog) LoadEmbedded() error {
	return fs.WalkDir(seedFS, "seed", func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() || !strings.HasSuffix(d.Name(), ".json") {
			return nil
		}
		data, err := seedFS.ReadFile(path)
		if err != nil {
			return err
		}
		pack, err := splits.ParsePolicyPack(data)
		if err != nil {
			return fmt.Errorf("revenue: embedded policy %s: %w", path, err)
		}
		c.Add(pack)
		return nil
	})
}

// LoadPolicyCatalog builds the runtime catalog: embedded seeds plus, when
// dir is non-empty, an override directory (which wins per state/head).
func LoadPolicyCatalog(dir string) (*PolicyCatalog, error) {
	c := NewPolicyCatalog()
	if err := c.LoadEmbedded(); err != nil {
		return nil, err
	}
	if dir != "" {
		if err := c.LoadDir(dir); err != nil {
			return nil, err
		}
	}
	return c, nil
}
