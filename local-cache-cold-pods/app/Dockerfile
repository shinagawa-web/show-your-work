ARG GO_IMAGE
ARG BASE_IMAGE
FROM ${GO_IMAGE} AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY main.go ./
RUN CGO_ENABLED=0 go build -o /app .
FROM ${BASE_IMAGE}
COPY --from=build /app /app
ENTRYPOINT ["/app"]
