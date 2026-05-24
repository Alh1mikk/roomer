# Шаг 1: Компиляция Rust-кода
FROM rust:1.85-slim AS builder
WORKDIR /app

# Копируем весь репозиторий в контейнер
COPY . .

# Заходим внутрь папки бэкенда и запускаем сборку там
RUN cd roomer_backend && SQLX_OFFLINE=true cargo build --release

# Шаг 2: Легковесный запуск готового бинарника
FROM debian:bookworm-slim
WORKDIR /app

# Ставим SSL-сертификаты для безопасного коннекта к Supabase
RUN apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*

# Копируем скомпилированный бинарник из папки сборки
COPY --from=builder /app/roomer_backend/target/release/roomer_backend .

# Запуск нашего сервера
CMD ["./roomer_backend"]
