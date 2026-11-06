package main

import (
	"log"
	"net/http"
	"os"
	"strconv"
	"time"
)

func envInt(name string) int {
	v, err := strconv.Atoi(os.Getenv(name))
	if err != nil {
		log.Fatalf("%s: %v", name, err)
	}
	return v
}

func main() {
	delay := time.Duration(envInt("DELAY_MS")) * time.Millisecond
	status := envInt("STATUS")
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(delay)
		w.WriteHeader(status)
	})
	log.Fatal(http.ListenAndServe(":8080", mux))
}
