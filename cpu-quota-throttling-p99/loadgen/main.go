package main

import (
	"bufio"
	"encoding/csv"
	"flag"
	"fmt"
	"io"
	"math/rand"
	"net"
	"os"
	"strconv"
	"sync"
	"time"
)

func clockOffset(addr string, n int) (offset, rtt int64, err error) {
	if addr == "" {
		return 0, 0, nil
	}
	c, err := net.Dial("tcp", addr)
	if err != nil {
		return 0, 0, err
	}
	defer c.Close()
	r := bufio.NewReader(c)
	rtt = 1 << 62
	for i := 0; i < n; i++ {
		t0 := time.Now().UnixNano()
		fmt.Fprint(c, "t\n")
		l, err := r.ReadString('\n')
		if err != nil {
			return 0, 0, err
		}
		t1 := time.Now().UnixNano()
		vm, _ := strconv.ParseInt(l[:len(l)-1], 10, 64)
		if t1-t0 < rtt {
			rtt, offset = t1-t0, vm-(t0+t1)/2
		}
	}
	return offset, rtt, nil
}

func main() {
	addr := flag.String("addr", "127.0.0.1:18080", "")
	syncAddr := flag.String("sync", "127.0.0.1:18081", "clock sync server on the target host; empty when loadgen runs on the same host")
	rps := flag.Float64("rps", 10, "")
	dur := flag.Duration("dur", 60*time.Second, "")
	mode := flag.String("mode", "poisson", "poisson|uniform")
	burst := flag.Int("burst", 1, "requests sent together per arrival event")
	seed := flag.Int64("seed", 1, "")
	out := flag.String("out", "requests.csv", "")
	flag.Parse()

	rng := rand.New(rand.NewSource(*seed))
	var sched []time.Duration
	for t := time.Duration(0); ; {
		if *mode == "uniform" {
			t += time.Duration(float64(time.Second) / (*rps / float64(*burst)))
		} else {
			t += time.Duration(rng.ExpFloat64() / (*rps / float64(*burst)) * float64(time.Second))
		}
		if t >= *dur {
			break
		}
		for b := 0; b < *burst; b++ {
			sched = append(sched, t)
		}
	}

	off0, rtt0, err := clockOffset(*syncAddr, 200)
	if err != nil {
		fmt.Fprintln(os.Stderr, "clock sync:", err)
		os.Exit(1)
	}

	req := []byte("GET / HTTP/1.1\r\nHost: cqt\r\nConnection: close\r\n\r\n")
	rows := make([][]string, len(sched))
	var wg sync.WaitGroup
	t0 := time.Now().Add(500 * time.Millisecond)
	for i, s := range sched {
		wg.Add(1)
		go func(i int, s time.Duration) {
			defer wg.Done()
			time.Sleep(time.Until(t0.Add(s)))
			send := time.Now()
			c, err := net.Dial("tcp", *addr)
			if err == nil {
				if _, err = c.Write(req); err == nil {
					var b []byte
					if b, err = io.ReadAll(c); err == nil && len(b) == 0 {
						err = fmt.Errorf("empty response")
					}
				}
				c.Close()
			}
			recv := time.Now()
			errs := ""
			if err != nil {
				errs = err.Error()
			}
			rows[i] = []string{strconv.Itoa(i), strconv.FormatInt(int64(s), 10), strconv.FormatInt(send.UnixNano(), 10), strconv.FormatInt(recv.UnixNano(), 10), errs}
		}(i, s)
	}
	wg.Wait()

	off1, rtt1, err := clockOffset(*syncAddr, 200)
	if err != nil {
		fmt.Fprintln(os.Stderr, "clock sync:", err)
		os.Exit(1)
	}
	f, _ := os.Create(*out)
	w := csv.NewWriter(f)
	w.Write([]string{"i", "sched_ns", "send_unix_ns", "recv_unix_ns", "err"})
	w.WriteAll(rows)
	w.Flush()
	f.Close()
	fmt.Fprintf(os.Stderr, "sent=%d t0=%d\nclock_offset_before_ns=%d\nrtt_before_ns=%d\nclock_offset_after_ns=%d\nrtt_after_ns=%d\n", len(sched), t0.UnixNano(), off0, rtt0, off1, rtt1)
}
