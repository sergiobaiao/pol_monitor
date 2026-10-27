import os
import sqlite3
import json
from datetime import datetime
from typing import Optional, Dict, Any, List

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DB_DIR, "pol_notifier.db")  # banco global (último usuário lembrado)
USERS_DIR = os.path.join(DB_DIR, "users")           # um banco por usuário: users/<sub>.db

# Chaves do banco global que não pertencem a nenhum usuário
GLOBAL_KEYS = ("remembered_email", "migrated_to")


def open_user_db(sub: str, email: str) -> "Database":
    """Abre o banco do usuário (chave = `sub` do JWT). Na primeira vez, migra o banco
    legado global se ele pertencer a esse usuário (mesmo pol_user_id ou pol_email)."""
    path = os.path.join(USERS_DIR, f"{sub}.db")
    if not os.path.exists(path) and os.path.exists(DB_PATH):
        legacy = Database(DB_PATH)
        owner_id = legacy.get_setting("pol_user_id", "")
        owner_email = legacy.get_setting("pol_email", "").lower()
        if not legacy.get_setting("migrated_to") and (owner_id == sub or owner_email == email.lower()):
            os.makedirs(USERS_DIR, exist_ok=True)
            with legacy._get_connection() as src, sqlite3.connect(path) as dst:
                src.backup(dst)
            with legacy._get_connection() as conn:
                marks = ",".join("?" * len(GLOBAL_KEYS))
                conn.execute(f"DELETE FROM settings WHERE key NOT IN ({marks})", GLOBAL_KEYS)
                conn.execute("DELETE FROM cached_items")
                conn.execute("DELETE FROM notification_history")
                conn.commit()
                conn.execute("VACUUM")  # não deixar a senha legada em páginas livres
            legacy.set_setting("migrated_to", sub)
            legacy.log("INFO", f"Dados legados migrados para o usuário {email}.")
    db = Database(path)
    # Senha e tokens nunca ficam no SQLite (a senha ia em texto puro nas versões antigas)
    # (chaves globais podem ter vindo junto na cópia do banco legado)
    stale = [k for k in ("pol_password", "pol_access_token", "pol_refresh_token", "pol_token_expiry", *GLOBAL_KEYS)
             if db.delete_setting(k)]
    if stale:
        with db._get_connection() as conn:
            conn.execute("VACUUM")
    return db


class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_tables()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Tabela de configurações
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Tabela de cache de tarefas e eventos
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cached_items (
                    id TEXT PRIMARY KEY,
                    item_type TEXT,
                    title TEXT,
                    description TEXT,
                    status TEXT,
                    priority TEXT,
                    deadline TIMESTAMP,
                    raw_json TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Tabela de histórico de notificações (deduplicação)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS notification_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id TEXT,
                    channel TEXT,
                    section TEXT,
                    recipient TEXT,
                    sent_date TEXT,
                    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT,
                    error_message TEXT
                )
            """)

            # Tabela de logs do sistema
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS app_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    level TEXT,
                    message TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    # --- Configurações ---
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: Any):
        str_val = str(value) if value is not None else ""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO settings (key, value, updated_at) 
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
            """, (key, str_val))
            conn.commit()

    def delete_setting(self, key: str) -> bool:
        with self._get_connection() as conn:
            deleted = conn.execute("DELETE FROM settings WHERE key = ?", (key,)).rowcount
            conn.commit()
            return deleted > 0

    def get_all_settings(self) -> Dict[str, str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM settings")
            return {row["key"]: row["value"] for row in cursor.fetchall()}

    # --- Cache de Itens (Tarefas e Calendário) ---
    def upsert_cached_item(self, item_id: str, item_type: str, title: str, description: str,
                           status: str, priority: str, deadline: Optional[str], raw_data: Dict[str, Any]):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO cached_items (id, item_type, title, description, status, priority, deadline, raw_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    description = excluded.description,
                    status = excluded.status,
                    priority = excluded.priority,
                    deadline = excluded.deadline,
                    raw_json = excluded.raw_json,
                    updated_at = CURRENT_TIMESTAMP
            """, (item_id, item_type, title, description, status, priority, deadline, json.dumps(raw_data, ensure_ascii=False)))
            conn.commit()

    def get_cached_items(self, item_type: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if item_type:
                cursor.execute("SELECT * FROM cached_items WHERE item_type = ? ORDER BY deadline ASC", (item_type,))
            else:
                cursor.execute("SELECT * FROM cached_items ORDER BY deadline ASC")
            return [dict(row) for row in cursor.fetchall()]

    def clear_cached_items(self):
        with self._get_connection() as conn:
            conn.cursor().execute("DELETE FROM cached_items")
            conn.commit()

    # --- Histórico e Deduplicação de Notificações ---
    def is_already_notified(self, item_id: str, channel: str, section: str, sent_date: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id FROM notification_history
                WHERE item_id = ? AND channel = ? AND section = ? AND sent_date = ? AND status = 'SUCCESS'
            """, (item_id, channel, section, sent_date))
            return cursor.fetchone() is not None

    def record_notification(self, item_id: str, channel: str, section: str, recipient: str,
                            sent_date: str, status: str = "SUCCESS", error_message: str = ""):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO notification_history (item_id, channel, section, recipient, sent_date, status, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (item_id, channel, section, recipient, sent_date, status, error_message))
            conn.commit()

    def get_recent_notifications(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM notification_history ORDER BY sent_at DESC LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]

    # --- Logs ---
    def log(self, level: str, message: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO app_logs (level, message, timestamp) VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (level.upper(), message))
            conn.commit()

    def get_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM app_logs ORDER BY timestamp DESC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def clear_logs(self):
        with self._get_connection() as conn:
            conn.cursor().execute("DELETE FROM app_logs")
            conn.commit()
