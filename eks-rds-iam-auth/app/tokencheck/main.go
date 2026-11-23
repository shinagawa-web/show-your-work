package main

import (
	"context"
	"fmt"
	"log"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/feature/rds/auth"
	"github.com/aws/aws-sdk-go-v2/service/sts"
)

func main() {
	ctx := context.Background()
	fmt.Printf("AWS_CONTAINER_CREDENTIALS_FULL_URI=%s\n", os.Getenv("AWS_CONTAINER_CREDENTIALS_FULL_URI"))
	awsCfg, err := config.LoadDefaultConfig(ctx)
	if err != nil {
		log.Fatal(err)
	}
	creds, err := awsCfg.Credentials.Retrieve(ctx)
	if err != nil {
		log.Fatalf("retrieve credentials: %v", err)
	}
	fmt.Printf("credentials source=%s access_key_prefix=%s session_token=%t expires_in=%s\n",
		creds.Source, creds.AccessKeyID[:4], creds.SessionToken != "", time.Until(creds.Expires).Round(time.Minute))

	id, err := sts.NewFromConfig(awsCfg).GetCallerIdentity(ctx, &sts.GetCallerIdentityInput{})
	if err != nil {
		log.Fatalf("get caller identity: %v", err)
	}
	fmt.Printf("caller=%s\n", *id.Arn)

	endpoint := os.Getenv("DB_HOST") + ":3306"
	token, err := auth.BuildAuthToken(ctx, endpoint, awsCfg.Region, os.Getenv("DB_USER"), awsCfg.Credentials)
	if err != nil {
		log.Fatalf("build auth token: %v", err)
	}
	u, err := url.Parse("https://" + token)
	if err != nil {
		log.Fatalf("parse token: %v", err)
	}
	fmt.Printf("token host=%s length=%d\n", u.Host, len(token))
	q := u.Query()
	for _, k := range []string{"Action", "DBUser", "X-Amz-Algorithm", "X-Amz-Credential", "X-Amz-Expires", "X-Amz-SignedHeaders"} {
		v := q.Get(k)
		if k == "X-Amz-Credential" {
			parts := strings.SplitN(v, "/", 2)
			v = parts[0][:4] + ".../" + parts[1]
		}
		fmt.Printf("token %s=%s\n", k, v)
	}
	fmt.Printf("token X-Amz-Security-Token present=%t\n", q.Get("X-Amz-Security-Token") != "")
	fmt.Printf("token X-Amz-Signature length=%d\n", len(q.Get("X-Amz-Signature")))
	if !strings.HasPrefix(creds.AccessKeyID, "ASIA") || q.Get("X-Amz-Security-Token") == "" || q.Get("DBUser") != os.Getenv("DB_USER") {
		log.Fatal("token was not signed with temporary credentials")
	}
	fmt.Println("token check: ok")
}
