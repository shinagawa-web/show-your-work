package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/go-sql-driver/mysql"
)

func main() {
	cfg := mysql.NewConfig()
	cfg.Net = "tcp"
	cfg.Addr = os.Getenv("DB_HOST") + ":3306"
	cfg.User = os.Getenv("DB_USER")
	cfg.Passwd = os.Getenv("DB_PASSWORD")
	cfg.DBName = os.Getenv("DB_NAME")

	connector, err := mysql.NewConnector(cfg)
	if err != nil {
		log.Fatal(err)
	}
	db := sql.OpenDB(connector)
	db.SetMaxOpenConns(5)
	db.SetMaxIdleConns(5)
	lifetime, _ := time.ParseDuration(os.Getenv("DB_CONN_MAX_LIFETIME"))
	db.SetConnMaxLifetime(lifetime)

	pod, _ := os.Hostname()
	started := time.Now().UTC().Format(time.RFC3339)
	http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()
		var user, name string
		var connID int64
		err := db.QueryRowContext(ctx, "SELECT CURRENT_USER(), CONNECTION_ID(), name FROM items WHERE id = 1").Scan(&user, &connID, &name)
		if err != nil {
			log.Printf("query failed: %v", err)
			http.Error(w, err.Error(), http.StatusServiceUnavailable)
			return
		}
		json.NewEncoder(w).Encode(map[string]any{"pod": pod, "started": started, "user": user, "conn_id": connID})
	})

	srv := &http.Server{Addr: ":8080"}
	go func() {
		sig := make(chan os.Signal, 1)
		signal.Notify(sig, syscall.SIGTERM)
		<-sig
		srv.Shutdown(context.Background())
	}()
	if err := srv.ListenAndServe(); !errors.Is(err, http.ErrServerClosed) {
		log.Fatal(err)
	}
}
