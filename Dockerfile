# Шаг 1: Сборка бинарника
FROM rust:1.75-slim AS builder
WORKDIR /app
COPY . .
RUN cargo build --release

# Шаг 2: Легковесный запуск в продакшене
FROM debian:bookworm-slim
WORKDIR /app
# Ставим SSL сертификаты, чтобы Rust мог достучаться до Supabase по HTTPS
RUN apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/target/release/roomer_backend .

# Говорим запускать наше Rust приложение
CMD ["./roomer_backend"]
