package main

import (
	"fmt"
	"net"
	"net/http"
	"os"
	"runtime"
	"strconv"
	"sync/atomic"
	"time"
)

var (
	sem      = make(chan struct{}, 4)
	arrived  atomic.Int64
	done     atomic.Int64
	waiting  atomic.Int64
	inflight atomic.Int64
	conns    atomic.Int64
)

func work(d time.Duration) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		arrived.Add(1)
		inflight.Add(1)
		waiting.Add(1)
		sem <- struct{}{}
		waiting.Add(-1)
		time.Sleep(d)
		<-sem
		done.Add(1)
		inflight.Add(-1)
		w.Header().Set("Content-Type", "text/plain")
		w.Write([]byte("ok\n"))
	}
}

func healthz(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/plain")
	w.Write([]byte("ok\n"))
}

func connState(c net.Conn, s http.ConnState) {
	switch s {
	case http.StateNew:
		conns.Add(1)
	case http.StateClosed, http.StateHijacked:
		conns.Add(-1)
	}
}

func stats() {
	prev := time.Now()
	for {
		time.Sleep(time.Second - time.Duration(time.Now().UnixNano()%int64(time.Second)))
		now := time.Now()
		fmt.Printf("stat %.3f %.3f %d %d %d %d %d goroutines=%d conns=%d\n",
			float64(prev.UnixNano())/1e9, float64(now.UnixNano())/1e9, os.Getpid(),
			arrived.Swap(0), done.Swap(0), waiting.Load(), inflight.Load(), runtime.NumGoroutine(), conns.Load())
		prev = now
	}
}

func main() {
	ms, err := strconv.ParseFloat(os.Getenv("WORK_MS"), 64)
	if err != nil {
		ms = 20
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/work", work(time.Duration(ms*float64(time.Millisecond))))
	mux.HandleFunc("/healthz", healthz)
	srv := &http.Server{Addr: "0.0.0.0:8080", Handler: mux, ConnState: connState}
	go stats()
	srv.ListenAndServe()
}
