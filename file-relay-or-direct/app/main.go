package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/feature/cloudfront/sign"
	"github.com/aws/aws-sdk-go-v2/feature/s3/transfermanager"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

const (
	appAddr     = ":8080"
	monitorAddr = ":8081"
	allowHeader = "X-Allow"
	urlTTL      = 15 * time.Minute
)

func main() {
	if len(os.Args) < 2 {
		log.Fatal("usage: relay-bench serve|monitor")
	}
	switch os.Args[1] {
	case "serve":
		serve()
	case "monitor":
		monitor()
	default:
		log.Fatalf("unknown command %q", os.Args[1])
	}
}

func serve() {
	ctx := context.Background()
	bucket := os.Getenv("BUCKET")
	cfDomain := os.Getenv("CF_DOMAIN")
	cfKeyID := os.Getenv("CF_KEY_ID")

	cfg, err := config.LoadDefaultConfig(ctx)
	if err != nil {
		log.Fatal(err)
	}
	if _, err := cfg.Credentials.Retrieve(ctx); err != nil {
		log.Fatal(err)
	}
	client := s3.NewFromConfig(cfg)
	presigner := s3.NewPresignClient(client)
	tm := transfermanager.New(client)

	key, err := sign.LoadPEMPrivKeyFile(os.Getenv("CF_KEY_FILE"))
	if err != nil {
		log.Fatal(err)
	}
	cfSigner := sign.NewURLSigner(cfKeyID, key)

	allowed := func(w http.ResponseWriter, r *http.Request, objectKey string) bool {
		if strings.HasPrefix(objectKey, "private/") && r.Header.Get(allowHeader) != "1" {
			http.Error(w, "forbidden", http.StatusForbidden)
			return false
		}
		return true
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, "ok\n")
	})

	mux.HandleFunc("POST /upload", func(w http.ResponseWriter, r *http.Request) {
		objectKey := r.URL.Query().Get("key")
		f, _, err := r.FormFile("file")
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		defer f.Close()
		defer r.MultipartForm.RemoveAll()
		if _, err := tm.UploadObject(r.Context(), &transfermanager.UploadObjectInput{
			Bucket: aws.String(bucket),
			Key:    aws.String(objectKey),
			Body:   f,
		}); err != nil {
			http.Error(w, err.Error(), http.StatusBadGateway)
			return
		}
		io.WriteString(w, "ok\n")
	})

	mux.HandleFunc("GET /download", func(w http.ResponseWriter, r *http.Request) {
		objectKey := r.URL.Query().Get("key")
		if !allowed(w, r, objectKey) {
			return
		}
		out, err := client.GetObject(r.Context(), &s3.GetObjectInput{
			Bucket: aws.String(bucket),
			Key:    aws.String(objectKey),
		})
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadGateway)
			return
		}
		defer out.Body.Close()
		if out.ContentLength != nil {
			w.Header().Set("Content-Length", strconv.FormatInt(*out.ContentLength, 10))
		}
		w.Header().Set("Content-Type", aws.ToString(out.ContentType))
		io.Copy(w, out.Body)
	})

	mux.HandleFunc("GET /sign/s3-put", func(w http.ResponseWriter, r *http.Request) {
		req, err := presigner.PresignPutObject(r.Context(), &s3.PutObjectInput{
			Bucket: aws.String(bucket),
			Key:    aws.String(r.URL.Query().Get("key")),
		}, s3.WithPresignExpires(urlTTL))
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		io.WriteString(w, req.URL+"\n")
	})

	mux.HandleFunc("GET /sign/s3-get", func(w http.ResponseWriter, r *http.Request) {
		objectKey := r.URL.Query().Get("key")
		if !allowed(w, r, objectKey) {
			return
		}
		req, err := presigner.PresignGetObject(r.Context(), &s3.GetObjectInput{
			Bucket: aws.String(bucket),
			Key:    aws.String(objectKey),
		}, s3.WithPresignExpires(urlTTL))
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		io.WriteString(w, req.URL+"\n")
	})

	mux.HandleFunc("GET /sign/cf", func(w http.ResponseWriter, r *http.Request) {
		objectKey := r.URL.Query().Get("key")
		if !allowed(w, r, objectKey) {
			return
		}
		signed, err := cfSigner.Sign("https://"+cfDomain+"/"+objectKey, time.Now().Add(urlTTL))
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		io.WriteString(w, signed+"\n")
	})

	log.Fatal(http.ListenAndServe(appAddr, mux))
}

type procStats struct {
	cpuTicks int64
	rssKiB   int64
	hwmKiB   int64
}

func readProc(pid int) (procStats, error) {
	var s procStats
	stat, err := os.ReadFile(fmt.Sprintf("/proc/%d/stat", pid))
	if err != nil {
		return s, err
	}
	fields := strings.Fields(string(stat[bytes.LastIndexByte(stat, ')')+2:]))
	utime, _ := strconv.ParseInt(fields[11], 10, 64)
	stime, _ := strconv.ParseInt(fields[12], 10, 64)
	s.cpuTicks = utime + stime
	status, err := os.Open(fmt.Sprintf("/proc/%d/status", pid))
	if err != nil {
		return s, err
	}
	defer status.Close()
	sc := bufio.NewScanner(status)
	for sc.Scan() {
		f := strings.Fields(sc.Text())
		if len(f) < 2 {
			continue
		}
		v, _ := strconv.ParseInt(f[1], 10, 64)
		switch f[0] {
		case "VmRSS:":
			s.rssKiB = v
		case "VmHWM:":
			s.hwmKiB = v
		}
	}
	return s, nil
}

type run struct {
	mu        sync.Mutex
	cmd       *exec.Cmd
	start     procStats
	startedAt time.Time
	peakKiB   int64
	samples   int
	stop      chan struct{}
	done      chan struct{}
	logs      *tail
}

type tail struct {
	mu  sync.Mutex
	buf []byte
}

func (t *tail) Write(p []byte) (int, error) {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.buf = append(t.buf, p...)
	if len(t.buf) > 64<<10 {
		t.buf = t.buf[len(t.buf)-(64<<10):]
	}
	return len(p), nil
}

const clockTicksPerSecond = 100

func monitor() {
	self, err := os.Executable()
	if err != nil {
		log.Fatal(err)
	}
	r := &run{logs: &tail{}}

	startChild := func() error {
		if r.cmd != nil {
			r.cmd.Process.Signal(syscall.SIGTERM)
			r.cmd.Wait()
			r.cmd = nil
		}
		cmd := exec.Command(self, "serve")
		cmd.Stdout = r.logs
		cmd.Stderr = r.logs
		if err := cmd.Start(); err != nil {
			return err
		}
		r.cmd = cmd
		deadline := time.Now().Add(30 * time.Second)
		for time.Now().Before(deadline) {
			resp, err := http.Get("http://127.0.0.1" + appAddr + "/healthz")
			if err == nil {
				resp.Body.Close()
				if resp.StatusCode == http.StatusOK {
					return nil
				}
			}
			time.Sleep(50 * time.Millisecond)
		}
		return fmt.Errorf("serve did not become ready")
	}

	mux := http.NewServeMux()
	mux.HandleFunc("POST /start", func(w http.ResponseWriter, req *http.Request) {
		r.mu.Lock()
		defer r.mu.Unlock()
		if r.stop != nil {
			close(r.stop)
			<-r.done
			r.stop = nil
		}
		if err := startChild(); err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		time.Sleep(time.Second)
		pid := r.cmd.Process.Pid
		s, err := readProc(pid)
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		r.start, r.startedAt, r.peakKiB, r.samples = s, time.Now(), s.rssKiB, 1
		r.stop, r.done = make(chan struct{}), make(chan struct{})
		go func(stop, done chan struct{}) {
			defer close(done)
			t := time.NewTicker(100 * time.Millisecond)
			defer t.Stop()
			for {
				select {
				case <-stop:
					return
				case <-t.C:
					s, err := readProc(pid)
					if err != nil {
						continue
					}
					r.mu.Lock()
					r.samples++
					if s.rssKiB > r.peakKiB {
						r.peakKiB = s.rssKiB
					}
					r.mu.Unlock()
				}
			}
		}(r.stop, r.done)
		json.NewEncoder(w).Encode(map[string]any{"pid": pid, "rss_kib": s.rssKiB})
	})

	mux.HandleFunc("POST /stop", func(w http.ResponseWriter, req *http.Request) {
		r.mu.Lock()
		stop, done := r.stop, r.done
		r.stop = nil
		r.mu.Unlock()
		if stop == nil {
			http.Error(w, "not started", http.StatusConflict)
			return
		}
		close(stop)
		<-done
		r.mu.Lock()
		defer r.mu.Unlock()
		s, err := readProc(r.cmd.Process.Pid)
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		ticks := s.cpuTicks - r.start.cpuTicks
		json.NewEncoder(w).Encode(map[string]any{
			"cpu_ms":         ticks * 1000 / clockTicksPerSecond,
			"rss_start_kib":  r.start.rssKiB,
			"rss_peak_kib":   r.peakKiB,
			"vmhwm_kib":      s.hwmKiB,
			"samples":        r.samples,
			"elapsed_ms":     time.Since(r.startedAt).Milliseconds(),
			"rss_at_end_kib": s.rssKiB,
		})
	})

	mux.HandleFunc("GET /log", func(w http.ResponseWriter, req *http.Request) {
		r.logs.mu.Lock()
		defer r.logs.mu.Unlock()
		w.Write(r.logs.buf)
	})

	r.mu.Lock()
	if err := startChild(); err != nil {
		log.Fatal(err)
	}
	r.mu.Unlock()
	log.Fatal(http.ListenAndServe(monitorAddr, mux))
}
