package main

import (
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
)

func main() {
	addr := flag.String("http", ":3000", "HTTP listen address")
	exit := flag.Bool("exit", false, "exit immediately with status 1")
	flag.Parse()
	if *exit {
		log.Print("exiting on purpose")
		os.Exit(1)
	}
	if b, err := os.ReadFile("/config/app.json"); err == nil {
		log.Printf("config: %s", b)
	}
	http.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintln(w, "ok")
	})
	log.Printf("listening on %s", *addr)
	log.Fatal(http.ListenAndServe(*addr, nil))
}
