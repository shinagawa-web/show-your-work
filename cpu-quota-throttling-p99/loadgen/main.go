package main

import (
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

func main() {
	addr := flag.String("addr", "127.0.0.1:18080", "")
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
			rows[i] = []string{strconv.Itoa(i), strconv.FormatInt(int64(s), 10), strconv.FormatInt(send.UnixNano(), 10), strconv.FormatInt(recv.UnixNano(), 10), strconv.FormatInt(recv.Sub(send).Nanoseconds(), 10), errs}
		}(i, s)
	}
	wg.Wait()

	f, _ := os.Create(*out)
	w := csv.NewWriter(f)
	w.Write([]string{"i", "sched_ns", "send_unix_ns", "recv_unix_ns", "latency_ns", "err"})
	w.WriteAll(rows)
	w.Flush()
	f.Close()
	fmt.Fprintf(os.Stderr, "sent=%d t0=%d\n", len(sched), t0.UnixNano())
}
