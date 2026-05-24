import os
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict
import psycopg2
from psycopg2.extras import RealDictCursor

app = FastAPI()

# 1. Железобетонный CORS — разрешаем запросы отовсюду
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. Менеджер WebSocket-комнат
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, room_id: int, websocket: WebSocket):
        await websocket.accept()
        if room_id not in self.active_connections:
            self.active_connections[room_id] = []
        self.active_connections[room_id].append(websocket)

    def disconnect(self, room_id: int, websocket: WebSocket):
        if room_id in self.active_connections:
            self.active_connections[room_id].remove(websocket)

    async def broadcast(self, room_id: int, message: str):
        if room_id in self.active_connections:
            for connection in self.active_connections[room_id]:
                await connection.send_text(message)

manager = ConnectionManager()

# 3. Функция подключения к Supabase PostgreSQL по плоским полям
def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "44.223.149.3"),
        database="postgres",
        user=os.getenv("DB_USER", "postgres.vdbevrnecyvmmxtsnpxn"),
        password=os.getenv("DB_PASS", "roomerdataba"),
        port=6543,
        sslmode="require" # Ирландия Supabase требует SSL, в Python это пишется одной строчкой!
    )

# Инициализация таблиц при старте
conn = get_db_connection()
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS tags (id BIGSERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL);")
cursor.execute("CREATE TABLE IF NOT EXISTS rooms (id BIGSERIAL PRIMARY KEY, title TEXT NOT NULL, description TEXT, creator_id BIGINT, is_active BOOLEAN DEFAULT TRUE);")
cursor.execute("CREATE TABLE IF NOT EXISTS room_tags (room_id BIGINT REFERENCES rooms(id) ON DELETE CASCADE, tag_id BIGINT REFERENCES tags(id) ON DELETE CASCADE, PRIMARY KEY (room_id, tag_id));")
cursor.execute("CREATE TABLE IF NOT EXISTS messages (id BIGSERIAL PRIMARY KEY, room_id BIGINT REFERENCES rooms(id) ON DELETE CASCADE, text TEXT NOT NULL);")
conn.commit()
cursor.close()
conn.close()

# Модели данных
class CreateRoomInput(BaseModel):
    title: str
    description: str
    creator_id: int
    tags: List[str]

@app.get("/")
def home():
    return {"status": "Python FastAPI Бэкенд запущен успешно!"}

# 4. Получение списка комнат
@app.get("/rooms")
def get_rooms():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT r.id, r.title, r.description, r.creator_id, string_agg(t.name, ',') as room_tags 
        FROM rooms r 
        LEFT JOIN room_tags rt ON r.id = rt.room_id 
        LEFT JOIN tags t ON rt.tag_id = t.id 
        WHERE r.is_active = TRUE 
        GROUP BY r.id 
        ORDER BY r.id DESC
    """)
    rows = cursor.fetchall()
    
    rooms = []
    for row in rows:
        rooms.append({
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "creator_id": row["creator_id"],
            "tags": row["room_tags"].split(",") if row["room_tags"] else []
        })
    cursor.close()
    conn.close()
    return rooms

# 5. Создание комнаты
@app.post("/rooms")
def create_room(payload: CreateRoomInput):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO rooms (title, description, creator_id) VALUES (%s, %s, %s) RETURNING id",
        (payload.title, payload.description, payload.creator_id)
    )
    room_id = cursor.fetchone()[0]

    for tag in payload.tags:
        clean_tag = tag.strip().lower()
        if not clean_tag: continue
        cursor.execute("INSERT INTO tags (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (clean_tag,))
        cursor.execute("SELECT id FROM tags WHERE name = %s", (clean_tag,))
        tag_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO room_tags (room_id, tag_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (room_id, tag_id))
    
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "success", "room_id": room_id}

# 6. Живой WebSocket Чат комнаты
@app.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: int):
    await manager.connect(room_id, websocket)
    
    # Загружаем историю чата из Supabase при подключении
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT text FROM messages WHERE room_id = %s ORDER BY id ASC", (room_id,))
    history = cursor.fetchall()
    cursor.close()
    conn.close()
    
    for msg in history:
        await websocket.send_text(msg[0])

    try:
        while True:
            data = await websocket.receive_text()
            clean_text = data.strip()
            if not clean_text: continue
            
            # Сохраняем сообщение в базу
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("INSERT INTO messages (room_id, text) VALUES (%s, %s)", (room_id, clean_text))
            conn.commit()
            cursor.close()
            conn.close()
            
            # Рассылаем всем участникам комнаты
            await manager.broadcast(room_id, clean_text)
    except WebSocketDisconnect:
        manager.disconnect(room_id, websocket)
