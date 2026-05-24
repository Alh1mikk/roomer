use axum::{
    extract::{
        ws::{Message, WebSocket, WebSocketUpgrade},
        Path, State,
    },
    routing::{get, post},
    Json, Router,
};
use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use sqlx::{postgres::PgPoolOptions, Row, PgPool};
use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::{Arc, Mutex};
use tokio::sync::broadcast;
use tower_http::cors::CorsLayer;

struct AppState {
    pool: PgPool,
    rooms_channels: Mutex<HashMap<i64, broadcast::Sender<String>>>,
}

#[derive(Deserialize)]
struct CreateRoomInput {
    title: String,
    description: String,
    creator_id: i64,
    tags: Vec<String>,
}

#[derive(Serialize)]
struct RoomResponse {
    status: String,
    room_id: i64,
}

#[derive(Serialize)]
struct RoomListResponse {
    id: i64,
    title: String,
    description: Option<String>,
    creator_id: Option<i64>,
    tags: Vec<String>,
}

#[tokio::main]
async fn main() {
    let db_url = std::env::var("DATABASE_URL")
        .expect("ПЕРЕМЕННАЯ ОКРУЖЕНИЯ DATABASE_URL НЕ НАЙДЕНА!");

    let pool = PgPoolOptions::new()
        .max_connections(4)
        .connect(&db_url)
        .await
        .expect("Не удалось подключиться к Supabase PostgreSQL");

    println!("✅ Облачная база данных Supabase успешно подключена!");

    // Создаем таблицы в базе данных Supabase
    sqlx::query("CREATE TABLE IF NOT EXISTS users (id BIGSERIAL PRIMARY KEY, tg_id BIGINT UNIQUE NOT NULL, username TEXT NOT NULL, bio TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);").execute(&pool).await.unwrap();
    sqlx::query("CREATE TABLE IF NOT EXISTS tags (id BIGSERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL);").execute(&pool).await.unwrap();
    sqlx::query("CREATE TABLE IF NOT EXISTS rooms (id BIGSERIAL PRIMARY KEY, title TEXT NOT NULL, description TEXT, creator_id BIGINT, max_participants INTEGER DEFAULT 5, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);").execute(&pool).await.unwrap();
    sqlx::query("CREATE TABLE IF NOT EXISTS room_tags (room_id BIGINT REFERENCES rooms(id) ON DELETE CASCADE, tag_id BIGINT REFERENCES tags(id) ON DELETE CASCADE, PRIMARY KEY (room_id, tag_id));").execute(&pool).await.unwrap();
    sqlx::query("CREATE TABLE IF NOT EXISTS messages (id BIGSERIAL PRIMARY KEY, room_id BIGINT REFERENCES rooms(id) ON DELETE CASCADE, text TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);").execute(&pool).await.unwrap();

    println!("📋 Все таблицы в Supabase успешно проверены");

    let shared_state = Arc::new(AppState {
        pool,
        rooms_channels: Mutex::new(HashMap::new()),
    });

    let app = Router::new()
        .route("/", get(|| async { "Добро пожаловать в Продакшн Roomer API!" }))
        .route("/rooms", post(create_room_handler))
        .route("/rooms", get(get_rooms_handler))
        .route("/ws/:room_id", get(ws_handler))
        .layer(CorsLayer::very_permissive()) // Разрешаем CORS-запросы с гитхаба
        .with_state(shared_state);

    let port = std::env::var("PORT").unwrap_or_else(|_| "3000".to_string());
    let addr: SocketAddr = format!("0.0.0.0:{}", port).parse().unwrap();
    println!("🚀 Сервер Roomer запущен на порту {}", port);

    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}

async fn ws_handler(
    Path(room_id): Path<i64>,
    ws: WebSocketUpgrade,
    State(state): State<Arc<AppState>>,
) -> axum::response::Response {
    ws.on_upgrade(move |socket| handle_socket(socket, room_id, state))
}

async fn handle_socket(socket: WebSocket, room_id: i64, state: Arc<AppState>) {
    let (mut sender, mut receiver) = socket.split();

    let history_rows = sqlx::query("SELECT text FROM messages WHERE room_id = $1 ORDER BY id ASC")
        .bind(room_id)
        .fetch_all(&state.pool)
        .await
        .unwrap();

    for row in history_rows {
        let old_msg: String = row.get("text");
        if sender.send(Message::Text(old_msg)).await.is_err() { return; }
    }

    let tx = {
        let mut channels = state.rooms_channels.lock().unwrap();
        channels.entry(room_id).or_insert_with(|| {
            let (tx, _) = broadcast::channel(100);
            tx
        }).clone()
    };

    let mut rx = tx.subscribe();

    let mut send_task = tokio::spawn(async move {
        while let Ok(msg) = rx.recv().await {
            if sender.send(Message::Text(msg)).await.is_err() { break; }
        }
    });

    let tx_clone = tx.clone();
    let pool_clone = state.pool.clone();
    let mut recv_task = tokio::spawn(async move {
        while let Some(Ok(Message::Text(text))) = receiver.next().await {
            let clean_text = text.trim().to_string();
            if clean_text.is_empty() { continue; }

            let _ = sqlx::query("INSERT INTO messages (room_id, text) VALUES ($1, $2)")
                .bind(room_id).bind(&clean_text).execute(&pool_clone).await;

            let _ = tx_clone.send(clean_text);
        }
    });

    tokio::select! {
        _ = (&mut send_task) => recv_task.abort(),
        _ = (&mut recv_task) => send_task.abort(),
    };
}

async fn create_room_handler(
    State(state): State<Arc<AppState>>,
    Json(payload): Json<CreateRoomInput>,
) -> Json<RoomResponse> {
    let room_row = sqlx::query("INSERT INTO rooms (title, description, creator_id) VALUES ($1, $2, $3) RETURNING id")
        .bind(&payload.title).bind(&payload.description).bind(payload.creator_id)
        .fetch_one(&state.pool).await.unwrap();

    let room_id: i64 = room_row.get("id");

    for raw_tag in payload.tags {
        let clean_tag = raw_tag.trim().to_lowercase();
        if clean_tag.is_empty() { continue; }

        sqlx::query("INSERT INTO tags (name) ON CONFLICT (name) DO NOTHING").bind(&clean_tag).execute(&state.pool).await.unwrap();
        let tag_row = sqlx::query("SELECT id FROM tags WHERE name = $1").bind(&clean_tag).fetch_one(&state.pool).await.unwrap();
        let tag_id: i64 = tag_row.get("id");
        sqlx::query("INSERT INTO room_tags (room_id, tag_id) VALUES ($1, $2) ON CONFLICT DO NOTHING").bind(room_id).bind(tag_id).execute(&state.pool).await.unwrap();
    }

    Json(RoomResponse { status: "success".to_string(), room_id })
}

async fn get_rooms_handler(State(state): State<Arc<AppState>>) -> Json<Vec<RoomListResponse>> {
    let rows = sqlx::query(
        "SELECT r.id, r.title, r.description, r.creator_id, string_agg(t.name, ',') as room_tags 
         FROM rooms r 
         LEFT JOIN room_tags rt ON r.id = rt.room_id 
         LEFT JOIN tags t ON rt.tag_id = t.id 
         WHERE r.is_active = TRUE 
         GROUP BY r.id 
         ORDER BY r.id DESC"
    )
    .fetch_all(&state.pool).await.unwrap();

    let rooms = rows.into_iter().map(|row| {
        let tags_str: Option<String> = row.get("room_tags");
        let tags = match tags_str {
            Some(s) => s.split(',').map(|t| t.to_string()).collect(),
            None => vec![],
        };
        // ИСПРАВЛЕНО: Теперь синтаксис получения строки title строго соответствует стандартам Rust sqlx
        let title: String = row.get("title");
        RoomListResponse { id: row.get("id"), title, description: row.get("description"), creator_id: row.get("creator_id"), tags }
    }).collect();

    Json(rooms)
}
