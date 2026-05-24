# Шаг 1: Компиляция Rust-кода
FROM rust:1.89-slim AS builder
WORKDIR /app
COPY . .
RUN cd roomer_backend && cargo build --release

# Шаг 2: Финальный запуск (Заменили Debian на современную Ubuntu 24.04)
FROM ubuntu:24.04
WORKDIR /app

# Обновляем сертификаты SSL внутри Ubuntu, чтобы работал коннект к Supabase
RUN apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*

# Копируем скомпилированный бинарник
COPY --from=builder /app/roomer_backend/target/release/roomer_backend .

# Запуск нашего сервера
CMD ["./roomer_backend"]
