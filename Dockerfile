# Шаг 1: Компиляция Rust-кода
FROM rust:1.89-slim AS builder
WORKDIR /app
COPY . .
RUN cd roomer_backend && cargo build --release

# Шаг 2: Финальный запуск
FROM ubuntu:24.04
WORKDIR /app

# Обновляем сертификаты SSL внутри Ubuntu
RUN apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*

# Копируем скомпилированный бинарник
COPY --from=builder /app/roomer_backend/target/release/roomer_backend .

# ИСПРАВЛЕНО: Перед запуском сервера принудительно прописываем DNS Google, чтобы домен Supabase резолвился!
CMD sh -c "echo 'nameserver 8.8.8.8' > /etc/resolv.conf && ./roomer_backend"
