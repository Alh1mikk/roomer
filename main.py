import os
import json
import asyncio
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Dict
import psycopg2
from psycopg2.extras import RealDictCursor

app = FastAPI()

# Железобетонный CORS для полной разблокировки запросов с GitHub Pages
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
            if websocket in self.active_connections[room_id]:
                self.active_connections[room_id].remove(websocket)

    async def broadcast(self, room_id: int, message_object: dict):
        if room_id in self.active_connections:
            for connection in self.active_connections[room_id]:
                try:
                    await connection.send_text(json.dumps(message_object))
                except Exception:
                    pass

manager = ConnectionManager()

def get_db_connection():
    # Читаем плоские переменные Ирландии со стабильным текстовым дефолтом
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "aws-0-eu-west-1.pooler.supabase.com"),
        database="postgres",
        user=os.getenv("DB_USER", "postgres.vdbevrnecyvmmxtsnpxn"),
        password=os.getenv("DB_PASS", "roomerdataba"),
        port=6543,
        sslmode="require"
    )

def ai_moderate_text(text: str) -> str:
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

# Однократная инициализация структуры БД при старте сервера
conn = get_db_connection()
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS tags (id BIGSERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL);")
cursor.execute("CREATE TABLE IF NOT EXISTS rooms (id BIGSERIAL PRIMARY KEY, title TEXT NOT NULL, description TEXT, creator_id BIGINT, is_active BOOLEAN DEFAULT TRUE);")
cursor.execute("CREATE TABLE IF NOT EXISTS room_tags (room_id BIGINT REFERENCES rooms(id) ON DELETE CASCADE, tag_id BIGINT REFERENCES tags(id) ON DELETE CASCADE, PRIMARY KEY (room_id, tag_id));")
cursor.execute("CREATE TABLE IF NOT EXISTS messages (id BIGSERIAL PRIMARY KEY, room_id BIGINT REFERENCES rooms(id) ON DELETE CASCADE, text TEXT NOT NULL);")
conn.commit()
# Миграция: добавляем sender_id, если колонки ещё нет
cursor.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='messages' AND column_name='sender_id'
        ) THEN
            ALTER TABLE messages ADD COLUMN sender_id BIGINT DEFAULT 0;
        END IF;
    END
    $$;
""")
conn.commit()
cursor.close()
conn.close()

class CreateRoomInput(BaseModel):
    title: str
    description: str
    creator_id: int
    tags: List[str]

# ─── Отдаём index.html по корневому пути ───
@app.get("/")
def home():
    html_path = Path(__file__).parent / "index.html"
    if html_path.exists():
        return FileResponse(str(html_path))
    return {"status": "FastAPI работает на 100%!", "note": "index.html не найден"}

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
    room_id = cursor.fetchone()[0] # Безопасное извлечение ID из кортежа
    
    for tag in payload.tags:
        clean_tag = tag.strip().lower()
        if not clean_tag: continue
        cursor.execute("INSERT INTO tags (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (clean_tag,))
        cursor.execute("SELECT id FROM tags WHERE name = %s", (clean_tag,))
        tag_id_row = cursor.fetchone()
        if tag_id_row:
            tag_id = tag_id_row[0]
            cursor.execute("INSERT INTO room_tags (room_id, tag_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (room_id, tag_id))
            
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "success", "room_id": room_id}

@app.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: int):
    # 1. Моментально жмем руку прокси Railway и Firefox
    await manager.connect(room_id, websocket)
    
    # Отправляем моментальное системное подтверждение
    try:
        await websocket.send_text(json.dumps({"text": "системное_сообщение_подключено", "sender_id": -1}))
    except Exception:
        manager.disconnect(room_id, websocket)
        return

    # 2. Потоковая фоновая выгрузка старой истории чата
    def fetch_history():
        c = get_db_connection()
        cur = c.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT text, sender_id FROM messages WHERE room_id = %s ORDER BY id ASC", (room_id,))
        h = cur.fetchall()
        cur.close()
        c.close()
        return h

    try:
        history = await asyncio.to_thread(fetch_history)
        for msg in history:
            await websocket.send_text(json.dumps({"text": msg["text"], "sender_id": msg["sender_id"]}))
    except Exception as e:
        print(f"Ошибка выгрузки истории чата: {e}")

    # 3. Бесконечный цикл обмена живыми сообщениями
    try:
        while True:
            raw_data = await websocket.receive_text()
            data = json.loads(raw_data)
            
            text = data.get("text", "").strip()
            sender_id = data.get("sender_id", 0)
            if not text: continue
            
            clean_text = ai_moderate_text(text)
            
            def save_message():
                c = get_db_connection()
                cur = c.cursor()
                cur.execute("INSERT INTO messages (room_id, text, sender_id) VALUES (%s, %s, %s)", (room_id, clean_text, sender_id))
                c.commit()
                cur.close()
                c.close()

            # Параллельный запуск сохранения без заморозки асинхронного сокета
            await asyncio.to_thread(save_message)
            await manager.broadcast(room_id, {"text": clean_text, "sender_id": sender_id})
            
    except WebSocketDisconnect:
        manager.disconnect(room_id, websocket)
    except Exception as e:
        print(f"Критический сбой WebSocket-сессии: {e}")
        manager.disconnect(room_id, websocket)
