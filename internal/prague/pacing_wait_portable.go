// Portable pacing waits reuse one Go timer. Their achieved precision depends on
// the runtime and OS and is reported separately from the Linux timerfd backend.
package prague

import (
	"sync"
	"time"
)

// portableWaiter owns a reusable timer and coalesced wake/close notifications.
type portableWaiter struct {
	origin time.Time
	mu     sync.Mutex
	timer  *time.Timer
	wake   chan struct{}
	done   chan struct{}
	closed bool
	active sync.WaitGroup
	once   sync.Once
}

// newPortableWaiter creates an idle timer without starting a worker goroutine.
func newPortableWaiter() *portableWaiter {
	timer := time.NewTimer(time.Hour)
	timer.Stop()
	return &portableWaiter{origin: time.Now(), timer: timer, wake: make(chan struct{}, 1), done: make(chan struct{})}
}

// Name identifies the timing backend in controller statistics and run metadata.
func (w *portableWaiter) Name() string { return "portable" }

// NowNS returns monotonic elapsed nanoseconds from this backend's private origin.
func (w *portableWaiter) NowNS() (int64, error) { return time.Since(w.origin).Nanoseconds(), nil }

// stopTimerLocked drains an old notification before resetting the shared timer.
func (w *portableWaiter) stopTimerLocked() {
	if !w.timer.Stop() {
		select {
		case <-w.timer.C:
		default:
		}
	}
}

// WaitUntilNS waits for a deadline or state change; it never grants packet credit.
func (w *portableWaiter) WaitUntilNS(deadlineNS int64) error {
	w.mu.Lock()
	if w.closed {
		w.mu.Unlock()
		return errPacerClosed
	}
	w.active.Add(1)
	w.stopTimerLocked()
	w.timer.Reset(time.Duration(max(deadlineNS-time.Since(w.origin).Nanoseconds(), 0)))
	w.mu.Unlock()
	defer w.active.Done()
	defer func() {
		w.mu.Lock()
		w.stopTimerLocked()
		w.mu.Unlock()
	}()
	select {
	case <-w.done:
		return errPacerClosed
	case <-w.wake:
	case <-w.timer.C:
	}
	w.mu.Lock()
	closed := w.closed
	w.mu.Unlock()
	if closed {
		return errPacerClosed
	}
	return nil
}

// Wake preserves a notification even when feedback arrives before the wait starts.
func (w *portableWaiter) Wake() error {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.closed {
		return errPacerClosed
	}
	select {
	case w.wake <- struct{}{}:
	default:
	}
	return nil
}

// Close wakes and joins active waits before releasing the timer, exactly once.
func (w *portableWaiter) Close() error {
	w.once.Do(func() {
		w.mu.Lock()
		w.closed = true
		close(w.done)
		w.mu.Unlock()
		w.active.Wait()
		w.mu.Lock()
		w.stopTimerLocked()
		w.mu.Unlock()
	})
	return nil
}
