ARG GO_IMAGE
FROM ${GO_IMAGE} AS build
WORKDIR /src
COPY go.mod main.go ./
RUN CGO_ENABLED=0 go build -o /server .

FROM scratch
COPY --from=build /server /server
