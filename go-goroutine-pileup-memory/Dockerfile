FROM golang:1.21-alpine AS build
WORKDIR /app
COPY go.mod .
COPY main.go .
RUN go build -o server .

FROM alpine
COPY --from=build /app/server /server
ENV PORT=3000
EXPOSE 3000
CMD ["/server"]
