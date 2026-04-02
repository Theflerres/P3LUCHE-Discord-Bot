"""
Gerenciamento SQLite — criação de tabelas, migrações e instância global.
"""
import sqlite3

from config import DB_PATH


class DatabaseManager:
    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = None

    def connect(self):
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        return self.conn

    def _add_column_safe(self, table, column_def):
        """Tenta adicionar uma coluna, ignora se ela já existir."""
        cursor = self.conn.cursor()
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                pass
            else:
                print(f"❌ Erro ao alterar schema de {table}: {e}")

    def migrate(self):
        """Cria tabelas para Guilda, Party System e Histórico."""
        cursor = self.conn.cursor()

        cursor.execute(
            """CREATE TABLE IF NOT EXISTS music_cache (id INTEGER PRIMARY KEY, youtube_url TEXT UNIQUE, drive_link TEXT, title TEXT, normalized_title TEXT, duration INTEGER, added_by TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
        )
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS user_memories (id INTEGER PRIMARY KEY, user_id INTEGER, user_name TEXT, memory_text TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
        )
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS server_lore (id INTEGER PRIMARY KEY, content TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
        )
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS player_lore (id INTEGER PRIMARY KEY, target_id INTEGER, target_name TEXT, character_name TEXT, content TEXT, added_by TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
        )
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS warnings (id INTEGER PRIMARY KEY, user_id INTEGER, user_name TEXT, moderator_id INTEGER, moderator_name TEXT, reason TEXT, proof TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
        )
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS lore_versions (id INTEGER PRIMARY KEY, lore_type TEXT, original_lore_id INTEGER, content TEXT, edited_by TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
        )
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS lore_graph_cache (id INTEGER PRIMARY KEY CHECK (id = 1), mermaid_code TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS world_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                current_weather TEXT DEFAULT 'normal',
                weather_end TIMESTAMP
            )
        """
        )
        cursor.execute(
            "INSERT OR IGNORE INTO world_state (id, current_weather) VALUES (1, 'normal')"
        )

        cursor.execute(
            """
        CREATE TABLE IF NOT EXISTS quest_progress (
            user_id INTEGER PRIMARY KEY,
            current_chapter TEXT DEFAULT 'inicio',
            quest_status TEXT DEFAULT 'locked',
            inventory TEXT DEFAULT '{}',
            reputation INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
        )

        cursor.execute(
            """
        CREATE TABLE IF NOT EXISTS persistent_catches (
            user_id INTEGER PRIMARY KEY,
            catch_count INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
        )

        cursor.execute(
            """
        CREATE TABLE IF NOT EXISTS economy (
            user_id INTEGER PRIMARY KEY,
            user_name TEXT,
            wallet INTEGER DEFAULT 0,
            last_fish TIMESTAMP,
            last_daily TIMESTAMP,
            fish_count INTEGER DEFAULT 0,
            rod_tier INTEGER DEFAULT 0,
            baits INTEGER DEFAULT 0,
            inventory TEXT DEFAULT '{}',
            current_rod TEXT DEFAULT 'vara_bambu',
            last_explore TIMESTAMP,
            guild_rank TEXT DEFAULT 'F',
            guild_xp INTEGER DEFAULT 0,
            scrap INTEGER DEFAULT 0,
            afk_trap TEXT DEFAULT '{}',
            rod_upgrades TEXT DEFAULT '{}'
        )
        """
        )

        cursor.execute(
            """
        CREATE TABLE IF NOT EXISTS parties (
            leader_id INTEGER PRIMARY KEY,
            leader_name TEXT,
            members_json TEXT DEFAULT '[]',
            active_mission_id TEXT,
            mission_progress INTEGER DEFAULT 0,
            mission_target INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
        )

        self.conn.commit()

        self._add_column_safe("economy", "inventory TEXT DEFAULT '{}'")
        self._add_column_safe("economy", "guild_rank TEXT DEFAULT 'F'")
        self._add_column_safe("economy", "guild_xp INTEGER DEFAULT 0")
        self._add_column_safe("economy", "scrap INTEGER DEFAULT 0")
        self._add_column_safe("economy", "rod_upgrades TEXT DEFAULT '{}'")
        self._add_column_safe("economy", "afk_trap TEXT DEFAULT '{}'")

        self._add_column_safe("economy", "last_explore TIMESTAMP")
        self._add_column_safe("economy", "fish_count INTEGER DEFAULT 0")

        self._add_column_safe("warnings", "status TEXT DEFAULT 'active'")
        self._add_column_safe("music_cache", "is_active INTEGER DEFAULT 1")
        self._add_column_safe("music_cache", "edited_by TEXT")
        self._add_column_safe("music_cache", "edited_at TIMESTAMP")
        self._add_column_safe("user_memories", "tag TEXT")
        self._add_column_safe("player_lore", "edited_at TIMESTAMP")
        self._add_column_safe("user_memories", "is_active INTEGER DEFAULT 1")

        self.conn.commit()
        print("💾 Banco de Dados atualizado: Sistema v3.1 (Scrap Seas) pronto.")


db_manager = DatabaseManager(DB_PATH)
