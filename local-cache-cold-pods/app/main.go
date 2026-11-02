package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/hashicorp/golang-lru/v2/expirable"
	"github.com/jackc/pgx/v5/pgxpool"
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

type counters struct {
	req, hit, miss, dbStart, dbDone, dbErr, dup, errs atomic.Int64
	dbInflight                                        atomic.Int64
}

type server struct {
	pool     *pgxpool.Pool
	cache    *expirable.LRU[int, item]
	c        counters
	ready    atomic.Bool
	mu       sync.Mutex
	inflight map[int]int
}

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

func (s *server) fromDB(ctx context.Context, id int) (item, error) {
	s.c.dbStart.Add(1)
	s.c.dbInflight.Add(1)
	defer s.c.dbInflight.Add(-1)
	var it item
	err := s.pool.QueryRow(ctx, query, id).Scan(&it.ID, &it.Name, &it.Reviews, &it.Score, &it.Countries)
	if err != nil {
		s.c.dbErr.Add(1)
		return it, err
	}
	s.c.dbDone.Add(1)
	return it, nil
}

func (s *server) load(ctx context.Context, id int) (item, error) {
	s.mu.Lock()
	if s.inflight[id] > 0 {
		s.c.dup.Add(1)
	}
	s.inflight[id]++
	s.mu.Unlock()
	defer func() {
		s.mu.Lock()
		s.inflight[id]--
		if s.inflight[id] == 0 {
			delete(s.inflight, id)
		}
		s.mu.Unlock()
	}()
	return s.fromDB(ctx, id)
}

func (s *server) handleItem(w http.ResponseWriter, r *http.Request) {
	s.c.req.Add(1)
	id, err := strconv.Atoi(r.URL.Query().Get("id"))
	if err != nil {
		s.c.errs.Add(1)
		http.Error(w, "bad id", http.StatusBadRequest)
		return
	}
	it, ok := s.cache.Get(id)
	if ok {
		s.c.hit.Add(1)
	} else {
		s.c.miss.Add(1)
		it, err = s.load(r.Context(), id)
		if err != nil {
			s.c.errs.Add(1)
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		s.cache.Add(id, it)
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

func (s *server) statsLoop(path, pod string, start time.Time) {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		log.Fatalf("stats file: %v", err)
	}
	enc := json.NewEncoder(f)
	for {
		now := time.Now()
		time.Sleep(now.Truncate(time.Second).Add(time.Second).Sub(now))
		t := time.Now()
		st := s.pool.Stat()
		enc.Encode(map[string]any{
			"t":                  float64(t.UnixNano()) / 1e9,
			"pod":                pod,
			"start":              float64(start.UnixNano()) / 1e9,
			"ready":              s.ready.Load(),
			"req":                s.c.req.Load(),
			"hit":                s.c.hit.Load(),
			"miss":               s.c.miss.Load(),
			"db_start":           s.c.dbStart.Load(),
			"db_done":            s.c.dbDone.Load(),
			"db_err":             s.c.dbErr.Load(),
			"db_inflight":        s.c.dbInflight.Load(),
			"dup":                s.c.dup.Load(),
			"errs":               s.c.errs.Load(),
			"cache_len":          s.cache.Len(),
			"pool_max":           st.MaxConns(),
			"pool_total":         st.TotalConns(),
			"pool_acquired":      st.AcquiredConns(),
			"pool_idle":          st.IdleConns(),
			"pool_acquire_count": st.AcquireCount(),
			"pool_acquire_ns":    st.AcquireDuration().Nanoseconds(),
			"pool_empty_acquire": st.EmptyAcquireCount(),
			"pool_empty_wait_ns": st.EmptyAcquireWaitTime().Nanoseconds(),
		})
	}
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
		pool:     pool,
		cache:    expirable.NewLRU[int, item](envInt("CACHE_SIZE", 1000), nil, time.Duration(envInt("CACHE_TTL_SECONDS", 600))*time.Second),
		inflight: map[int]int{},
	}
	if dir := os.Getenv("STATS_DIR"); dir != "" {
		go s.statsLoop(dir+"/"+pod+".jsonl", pod, start)
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
