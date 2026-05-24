# Шаг 1: Компиляция Rust-кода на свежей версии
FROM rust:1.89-slim AS builder
WORKDIR /app

# Копируем весь репозиторий в контейнер
COPY . .

# Собираем бинарник. SQLX_OFFLINE говорит компилятору НЕ подключаться к базе в момент сборки
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
