package main

import (
	"context"
	"io"
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/feature/cloudfront/sign"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

const (
	appAddr     = ":8080"
	allowHeader = "X-Allow"
	urlTTL      = 15 * time.Minute
)

func main() {
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
