//go:build !linux

// Non-Linux senders use the portable backend and reject explicit Linux requests.
package prague

import "fmt"

// newPlatformWaiter implements auto selection without silently emulating timerfd.
func newPlatformWaiter(name string) (pacingWaiter, error) {
	if name == "linux-timerfd" {
		return nil, fmt.Errorf("linux-timerfd Prague pacing requires Linux")
	}
	return newPortableWaiter(), nil
}
