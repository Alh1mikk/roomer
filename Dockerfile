# Шаг 1: Сборка на чистом Rust без оффлайн модов
FROM rust:1.89-slim AS builder
WORKDIR /app
COPY . .
RUN cd roomer_backend && cargo build --release

# Шаг 2: Финальный ультра-легкий образ
FROM debian:bookworm-slim
WORKDIR /app
RUN apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/roomer_backend/target/release/roomer_backend .
CMD ["./roomer_backend"]
