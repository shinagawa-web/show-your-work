ARG GO_IMAGE
FROM ${GO_IMAGE} AS build
WORKDIR /src
COPY go.mod main.go ./
RUN CGO_ENABLED=0 go build -o /app .
FROM scratch
COPY --from=build /app /app
ENTRYPOINT ["/app"]
