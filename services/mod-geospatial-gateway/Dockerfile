# syntax=docker/dockerfile:1
FROM golang:1.23 AS build
WORKDIR /src
COPY go.mod ./
COPY cmd ./cmd
COPY internal ./internal
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/geogateway ./cmd/server

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/geogateway /geogateway
EXPOSE 8014
ENV GATEWAY_ADDR=:8014
USER nonroot
ENTRYPOINT ["/geogateway"]
