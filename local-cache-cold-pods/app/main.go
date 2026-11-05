package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	crand "crypto/rand"

	"github.com/hashicorp/golang-lru/v2/expirable"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

type item struct {
	ID        int     `json:"id"`
	Name      string  `json:"name"`
	Reviews   int64   `json:"reviews"`
	Score     float64 `json:"score"`
	Countries int64   `json:"countries"`
}

const query = `SELECT i.id, i.name, count(r.id), avg(r.score)::float8, count(DISTINCT u.country)
FROM items i JOIN reviews r ON r.item_id = i.id JOIN users u ON u.id = r.user_id
WHERE i.id = $1
GROUP BY i.id, i.name`

const headerBytes = 256

type errCounts struct {
	loading, refused, timeout, readonly, other atomic.Int64
}

func (e *errCounts) add(kind string) {
	switch kind {
	case "loading":
		e.loading.Add(1)
	case "refused":
		e.refused.Add(1)
	case "timeout":
		e.timeout.Add(1)
	case "readonly":
		e.readonly.Add(1)
	default:
		e.other.Add(1)
	}
}

func (e *errCounts) snapshot() map[string]int64 {
	return map[string]int64{
		"loading":  e.loading.Load(),
		"refused":  e.refused.Load(),
		"timeout":  e.timeout.Load(),
		"readonly": e.readonly.Load(),
		"other":    e.other.Load(),
	}
}

type counters struct {
	req, localHit, redisHit, redisAbsent, redisFailed, miss atomic.Int64
	dbStart, dbInflight, redisSetOK                         atomic.Int64
	getErr, setErr                                          errCounts
}

type server struct {
	pool    *pgxpool.Pool
	local   *expirable.LRU[int, item]
	rdb     redis.UniversalClient
	ttl     time.Duration
	read    int
	values  sync.Pool
	c       counters
	ready   atomic.Bool
	otherMu sync.Mutex
	other   map[string]int64

	eventsMu sync.Mutex
	events   *json.Encoder
}

var addrPattern = regexp.MustCompile(`\d+\.\d+\.\d+\.\d+:\d+`)

func envInt(name string, def int) int {
	if v := os.Getenv(name); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil {
			log.Fatalf("%s: %v", name, err)
		}
		return n
	}
	return def
}

func classify(err error) string {
	msg := err.Error()
	var nerr net.Error
	switch {
	case strings.HasPrefix(msg, "LOADING"):
		return "loading"
	case strings.HasPrefix(msg, "READONLY"):
		return "readonly"
	case errors.Is(err, syscall.ECONNREFUSED), strings.Contains(msg, "connection refused"):
		return "refused"
	case errors.Is(err, redis.ErrPoolTimeout), errors.Is(err, context.DeadlineExceeded), errors.As(err, &nerr) && nerr.Timeout():
		return "timeout"
	}
	return "other"
}

func (s *server) noteErr(counts *errCounts, err error) {
	kind := classify(err)
	counts.add(kind)
	if kind == "other" {
		msg := addrPattern.ReplaceAllString(err.Error(), "ADDR")
		s.otherMu.Lock()
		s.other[msg]++
		s.otherMu.Unlock()
	}
}

func now() float64 {
	return float64(time.Now().UnixNano()) / 1e9
}

func (s *server) emit(ev map[string]any) {
	s.eventsMu.Lock()
	defer s.eventsMu.Unlock()
	if s.events != nil {
		s.events.Encode(ev)
	}
}

func (s *server) fromDB(ctx context.Context, id int) (item, error) {
	s.c.dbStart.Add(1)
	s.c.dbInflight.Add(1)
	defer s.c.dbInflight.Add(-1)
	var it item
	err := s.pool.QueryRow(ctx, query, id).Scan(&it.ID, &it.Name, &it.Reviews, &it.Score, &it.Countries)
	return it, err
}

func key(id int) string {
	return "item:" + strconv.Itoa(id)
}

func (s *server) fromRedis(ctx context.Context, id int) (item, bool) {
	var it item
	head, err := s.rdb.GetRange(ctx, key(id), 0, int64(s.read-1)).Bytes()
	if err != nil && !errors.Is(err, redis.Nil) {
		s.noteErr(&s.c.getErr, err)
		s.c.redisFailed.Add(1)
		return it, false
	}
	if len(head) == 0 {
		s.c.redisAbsent.Add(1)
		return it, false
	}
	if err := json.Unmarshal(head[:min(len(head), headerBytes)], &it); err != nil {
		log.Printf("bad value for %d: %v", id, err)
		s.c.redisAbsent.Add(1)
		return it, false
	}
	s.c.redisHit.Add(1)
	return it, true
}

func (s *server) toRedis(ctx context.Context, it item) {
	v := s.values.Get().(*[]byte)
	defer s.values.Put(v)
	b := *v
	for i := 0; i < headerBytes; i++ {
		b[i] = ' '
	}
	if enc, err := json.Marshal(it); err == nil {
		copy(b, enc)
	}
	if err := s.rdb.Set(ctx, key(it.ID), b, s.ttl).Err(); err != nil {
		s.noteErr(&s.c.setErr, err)
		return
	}
	s.c.redisSetOK.Add(1)
}

func (s *server) get(ctx context.Context, id int) (item, error) {
	if s.local != nil {
		if it, ok := s.local.Get(id); ok {
			s.c.localHit.Add(1)
			return it, nil
		}
	}
	if s.rdb != nil {
		if it, ok := s.fromRedis(ctx, id); ok {
			if s.local != nil {
				s.local.Add(id, it)
			}
			return it, nil
		}
	}
	s.c.miss.Add(1)
	it, err := s.fromDB(ctx, id)
	if err != nil {
		return it, err
	}
	if s.rdb != nil {
		s.toRedis(ctx, it)
	}
	if s.local != nil {
		s.local.Add(id, it)
	}
	return it, nil
}

func (s *server) handleItem(w http.ResponseWriter, r *http.Request) {
	s.c.req.Add(1)
	id, err := strconv.Atoi(r.URL.Query().Get("id"))
	if err != nil {
		http.Error(w, "bad id", http.StatusBadRequest)
		return
	}
	it, err := s.get(r.Context(), id)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(it)
}

func (s *server) handleReady(w http.ResponseWriter, r *http.Request) {
	if !s.ready.Load() {
		http.Error(w, "not ready", http.StatusServiceUnavailable)
		return
	}
	fmt.Fprintln(w, "ok")
}

func (s *server) otherSnapshot() map[string]int64 {
	s.otherMu.Lock()
	defer s.otherMu.Unlock()
	out := make(map[string]int64, len(s.other))
	for k, v := range s.other {
		out[k] = v
	}
	return out
}

func (s *server) probeLoop(client redis.UniversalClient) {
	down := false
	lastOK := now()
	for {
		ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
		err := client.GetRange(ctx, "probe", 0, 0).Err()
		cancel()
		t := now()
		if err != nil && !errors.Is(err, redis.Nil) {
			if !down {
				down = true
				s.emit(map[string]any{"ev": "probe_down", "t": t, "last_ok": lastOK, "kind": classify(err), "err": addrPattern.ReplaceAllString(err.Error(), "ADDR")})
			}
		} else {
			if down {
				down = false
				s.emit(map[string]any{"ev": "probe_up", "t": t})
			}
			lastOK = t
		}
		time.Sleep(100 * time.Millisecond)
	}
}

func (s *server) statsLoop(path string) {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		log.Fatalf("stats file: %v", err)
	}
	enc := json.NewEncoder(f)
	for {
		now := time.Now()
		time.Sleep(now.Truncate(time.Second).Add(time.Second).Sub(now))
		t := time.Now()
		enc.Encode(map[string]any{
			"t":            float64(t.UnixNano()) / 1e9,
			"req":          s.c.req.Load(),
			"local_hit":    s.c.localHit.Load(),
			"redis_hit":    s.c.redisHit.Load(),
			"redis_absent": s.c.redisAbsent.Load(),
			"redis_failed": s.c.redisFailed.Load(),
			"redis_set_ok": s.c.redisSetOK.Load(),
			"miss":         s.c.miss.Load(),
			"db_start":     s.c.dbStart.Load(),
			"db_inflight":  s.c.dbInflight.Load(),
			"get_err":      s.c.getErr.snapshot(),
			"set_err":      s.c.setErr.snapshot(),
			"other_err":    s.otherSnapshot(),
		})
	}
}

func newRedis() redis.UniversalClient {
	switch os.Getenv("REDIS_MODE") {
	case "single":
		opt := &redis.Options{Addr: os.Getenv("REDIS_ADDR")}
		if os.Getenv("GO_REDIS") == "short" {
			opt.DialTimeout = 500 * time.Millisecond
			opt.DialerRetries = 1
			opt.ReadTimeout = 500 * time.Millisecond
			opt.WriteTimeout = 500 * time.Millisecond
			opt.PoolTimeout = 500 * time.Millisecond
			opt.MaxRetries = 1
		}
		return redis.NewClient(opt)
	case "sentinel":
		return redis.NewFailoverClient(&redis.FailoverOptions{
			MasterName:    os.Getenv("SENTINEL_MASTER"),
			SentinelAddrs: strings.Split(os.Getenv("SENTINEL_ADDRS"), ","),
		})
	}
	return nil
}

func newProbe() redis.UniversalClient {
	const limit = 300 * time.Millisecond
	switch os.Getenv("REDIS_MODE") {
	case "single":
		return redis.NewClient(&redis.Options{Addr: os.Getenv("REDIS_ADDR"), PoolSize: 1, MaxRetries: -1,
			DialTimeout: limit, ReadTimeout: limit, WriteTimeout: limit})
	case "sentinel":
		return redis.NewFailoverClient(&redis.FailoverOptions{
			MasterName:    os.Getenv("SENTINEL_MASTER"),
			SentinelAddrs: strings.Split(os.Getenv("SENTINEL_ADDRS"), ","),
			PoolSize:      1, MaxRetries: -1, DialTimeout: limit, ReadTimeout: limit, WriteTimeout: limit,
		})
	}
	return nil
}

func main() {
	start := time.Now()
	pod := os.Getenv("POD_NAME")
	cfg, err := pgxpool.ParseConfig(os.Getenv("DATABASE_URL"))
	if err != nil {
		log.Fatal(err)
	}
	cfg.ConnConfig.ConnectTimeout = 2 * time.Second
	poolMax := envInt("POOL_MAX", 4)
	cfg.MaxConns = int32(poolMax)
	cfg.MinConns = int32(poolMax)
	pool, err := pgxpool.NewWithConfig(context.Background(), cfg)
	if err != nil {
		log.Fatal(err)
	}
	s := &server{
		pool:  pool,
		rdb:   newRedis(),
		ttl:   time.Duration(envInt("REDIS_TTL_SECONDS", 600)) * time.Second,
		read:  envInt("READ_BYTES", headerBytes),
		other: map[string]int64{},
	}
	if size := envInt("LOCAL_SIZE", 0); size > 0 {
		s.local = expirable.NewLRU[int, item](size, nil, time.Duration(envInt("LOCAL_TTL_SECONDS", 300))*time.Second)
	}
	if s.rdb != nil {
		pad := make([]byte, envInt("VALUE_BYTES", 2097152))
		if _, err := crand.Read(pad); err != nil {
			log.Fatal(err)
		}
		s.values.New = func() any {
			b := append([]byte(nil), pad...)
			return &b
		}
	}
	if dir := os.Getenv("STATS_DIR"); dir != "" {
		if s.rdb != nil {
			f, err := os.OpenFile(dir+"/"+pod+".events", os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
			if err != nil {
				log.Fatalf("events file: %v", err)
			}
			s.events = json.NewEncoder(f)
		}
		go s.statsLoop(dir + "/" + pod + ".jsonl")
		if s.rdb != nil {
			go s.probeLoop(newProbe())
		}
	}
	go func() {
		for {
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			conns := make([]*pgxpool.Conn, 0, poolMax)
			var err error
			for i := 0; i < poolMax && err == nil; i++ {
				var c *pgxpool.Conn
				c, err = pool.Acquire(ctx)
				if err == nil {
					conns = append(conns, c)
				}
			}
			for _, c := range conns {
				c.Release()
			}
			cancel()
			if err == nil {
				s.ready.Store(true)
				log.Printf("ready after %s with %d connections", time.Since(start), len(conns))
				return
			}
			log.Printf("not ready: %v", err)
			time.Sleep(500 * time.Millisecond)
		}
	}()
	mux := http.NewServeMux()
	mux.HandleFunc("/item", s.handleItem)
	mux.HandleFunc("/ready", s.handleReady)
	srv := &http.Server{Addr: ":8080", Handler: mux}
	go func() {
		sig := make(chan os.Signal, 1)
		signal.Notify(sig, syscall.SIGTERM, syscall.SIGINT)
		<-sig
		log.Printf("SIGTERM")
		time.Sleep(2 * time.Second)
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		srv.Shutdown(ctx)
	}()
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatal(err)
	}
}
