package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

var ports = []string{"8081"}

func run(name string, args ...string) []string {
	out, err := exec.Command(name, args...).Output()
	if err != nil {
		log.Println(name, err)
	}
	var lines []string
	sc := bufio.NewScanner(bytes.NewReader(out))
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	for sc.Scan() {
		lines = append(lines, sc.Text())
	}
	return lines
}

func localPort(fields []string) string {
	for _, f := range fields {
		if i := strings.LastIndex(f, ":"); i > 0 && !strings.HasPrefix(f, "users:") {
			if _, err := strconv.Atoi(f[i+1:]); err == nil {
				return f[i+1:]
			}
		}
	}
	return ""
}

func isApp(p string) bool {
	for _, x := range ports {
		if p == x {
			return true
		}
	}
	return false
}

type settings struct {
	Limit      float64 `json:"limit"`
	DelayLight float64 `json:"delay_light"`
	DelayHeavy float64 `json:"delay_heavy"`
	Drain      bool    `json:"drain"`
}

func main() {
	out := os.Getenv("OUT")
	if out == "" {
		out = "/results/kernel_100ms.csv"
	}
	every := 100
	if v, err := strconv.Atoi(os.Getenv("SAMPLE_MS")); err == nil {
		every = v
	}
	f, err := os.Create(out)
	if err != nil {
		log.Fatal(err)
	}
	cols := []string{"ts_ms", "limit", "delay_light", "delay_heavy", "drain"}
	for _, p := range ports {
		cols = append(cols, "recvq_"+p, "sendq_"+p)
	}
	cols = append(cols, "recvq_total", "inuse", "queued_est", "listen_overflows", "listen_drops", "sample_ms")
	fmt.Fprintln(f, strings.Join(cols, ","))
	client := &http.Client{Timeout: 50 * time.Millisecond}

	for range time.Tick(time.Duration(every) * time.Millisecond) {
		t0 := time.Now()
		var s settings
		if resp, err := client.Get("http://127.0.0.1:9001/admin"); err == nil {
			json.NewDecoder(resp.Body).Decode(&s)
			resp.Body.Close()
		}
		recvq, sendq := map[string]int{}, map[string]int{}
		for _, l := range run("ss", "-ltnH") {
			fl := strings.Fields(l)
			if len(fl) < 4 || !isApp(localPort(fl[3:4])) {
				continue
			}
			p := localPort(fl[3:4])
			recvq[p], _ = strconv.Atoi(fl[1])
			sendq[p], _ = strconv.Atoi(fl[2])
		}
		inuse, queued := 0, 0
		for _, l := range run("ss", "-tnpH", "state", "established", "state", "close-wait") {
			fl := strings.Fields(l)
			if len(fl) < 5 || !isApp(localPort(fl[3:4])) {
				continue
			}
			if strings.Contains(l, "users:") {
				inuse++
			} else {
				queued++
			}
		}
		nst := map[string]string{"TcpExtListenOverflows": "0", "TcpExtListenDrops": "0"}
		for _, l := range run("nstat", "-asz", "TcpExtListenOverflows", "TcpExtListenDrops") {
			fl := strings.Fields(l)
			if len(fl) >= 2 {
				if _, ok := nst[fl[0]]; ok {
					nst[fl[0]] = fl[1]
				}
			}
		}
		drain := 0
		if s.Drain {
			drain = 1
		}
		row := []string{strconv.FormatInt(t0.UnixMilli(), 10), strconv.FormatFloat(s.Limit, 'f', -1, 64),
			strconv.FormatFloat(s.DelayLight, 'f', -1, 64), strconv.FormatFloat(s.DelayHeavy, 'f', -1, 64), strconv.Itoa(drain)}
		tot := 0
		for _, p := range ports {
			row = append(row, strconv.Itoa(recvq[p]), strconv.Itoa(sendq[p]))
			tot += recvq[p]
		}
		row = append(row, strconv.Itoa(tot), strconv.Itoa(inuse), strconv.Itoa(queued),
			nst["TcpExtListenOverflows"], nst["TcpExtListenDrops"], strconv.FormatInt(time.Since(t0).Milliseconds(), 10))
		fmt.Fprintln(f, strings.Join(row, ","))
	}
}
