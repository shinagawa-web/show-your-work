package main

import (
	"context"
	"database/sql"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"sort"
	"strings"

	"github.com/XSAM/otelsql"
	_ "github.com/jackc/pgx/v5/stdlib"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/contrib/propagators/autoprop"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
)

var b3Headers = []string{"X-B3-TraceId", "X-B3-SpanId", "X-B3-ParentSpanId", "X-B3-Sampled", "X-B3-Flags"}

func main() {
	next := os.Getenv("NEXT_URL")
	dsn := os.Getenv("DATABASE_URL")

	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })

	if os.Getenv("MANUAL_FORWARD") == "1" {
		mux.Handle("/", manualForward(next))
	} else {
		mux.Handle("/", instrumented(next, dsn))
	}

	log.Fatal(http.ListenAndServe(":8080", mux))
}

func manualForward(next string) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, next, nil)
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		for _, h := range b3Headers {
			if v := r.Header.Get(h); v != "" {
				req.Header.Set(h, v)
			}
		}
		call(w, http.DefaultClient, req)
	})
}

func instrumented(next, dsn string) http.Handler {
	ctx := context.Background()
	exporter, err := otlptracehttp.New(ctx)
	if err != nil {
		log.Fatal(err)
	}
	res, err := resource.New(ctx, resource.WithFromEnv(), resource.WithTelemetrySDK())
	if err != nil {
		log.Fatal(err)
	}
	otel.SetTracerProvider(sdktrace.NewTracerProvider(sdktrace.WithBatcher(exporter), sdktrace.WithResource(res)))
	otel.SetTextMapPropagator(autoprop.NewTextMapPropagator())

	client := &http.Client{Transport: otelhttp.NewTransport(http.DefaultTransport)}
	var db *sql.DB
	if dsn != "" {
		db, err = otelsql.Open("pgx", dsn, otelsql.WithSQLCommenter(true))
		if err != nil {
			log.Fatal(err)
		}
	}

	handler := func(w http.ResponseWriter, r *http.Request) {
		printTraceHeaders(r.Header)
		if db != nil {
			var one int
			if err := db.QueryRowContext(r.Context(), "SELECT 1").Scan(&one); err != nil {
				http.Error(w, err.Error(), http.StatusInternalServerError)
				return
			}
		}
		if next == "" {
			w.WriteHeader(http.StatusOK)
			return
		}
		req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, next, nil)
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		call(w, client, req)
	}
	return otelhttp.NewHandler(http.HandlerFunc(handler), "handle")
}

func printTraceHeaders(h http.Header) {
	names := make([]string, 0, len(h))
	for name := range h {
		lower := strings.ToLower(name)
		if lower == "traceparent" || lower == "tracestate" || strings.HasPrefix(lower, "x-b3-") {
			names = append(names, name)
		}
	}
	sort.Strings(names)
	for _, name := range names {
		for _, v := range h.Values(name) {
			fmt.Printf("received-header\t%s\t%s\n", name, v)
		}
	}
}

func call(w http.ResponseWriter, client *http.Client, req *http.Request) {
	resp, err := client.Do(req)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()
	w.WriteHeader(resp.StatusCode)
	io.Copy(w, resp.Body)
}
