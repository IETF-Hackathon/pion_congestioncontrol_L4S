// Backend selection for interruptible monotonic pacing waits. The serialized
// RTP writer waits; feedback and controller shutdown may notify it concurrently.
package prague

import (
	"errors"
	"fmt"
)

var errPacerClosed = errors.New("Prague closed")

// pacingWaiter keeps the clock and absolute deadlines in the same time domain.
type pacingWaiter interface {
	NowNS() (int64, error)
	WaitUntilNS(int64) error
	Wake() error
	Close() error
	Name() string
}

// ValidatePacingBackend checks a backend name without allocating OS resources.
func ValidatePacingBackend(name string) error {
	switch name {
	case "auto", "portable", "linux-timerfd":
		return nil
	default:
		return fmt.Errorf("Prague pacing backend must be auto, portable or linux-timerfd")
	}
}

// newPacingWaiter selects an explicit backend or the platform's recorded default.
func newPacingWaiter(name string) (pacingWaiter, error) {
	if err := ValidatePacingBackend(name); err != nil {
		return nil, err
	}
	if name == "portable" {
		return newPortableWaiter(), nil
	}
	return newPlatformWaiter(name)
}
