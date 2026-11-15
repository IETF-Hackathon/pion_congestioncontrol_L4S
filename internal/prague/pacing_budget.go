// Elapsed-time byte accounting for the Prague pacer. Fixed-point credit retains
// fractional bytes, while a native-derived cap bounds idle and catch-up bursts.
package prague

import "fmt"

const creditScale uint64 = 1_000_000_000
const maxCredit uint64 = 1<<63 - 1

// pacingBudget stores billionths of a byte against one monotonic clock domain.
type pacingBudget struct {
	initialized bool
	atNS        int64
	rate        uint64
	capacity    uint64
	credit      uint64
}

// ceilDivide rounds a nonnegative quotient up without overflowing the numerator.
func ceilDivide(n, d uint64) uint64 {
	q := n / d
	if n%d != 0 {
		q++
	}
	return q
}

// advance earns credit at the old rate, saturating before multiplying elapsed time.
func (b *pacingBudget) advance(nowNS int64) error {
	if nowNS < b.atNS {
		return fmt.Errorf("pacing clock moved backwards")
	}
	if b.credit < b.capacity {
		elapsed := uint64(nowNS - b.atNS)
		if elapsed >= ceilDivide(b.capacity-b.credit, b.rate) {
			b.credit = b.capacity
		} else {
			b.credit += elapsed * b.rate
		}
	}
	b.atNS = nowNS
	return nil
}

// configure settles the preceding rate before applying and clamping new limits.
func (b *pacingBudget) configure(nowNS int64, rate, burstBytes uint64) error {
	if nowNS < 0 || rate == 0 || burstBytes == 0 || burstBytes > maxCredit/creditScale {
		return fmt.Errorf("invalid Prague pacing budget")
	}
	capacity := burstBytes * creditScale
	if b.initialized {
		if err := b.advance(nowNS); err != nil {
			return err
		}
	} else {
		b.initialized, b.atNS, b.credit = true, nowNS, capacity
	}
	b.rate, b.capacity = rate, capacity
	if b.credit > capacity {
		b.credit = capacity
	}
	return nil
}

// delayNS returns the remaining credit wait for one whole accounted RTP packet.
func (b *pacingBudget) delayNS(packetBytes uint64) (int64, error) {
	if !b.initialized || packetBytes == 0 || packetBytes > b.capacity/creditScale {
		return 0, fmt.Errorf("packet exceeds Prague burst allowance")
	}
	cost := packetBytes * creditScale
	if b.credit >= cost {
		return 0, nil
	}
	return int64(ceilDivide(cost-b.credit, b.rate)), nil
}

// spend debits an already eligible packet without borrowing future credit.
func (b *pacingBudget) spend(packetBytes uint64) error {
	delay, err := b.delayNS(packetBytes)
	if err != nil {
		return err
	}
	if delay != 0 {
		return fmt.Errorf("Prague packet admitted without pacing credit")
	}
	b.credit -= packetBytes * creditScale
	return nil
}

// deadlineAfter adds a bounded wait without overflowing monotonic nanoseconds.
func deadlineAfter(nowNS, delayNS int64) (int64, error) {
	if nowNS < 0 || delayNS < 0 || uint64(delayNS) > maxCredit-uint64(nowNS) {
		return 0, fmt.Errorf("Prague pacing deadline overflow")
	}
	return nowNS + delayNS, nil
}
