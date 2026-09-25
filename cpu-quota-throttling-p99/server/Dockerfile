FROM golang:1.22-alpine AS build
WORKDIR /src
COPY main.go .
RUN go mod init server && CGO_ENABLED=0 go build -o /server .

FROM alpine:3.19
COPY --from=build /server /server
ENV W=4 CPU_MS=20 GOMAXPROCS=16
EXPOSE 8080
CMD ["/server"]
