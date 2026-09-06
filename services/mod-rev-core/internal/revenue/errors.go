package revenue

import (
	"errors"
	"fmt"
	"net/http"
	"time"
)

// ErrConflict signals a uniqueness violation (e.g. bill reference).
var ErrConflict = errors.New("revenue: conflict")

func timeFromUnix(sec int64) time.Time { return time.Unix(sec, 0).UTC() }

// APIError is a domain error carrying an HTTP status and a stable
// machine-readable code.
type APIError struct {
	Status  int
	Code    string
	Message string
}

func (e *APIError) Error() string { return fmt.Sprintf("%s: %s", e.Code, e.Message) }

func badRequest(code, format string, args ...any) *APIError {
	return &APIError{Status: http.StatusBadRequest, Code: code, Message: fmt.Sprintf(format, args...)}
}

func notFound(code, format string, args ...any) *APIError {
	return &APIError{Status: http.StatusNotFound, Code: code, Message: fmt.Sprintf(format, args...)}
}

func conflict(code, format string, args ...any) *APIError {
	return &APIError{Status: http.StatusConflict, Code: code, Message: fmt.Sprintf(format, args...)}
}

func internalError(code, format string, args ...any) *APIError {
	return &APIError{Status: http.StatusInternalServerError, Code: code, Message: fmt.Sprintf(format, args...)}
}
