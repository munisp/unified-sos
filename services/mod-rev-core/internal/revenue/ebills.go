package revenue

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"time"
)

// EBillNotifier issues assessment bills to an external e-Bills gateway
// (NIBSS e-Bills). The seam mirrors the fail-closed adapter idiom used by
// the Python services: production mode ("nibss") requires a configured
// endpoint; the deterministic local default is NoopEBillNotifier.
type EBillNotifier interface {
	// IssueBill registers the assessment with the gateway and returns the
	// bill reference to expose to payers.
	IssueBill(assessment *Assessment) (billRef string, err error)
}

// NoopEBillNotifier is the deterministic local default: the locally minted
// bill reference is used and no external call is made.
type NoopEBillNotifier struct{}

// IssueBill returns the assessment's existing bill reference unchanged.
func (NoopEBillNotifier) IssueBill(a *Assessment) (string, error) {
	return a.BillReference, nil
}

// HTTPEBillNotifier is the production NIBSS e-Bills client.
type HTTPEBillNotifier struct {
	BaseURL string
	Client  *http.Client
}

type ebillIssueRequest struct {
	BillReference string `json:"billReference"`
	AmountMinor   int64  `json:"amountMinor"`
	Currency      string `json:"currency"`
	PayerSTIN     string `json:"payerStin"`
	MDACode       string `json:"mdaCode"`
	RevenueHead   string `json:"revenueHead"`
}

type ebillIssueResponse struct {
	BillReference string `json:"billReference"`
}

// IssueBill posts the bill to the gateway; a non-2xx response or an empty
// reference is an error (fail closed — never silently fall back).
func (n *HTTPEBillNotifier) IssueBill(a *Assessment) (string, error) {
	if n.BaseURL == "" {
		return "", fmt.Errorf("ebills: no NIBSS_EBILLS_URL configured")
	}
	client := n.Client
	if client == nil {
		client = &http.Client{Timeout: 10 * time.Second}
	}
	payload, err := json.Marshal(ebillIssueRequest{
		BillReference: a.BillReference,
		AmountMinor:   a.AmountDueKobo,
		Currency:      "NGN",
		PayerSTIN:     a.TaxpayerSTIN,
		MDACode:       a.MDACode,
		RevenueHead:   a.RevenueHead,
	})
	if err != nil {
		return "", fmt.Errorf("ebills: marshal: %w", err)
	}
	req, err := http.NewRequestWithContext(context.Background(), http.MethodPost,
		n.BaseURL+"/bills", bytes.NewReader(payload))
	if err != nil {
		return "", fmt.Errorf("ebills: build request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := client.Do(req)
	if err != nil {
		return "", fmt.Errorf("ebills: gateway call: %w", err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return "", fmt.Errorf("ebills: read response: %w", err)
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return "", fmt.Errorf("ebills: gateway returned %d: %s", resp.StatusCode, body)
	}
	var ack ebillIssueResponse
	if err := json.Unmarshal(body, &ack); err != nil {
		return "", fmt.Errorf("ebills: decode ack: %w", err)
	}
	if ack.BillReference == "" {
		return "", fmt.Errorf("ebills: gateway ack carried no bill reference")
	}
	return ack.BillReference, nil
}

// NewEBillNotifierFromEnv resolves the notifier from the environment:
//
//	REV_CORE_EBILLS  "" (default) or "noop"  → NoopEBillNotifier
//	                 "nibss"                  → HTTPEBillNotifier, requires
//	                                           NIBSS_EBILLS_URL (fail closed)
//	NIBSS_EBILLS_URL e-Bills gateway base URL
func NewEBillNotifierFromEnv() (EBillNotifier, error) {
	switch mode := os.Getenv("REV_CORE_EBILLS"); mode {
	case "", "noop":
		return NoopEBillNotifier{}, nil
	case "nibss":
		url := os.Getenv("NIBSS_EBILLS_URL")
		if url == "" {
			return nil, fmt.Errorf("REV_CORE_EBILLS=nibss requires NIBSS_EBILLS_URL (fail closed)")
		}
		return &HTTPEBillNotifier{BaseURL: url}, nil
	default:
		return nil, fmt.Errorf("unknown REV_CORE_EBILLS %q (want noop|nibss)", mode)
	}
}
