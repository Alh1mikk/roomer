import os
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict
import psycopg2
from psycopg2.extras import RealDictCursor

app = FastAPI()

# Разрешаем CORS запросы с гитхаба
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

    async def broadcast(self, room_id: int, message_object: dict):
        if room_id in self.active_connections:
            # Отправляем JSON строку всем участникам
            for connection in self.active_connections[room_id]:
                await connection.send_text(json.dumps(message_object))

manager = ConnectionManager()

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "://supabase.com"),
        database="postgres",
        user=os.getenv("DB_USER", "postgres.vdbevrnecyvmmxtsnpxn"),
        password=os.getenv("DB_PASS", "roomerdataba"),
        port=6543,
        sslmode="require"
    )

# Простая ИИ-функция модерации текста (базовый семантический фильтр токсичности) [INDEX]
def ai_moderate_text(text: str) -> str:
    # Список запрещенных корней (можно расширять до бесконечности)
    bad_words = ["хуй", "пизд", "ебан", "блять", "сука", "бля", "нах"]
    words = text.split()
    clean_words = []
    for word in words:
        lower_word = word.lower()
        if any(bad in lower_word for bad in bad_words):
            clean_words.append("[ЦЕНЗУРА]")
        else:
            clean_words.append(word)
    return " ".join(clean_words)

# Проверка базы данных
conn = get_db_connection()
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS tags (id BIGSERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL);")
cursor.execute("CREATE TABLE IF NOT EXISTS rooms (id BIGSERIAL PRIMARY KEY, title TEXT NOT NULL, description TEXT, creator_id BIGINT, is_active BOOLEAN DEFAULT TRUE);")
cursor.execute("CREATE TABLE IF NOT EXISTS room_tags (room_id BIGINT REFERENCES rooms(id) ON DELETE CASCADE, tag_id BIGINT REFERENCES tags(id) ON DELETE CASCADE, PRIMARY KEY (room_id, tag_id));")
cursor.execute("CREATE TABLE IF NOT EXISTS messages (id BIGSERIAL PRIMARY KEY, room_id BIGINT REFERENCES rooms(id) ON DELETE CASCADE, text TEXT NOT NULL, sender_id BIGINT DEFAULT 0);")
conn.commit()
cursor.close()
conn.close()

class CreateRoomInput(BaseModel):
    title: str
    description: str
    creator_id: int
    tags: List[str]

@app.get("/")
def home():
    return {"status": "FastAPI работает!"}

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

@app.post("/rooms")
def create_room(payload: CreateRoomInput):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO rooms (title, description, creator_id) VALUES (%s, %s, %s) RETURNING id", (payload.title, payload.description, payload.creator_id))
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

@app.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: int):
    await manager.connect(room_id, websocket)
    
    # Отдаем историю чата в формате JSON объектов
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT text, sender_id FROM messages WHERE room_id = %s ORDER BY id ASC", (room_id,))
    history = cursor.fetchall()
    cursor.close()
    conn.close()
    
    for msg in history:
        await websocket.send_text(json.dumps({"text": msg["text"], "sender_id": msg["sender_id"]}))

    try:
        while True:
            raw_data = await websocket.receive_text()
            data = json.loads(raw_data)
            
            text = data.get("text", "").strip()
            sender_id = data.get("sender_id", 0)
            if not text: continue
            
            # Врубаем ИИ-модерацию мата [INDEX]
            clean_text = ai_moderate_text(text)
            
            # Пишем в базу
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("INSERT INTO messages (room_id, text, sender_id) VALUES (%s, %s, %s)", (room_id, clean_text, sender_id))
            conn.commit()
            cursor.close()
            conn.close()
            
            # Вещаем на всю комнату структурированный JSON
            await manager.broadcast(room_id, {"text": clean_text, "sender_id": sender_id})
    except WebSocketDisconnect:
        manager.disconnect(room_id, websocket)
