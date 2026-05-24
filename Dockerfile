FROM rust:1.85-slim AS builder
WORKDIR /app
COPY . .
RUN cd roomer_backend && cargo build --release

FROM ubuntu:24.04
WORKDIR /app
RUN apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/roomer_backend/target/release/roomer_backend .
CMD ["./roomer_backend"]
