package main

import (
	"encoding/json"
	"io"
	"net/http"
	"os"
	"time"
)

type reply struct {
	Pod    string `json:"pod"`
	User   string `json:"user"`
	ConnID int64  `json:"conn_id"`
}

type record struct {
	T      string  `json:"t"`
	OK     bool    `json:"ok"`
	Status int     `json:"status"`
	Ms     float64 `json:"ms"`
	Pod    string  `json:"pod,omitempty"`
	User   string  `json:"user,omitempty"`
	ConnID int64   `json:"conn_id,omitempty"`
	Err    string  `json:"err,omitempty"`
}

func main() {
	target := os.Getenv("TARGET_URL")
	interval, err := time.ParseDuration(os.Getenv("INTERVAL"))
	if err != nil {
		interval = 200 * time.Millisecond
	}
	client := &http.Client{Timeout: 3 * time.Second, Transport: &http.Transport{DisableKeepAlives: true}}
	out := json.NewEncoder(os.Stdout)
	for range time.Tick(interval) {
		start := time.Now()
		rec := record{T: start.UTC().Format(time.RFC3339Nano)}
		resp, err := client.Get(target)
		if err != nil {
			rec.Err = err.Error()
		} else {
			body, _ := io.ReadAll(resp.Body)
			resp.Body.Close()
			rec.Status = resp.StatusCode
			var r reply
			if resp.StatusCode == http.StatusOK && json.Unmarshal(body, &r) == nil {
				rec.OK = true
				rec.Pod, rec.User, rec.ConnID = r.Pod, r.User, r.ConnID
			} else {
				rec.Err = string(body)
			}
		}
		rec.Ms = float64(time.Since(start).Microseconds()) / 1000
		out.Encode(rec)
	}
}
