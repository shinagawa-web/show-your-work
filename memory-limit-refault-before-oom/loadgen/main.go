package main

import (
	"bufio"
	"encoding/csv"
	"flag"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

func main() {
	addr := flag.String("addr", "127.0.0.1:8000", "")
	path := flag.String("path", "/", "")
	rps := flag.Float64("rps", 200, "")
	dur := flag.Duration("dur", 30*time.Second, "")
	timeout := flag.Duration("timeout", 5*time.Second, "")
	out := flag.String("out", "requests.csv", "")
	flag.Parse()

	n := int(dur.Seconds() * *rps)
	interval := time.Duration(float64(time.Second) / *rps)
	req := []byte("GET " + *path + " HTTP/1.1\r\nHost: app\r\nConnection: close\r\n\r\n")

	type row struct {
		sched, send, recv int64
		status            int
		timeout           bool
		err               string
	}
	rows := make([]row, n)
	var stop atomic.Bool
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGTERM, syscall.SIGINT)
	go func() { <-sig; stop.Store(true) }()

	t0 := time.Now().Add(500 * time.Millisecond)
	var wg sync.WaitGroup
	launched := 0
	for i := 0; i < n; i++ {
		at := t0.Add(time.Duration(i) * interval)
		time.Sleep(time.Until(at))
		if stop.Load() {
			break
		}
		launched++
		wg.Add(1)
		go func(i int, at time.Time) {
			defer wg.Done()
			r := row{sched: at.UnixNano()}
			r.send = time.Now().UnixNano()
			deadline := time.Now().Add(*timeout)
			c, err := net.DialTimeout("tcp", *addr, *timeout)
			if err == nil {
				c.SetDeadline(deadline)
				if _, err = c.Write(req); err == nil {
					var resp *http.Response
					resp, err = http.ReadResponse(bufio.NewReader(c), nil)
					if err == nil {
						r.status = resp.StatusCode
						_, err = bufio.NewReader(resp.Body).WriteTo(discard{})
						resp.Body.Close()
					}
				}
				c.Close()
			}
			r.recv = time.Now().UnixNano()
			if err != nil {
				if ne, ok := err.(net.Error); ok && ne.Timeout() {
					r.timeout = true
				}
				r.err = err.Error()
			}
			rows[i] = r
		}(i, at)
	}
	wg.Wait()

	f, err := os.Create(*out)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	w := csv.NewWriter(f)
	w.Write([]string{"i", "sched_unix_ns", "send_unix_ns", "recv_unix_ns", "status", "timeout", "err"})
	for i := 0; i < launched; i++ {
		r := rows[i]
		w.Write([]string{strconv.Itoa(i), strconv.FormatInt(r.sched, 10), strconv.FormatInt(r.send, 10), strconv.FormatInt(r.recv, 10), strconv.Itoa(r.status), strconv.FormatBool(r.timeout), r.err})
	}
	w.Flush()
	f.Close()
	fmt.Fprintf(os.Stderr, "planned=%d launched=%d t0_unix_ns=%d interval_ns=%d\n", n, launched, t0.UnixNano(), interval.Nanoseconds())
}

type discard struct{}

func (discard) Write(p []byte) (int, error) { return len(p), nil }
