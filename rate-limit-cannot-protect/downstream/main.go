// Downstream service. The concurrency limit lives in the listener: at most N
// connections are accepted and not yet closed at any time. Connections over the
// limit are not accepted and wait in the kernel accept queue (listen backlog).
// The service keeps no queue of its own.
//
// Each request sleeps for the endpoint's processing time, answers 200 with
// "Connection: close" and the connection is closed at once (hijacked from net/http,
// see app). The sleep is not cancelled when the client goes away.
//
// Access log (ACCESS_LOG, one line per request, written after the response):
//
//	<start, unix seconds with microseconds> <processing time ms> <path> <X-Request-Id> <X-Tenant> <request proto> <Connection header or ->
//
// start is when the handler got the request (after accept and reading the request);
// processing time runs from start to the end of the response write.
//
// App port: APP_PORT (default 8081). One listener only: the Accept loop takes a slot
// before it blocks in accept(), so a second listener would hold a slot of its own
// while idle.
// Admin (separate listener, not limited): ADMIN_PORT (default 9001)
//
//	GET /admin                         current settings
//	GET /admin?limit=&delay_light=&delay_heavy=
//	GET /admin?reset=1                 back to the startup defaults
//	GET /admin?drain=1                 accept everything, answer at once (used between scenarios)
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"
)

type settings struct {
	Limit      int     `json:"limit"`
	DelayLight float64 `json:"delay_light"`
	DelayHeavy float64 `json:"delay_heavy"`
	Drain      bool    `json:"drain"`
}

// gate is a counting semaphore whose size can change at runtime.
type gate struct {
	mu     sync.Mutex
	cond   *sync.Cond
	active int
}

var (
	smu sync.Mutex
	cur settings
	g   = func() *gate { x := &gate{}; x.cond = sync.NewCond(&x.mu); return x }()
)

var (
	lmu  sync.Mutex
	alog *bufio.Writer
)

func accessLog(start time.Time, d time.Duration, r *http.Request) {
	if alog == nil {
		return
	}
	dash := func(v string) string {
		if v == "" {
			return "-"
		}
		return v
	}
	line := fmt.Sprintf("%d.%06d %.3f %s %s %s %s %s\n", start.Unix(), start.Nanosecond()/1000,
		float64(d.Microseconds())/1000, r.URL.Path, dash(r.Header.Get("X-Request-Id")),
		dash(r.Header.Get("X-Tenant")), r.Proto, dash(r.Header.Get("Connection")))
	lmu.Lock()
	alog.WriteString(line)
	lmu.Unlock()
}

func get() settings { smu.Lock(); defer smu.Unlock(); return cur }

func limit() int {
	s := get()
	if s.Drain {
		return 1 << 30
	}
	return s.Limit
}

func (g *gate) acquire() {
	g.mu.Lock()
	for g.active >= limit() {
		g.cond.Wait()
	}
	g.active++
	g.mu.Unlock()
}

func (g *gate) release() {
	g.mu.Lock()
	g.active--
	g.mu.Unlock()
	g.cond.Broadcast()
}

func (g *gate) wake() { g.mu.Lock(); g.mu.Unlock(); g.cond.Broadcast() }

// limitListener calls Accept only while the gate has room.
type limitListener struct{ net.Listener }

func (l limitListener) Accept() (net.Conn, error) {
	g.acquire()
	c, err := l.Listener.Accept()
	if err != nil {
		g.release()
		return nil, err
	}
	return &limitConn{Conn: c}, nil
}

type limitConn struct {
	net.Conn
	once sync.Once
}

func (c *limitConn) Close() error {
	err := c.Conn.Close()
	c.once.Do(g.release)
	return err
}

func envNum(k string, def float64) float64 {
	if v, err := strconv.ParseFloat(os.Getenv(k), 64); err == nil {
		return v
	}
	return def
}

func defaults() settings {
	return settings{Limit: int(envNum("LIMIT", 20)), DelayLight: envNum("DELAY_LIGHT", 50), DelayHeavy: envNum("DELAY_HEAVY", 300)}
}

func app(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	s := get()
	d := s.DelayLight
	if strings.HasPrefix(r.URL.Path, "/api/heavy") {
		d = s.DelayHeavy
	}
	if s.Drain {
		d = 0
	}
	// Take the connection over from net/http so that it is closed right after the
	// response. Letting net/http close it would add rstAvoidanceDelay (500 ms) while
	// the connection still holds a slot of the limit.
	conn, _, err := w.(http.Hijacker).Hijack()
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	time.Sleep(time.Duration(d * float64(time.Millisecond))) // not cancelled on client close
	fmt.Fprint(conn, "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 3\r\nConnection: close\r\n\r\nok\n")
	accessLog(start, time.Since(start), r)
	conn.Close()
}

func admin(w http.ResponseWriter, r *http.Request) {
	p := r.URL.Query()
	smu.Lock()
	if p.Has("reset") {
		cur = defaults()
	}
	if p.Has("drain") {
		cur.Drain = p.Get("drain") == "1"
	}
	if p.Has("limit") {
		v, _ := strconv.ParseFloat(p.Get("limit"), 64)
		cur.Limit = int(v)
	}
	if p.Has("delay_light") {
		cur.DelayLight, _ = strconv.ParseFloat(p.Get("delay_light"), 64)
	}
	if p.Has("delay_heavy") {
		cur.DelayHeavy, _ = strconv.ParseFloat(p.Get("delay_heavy"), 64)
	}
	s := cur
	smu.Unlock()
	g.wake()
	b, _ := json.Marshal(struct {
		TsMs int64 `json:"ts_ms"`
		settings
	}{time.Now().UnixMilli(), s})
	if r.URL.RawQuery != "" {
		log.Printf("admin ?%s -> %s", r.URL.RawQuery, b)
	}
	w.Header().Set("Content-Type", "application/json")
	w.Write(b)
}

func main() {
	log.SetFlags(0)
	log.SetPrefix("")
	cur = defaults()
	if p := os.Getenv("ACCESS_LOG"); p != "" {
		f, err := os.OpenFile(p, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
		if err != nil {
			log.Fatal(err)
		}
		alog = bufio.NewWriterSize(f, 1<<20)
		go func() {
			for range time.Tick(100 * time.Millisecond) {
				lmu.Lock()
				alog.Flush()
				lmu.Unlock()
			}
		}()
	}
	port := os.Getenv("APP_PORT")
	if port == "" {
		port = "8081"
	}
	ln, err := net.Listen("tcp", ":"+port) // backlog = net.core.somaxconn of this netns
	if err != nil {
		log.Fatal(err)
	}
	go func() { log.Fatal(http.Serve(limitListener{ln}, http.HandlerFunc(app))) }()
	adminPort := os.Getenv("ADMIN_PORT")
	if adminPort == "" {
		adminPort = "9001"
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/admin", admin)
	b, _ := json.Marshal(cur)
	log.Printf("%d start port=%s admin=%s %s", time.Now().UnixMilli(), port, adminPort, b)
	log.Fatal(http.ListenAndServe(":"+adminPort, mux))
}
