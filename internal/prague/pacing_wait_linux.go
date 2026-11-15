//go:build linux

// Linux pacing uses reusable CLOCK_MONOTONIC timerfd deadlines and eventfd
// notifications. File descriptors are released only after active polls return.
package prague

import (
	"encoding/binary"
	"errors"
	"fmt"
	"sync"

	"golang.org/x/sys/unix"
)

// linuxWaiter owns one timer and one notification descriptor for a single writer.
type linuxWaiter struct {
	mu               sync.Mutex
	timerFD, eventFD int
	closed           bool
	active           sync.WaitGroup
	once             sync.Once
	closeErr         error
}

// newPlatformWaiter selects timerfd for Linux auto and explicit precise requests.
func newPlatformWaiter(_ string) (pacingWaiter, error) {
	timerFD, err := unix.TimerfdCreate(unix.CLOCK_MONOTONIC, unix.TFD_NONBLOCK|unix.TFD_CLOEXEC)
	if err != nil {
		return nil, fmt.Errorf("create Prague timerfd: %w", err)
	}
	eventFD, err := unix.Eventfd(0, unix.EFD_NONBLOCK|unix.EFD_CLOEXEC)
	if err != nil {
		return nil, errors.Join(fmt.Errorf("create Prague eventfd: %w", err), unix.Close(timerFD))
	}
	return &linuxWaiter{timerFD: timerFD, eventFD: eventFD}, nil
}

// Name reports the selected Linux implementation rather than its auto alias.
func (w *linuxWaiter) Name() string { return "linux-timerfd" }

// NowNS reads the same CLOCK_MONOTONIC domain used by absolute timerfd deadlines.
func (w *linuxWaiter) NowNS() (int64, error) {
	var stamp unix.Timespec
	if err := unix.ClockGettime(unix.CLOCK_MONOTONIC, &stamp); err != nil {
		return 0, fmt.Errorf("read Prague monotonic clock: %w", err)
	}
	return stamp.Nano(), nil
}

// readPacingCounter consumes one coalesced descriptor notification without blocking.
func readPacingCounter(fd int) error {
	var data [8]byte
	for {
		n, err := unix.Read(fd, data[:])
		if errors.Is(err, unix.EINTR) {
			continue
		}
		if errors.Is(err, unix.EAGAIN) {
			return nil
		}
		if err != nil {
			return fmt.Errorf("read Prague pacing event: %w", err)
		}
		if n != len(data) {
			return fmt.Errorf("short Prague pacing event: %d bytes", n)
		}
		return nil
	}
}

// WaitUntilNS polls a one-shot deadline and the notifier, preserving early wakes.
func (w *linuxWaiter) WaitUntilNS(deadlineNS int64) error {
	w.mu.Lock()
	if w.closed {
		w.mu.Unlock()
		return errPacerClosed
	}
	w.active.Add(1)
	timerFD, eventFD := w.timerFD, w.eventFD
	w.mu.Unlock()
	defer w.active.Done()
	if err := unix.TimerfdSettime(timerFD, unix.TFD_TIMER_ABSTIME, &unix.ItimerSpec{Value: unix.NsecToTimespec(max(deadlineNS, 1))}, nil); err != nil {
		return fmt.Errorf("arm Prague pacing deadline: %w", err)
	}
	fds := []unix.PollFd{{Fd: int32(timerFD), Events: unix.POLLIN}, {Fd: int32(eventFD), Events: unix.POLLIN}}
	for {
		if _, err := unix.Poll(fds, -1); err != nil {
			if errors.Is(err, unix.EINTR) {
				continue
			}
			return fmt.Errorf("poll Prague pacing events: %w", err)
		}
		for _, fd := range fds {
			if fd.Revents&(unix.POLLERR|unix.POLLHUP|unix.POLLNVAL) != 0 {
				return fmt.Errorf("Prague pacing descriptor failed: events=%#x", fd.Revents)
			}
			if fd.Revents&unix.POLLIN != 0 {
				if err := readPacingCounter(int(fd.Fd)); err != nil {
					return err
				}
			}
		}
		w.mu.Lock()
		closed := w.closed
		w.mu.Unlock()
		if closed {
			return errPacerClosed
		}
		return nil
	}
}

// signalLocked writes a coalesced wake while descriptor lifetime is protected.
func (w *linuxWaiter) signalLocked() error {
	var data [8]byte
	binary.NativeEndian.PutUint64(data[:], 1)
	for {
		n, err := unix.Write(w.eventFD, data[:])
		if errors.Is(err, unix.EINTR) {
			continue
		}
		if errors.Is(err, unix.EAGAIN) {
			return nil // A notification is already pending.
		}
		if err != nil {
			return fmt.Errorf("notify Prague pacer: %w", err)
		}
		if n != len(data) {
			return fmt.Errorf("short Prague pacing notification: %d bytes", n)
		}
		return nil
	}
}

// Wake interrupts polling without losing a notification sent before poll begins.
func (w *linuxWaiter) Wake() error {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.closed {
		return errPacerClosed
	}
	return w.signalLocked()
}

// Close marks closure, wakes polls, joins waits and closes both descriptors once.
func (w *linuxWaiter) Close() error {
	w.once.Do(func() {
		w.mu.Lock()
		w.closed = true
		err := w.signalLocked()
		w.mu.Unlock()
		w.active.Wait()
		w.closeErr = errors.Join(err, unix.Close(w.timerFD), unix.Close(w.eventFD))
	})
	return w.closeErr
}
