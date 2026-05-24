FROM rust:1.89-slim AS builder
WORKDIR /app
COPY . .
# Чистая и быстрая сборка приложения без оффлайн-костылей
RUN cd roomer_backend && cargo build --release

FROM debian:bookworm-slim
WORKDIR /app
RUN apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/roomer_backend/target/release/roomer_backend .
CMD ["./roomer_backend"]
