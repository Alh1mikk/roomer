# Шаг 1: Компиляция Rust-кода
FROM rust:1.85-slim AS builder
WORKDIR /app

# Ставим утилиты для сборки Си-пакетов TLS
RUN apt-get update && apt-get install -y pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*

COPY . .
RUN cd roomer_backend && cargo build --release

# Шаг 2: Финальный запуск
FROM ubuntu:24.04
WORKDIR /app
RUN apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/roomer_backend/target/release/roomer_backend .
CMD ["./roomer_backend"]
