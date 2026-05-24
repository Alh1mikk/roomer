# Шаг 1: Компиляция Rust-кода
FROM rust:1.75-slim AS builder
WORKDIR /app

# Копируем файлы (они уже лежат в корне, так как Railway передал только эту подпапку)
COPY Cargo.toml ./
COPY src ./src

# Запускаем сборку с флагом отключения оффлайн-валидации макросов sqlx
RUN SQLX_OFFLINE=true cargo build --release

# Шаг 2: Легковесный запуск готового бинарника
FROM debian:bookworm-slim
WORKDIR /app

# Ставим SSL-сертификаты для безопасного коннекта к Supabase
RUN apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*

# Копируем скомпилированный файл из шага сборки
COPY --from=builder /app/target/release/roomer_backend .

# Запуск нашего сервера
CMD ["./roomer_backend"]
