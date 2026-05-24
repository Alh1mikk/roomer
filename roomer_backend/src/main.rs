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
use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::{Arc, Mutex};
use tokio::sync::broadcast;
use tower_http::cors::CorsLayer;
use postgres_rustls::MakeTlsConnector;

struct AppState {
    db_client: Arc<tokio_postgres::Client>,
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
    // 1. Считываем плоские переменные окружения из панели Railway
    let db_host = std::env::var("DB_HOST").unwrap_or_else(|_| "://supabase.com".to_string());
    let db_user = std::env::var("DB_USER").unwrap_or_else(|_| "postgres.vdbevrnecyvmmxtsnpxn".to_string());
    let db_pass = std::env::var("DB_PASS").unwrap_or_else(|_| "roomerdataba".to_string());

    // 2. Настраиваем чистый Rustls коннектор с корневыми сертификатами интернета webpki
    let mut root_store = rustls::RootCertStore::empty();
    root_store.extend(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
    
    let config = rustls::ClientConfig::builder()
        .with_root_certificates(root_store)
        .with_no_client_auth();
    let connector = MakeTlsConnector::new(config);

    // 3. Подключаемся К IPv4 пуллеру Supabase строго по отдельным полям
    let (client, connection) = tokio_postgres::Config::new()
        .host(&db_host)
        .port(6543)
        .user(&db_user)
        .password(&db_pass)
        .dbname("postgres")
        .connect(connector)
        .await
        .expect("НЕ УДАЛОСЬ ПОДКЛЮЧИТЬСЯ К SUPABASE POSTGRESQL");

    // Запускаем фоновый поток удержания пула
    tokio::spawn(async move {
        if let Err(e) = connection.await {
            eprintln!("Ошибка пула базы данных: {}", e);
        }
    });

    println!("✅ Облачная база данных Supabase успешно подключена через tokio-postgres + rustls!");

    // Создаем структуру таблиц
    let _ = client.execute("CREATE TABLE IF NOT EXISTS users (id BIGSERIAL PRIMARY KEY, tg_id BIGINT UNIQUE NOT NULL, username TEXT NOT NULL, bio TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);", &[]).await;
    let _ = client.execute("CREATE TABLE IF NOT EXISTS tags (id BIGSERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL);", &[]).await;
    let _ = client.execute("CREATE TABLE IF NOT EXISTS rooms (id BIGSERIAL PRIMARY KEY, title TEXT NOT NULL, description TEXT, creator_id BIGINT, max_participants INTEGER DEFAULT 5, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);", &[]).await;
    let _ = client.execute("CREATE TABLE IF NOT EXISTS room_tags (room_id BIGINT REFERENCES rooms(id) ON DELETE CASCADE, tag_id BIGINT REFERENCES tags(id) ON DELETE CASCADE, PRIMARY KEY (room_id, tag_id));", &[]).await;
    let _ = client.execute("CREATE TABLE IF NOT EXISTS messages (id BIGSERIAL PRIMARY KEY, room_id BIGINT REFERENCES rooms(id) ON DELETE CASCADE, text TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);", &[]).await;

    println!("📋 Все таблицы в Supabase успешно проверены");

    let shared_state = Arc::new(AppState {
        db_client: Arc::new(client),
        rooms_channels: Mutex::new(HashMap::new()),
    });

    let app = Router::new()
        .route("/", get(|| async { "Добро пожаловать в Продакшн Roomer API на Чистом Драйвере!" }))
        .route("/rooms", post(create_room_handler))
        .route("/rooms", get(get_rooms_handler))
        .route("/ws/:room_id", get(ws_handler))
        .layer(CorsLayer::very_permissive())
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

    // Загрузка старой истории чата
    if let Ok(rows) = state.db_client.query("SELECT text FROM messages WHERE room_id = $1 ORDER BY id ASC", &[&room_id]).await {
        for row in rows {
            let old_msg: String = row.get(0);
            if sender.send(Message::Text(old_msg)).await.is_err() { return; }
        }
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
    let db_clone = state.db_client.clone();
    let mut recv_task = tokio::spawn(async move {
        while let Some(Ok(Message::Text(text))) = receiver.next().await {
            let clean_text = text.trim().to_string();
            if clean_text.is_empty() { continue; }

            let _ = db_clone.execute("INSERT INTO messages (room_id, text) VALUES ($1, $2)", &[&room_id, &clean_text]).await;
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
    let room_row = state.db_client.query_one(
        "INSERT INTO rooms (title, description, creator_id) VALUES ($1, $2, $3) RETURNING id",
        &[&payload.title, &payload.description, &payload.creator_id]
    ).await.unwrap();

    let room_id: i64 = room_row.get(0);

    for raw_tag in payload.tags {
        let clean_tag = raw_tag.trim().to_lowercase();
        if clean_tag.is_empty() { continue; }

        let _ = state.db_client.execute("INSERT INTO tags (name) ON CONFLICT (name) DO NOTHING", &[]).await;
        if let Ok(tag_rows) = state.db_client.query("SELECT id FROM tags WHERE name = $1", &[&clean_tag]).await {
            if let Some(tag_row) = tag_rows.first() {
                let tag_id: i64 = tag_row.get(0);
                let _ = state.db_client.execute("INSERT INTO room_tags (room_id, tag_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", &[&room_id, &tag_id]).await;
            }
        }
    }

    Json(RoomResponse { status: "success".to_string(), room_id })
}

async fn get_rooms_handler(State(state): State<Arc<AppState>>) -> Json<Vec<RoomListResponse>> {
    let rows = state.db_client.query(
        "SELECT r.id, r.title, r.description, r.creator_id, string_agg(t.name, ',') as room_tags 
         FROM rooms r 
         LEFT JOIN room_tags rt ON r.id = rt.room_id 
         LEFT JOIN tags t ON rt.tag_id = t.id 
         WHERE r.is_active = TRUE 
         GROUP BY r.id 
         ORDER BY r.id DESC",
        &[]
    ).await.unwrap();

    let rooms = rows.into_iter().map(|row| {
        let tags_str: Option<String> = row.get(4);
        let tags = match tags_str {
            Some(s) => s.split(',').map(|t| t.to_string()).collect(),
            None => vec![],
        };
        RoomListResponse { 
            id: row.get(0), 
            title: row.get(1), 
            description: row.get(2), 
            creator_id: row.get(3), 
            tags 
        }
    }).collect();

    Json(rooms)
}
