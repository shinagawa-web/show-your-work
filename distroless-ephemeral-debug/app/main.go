package main

import (
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"strconv"
	"sync"
	"syscall"
)

var (
	mu   sync.Mutex
	held []any
)

func stuckListener(port, backlog int) {
	fd, err := syscall.Socket(syscall.AF_INET, syscall.SOCK_STREAM, 0)
	if err != nil {
		log.Fatal(err)
	}
	if err := syscall.SetsockoptInt(fd, syscall.SOL_SOCKET, syscall.SO_REUSEADDR, 1); err != nil {
		log.Fatal(err)
	}
	if err := syscall.Bind(fd, &syscall.SockaddrInet4{Port: port}); err != nil {
		log.Fatal(err)
	}
	if err := syscall.Listen(fd, backlog); err != nil {
		log.Fatal(err)
	}
	log.Printf("listening on :%d without accept, backlog %d", port, backlog)
}

func main() {
	addr := flag.String("http", ":3000", "HTTP listen address")
	stuck := flag.Int("stuck", 3001, "port that listens but never accepts, 0 to disable")
	exit := flag.Bool("exit", false, "exit immediately with status 1")
	flag.Parse()
	if *exit {
		log.Print("exiting on purpose")
		os.Exit(1)
	}
	if b, err := os.ReadFile("/config/app.json"); err == nil {
		log.Printf("config: %s", b)
	}
	if *stuck != 0 {
		stuckListener(*stuck, 4)
	}
	http.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintln(w, "ok")
	})
	http.HandleFunc("/hang", func(w http.ResponseWriter, r *http.Request) {
		select {}
	})
	http.HandleFunc("/leak", func(w http.ResponseWriter, r *http.Request) {
		n, _ := strconv.Atoi(r.URL.Query().Get("n"))
		mu.Lock()
		defer mu.Unlock()
		for i := 0; i < n; i++ {
			if f, err := os.Open("/config/app.json"); err == nil {
				held = append(held, f)
			}
			if c, err := net.Dial("tcp", "127.0.0.1"+*addr); err == nil {
				held = append(held, c)
			}
		}
		fmt.Fprintf(w, "holding %d\n", len(held))
	})
	log.Printf("listening on %s", *addr)
	log.Fatal(http.ListenAndServe(*addr, nil))
}
