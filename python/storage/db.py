import sqlite3
import os
import json
from typing import List, Dict, Any, Optional

# Ensure cache directory exists
CACHE_DIR = os.path.expanduser("~/.cache/emigo/")
os.makedirs(CACHE_DIR, exist_ok=True)
DB_PATH = os.path.join(CACHE_DIR, "emigo_symbols.db")

class Storage:
    def __init__(self, db_path: str = DB_PATH):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        cursor = self.conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS symbols (
            symbol TEXT PRIMARY KEY,
            kind TEXT,
            package TEXT,
            prefix TEXT,
            file TEXT,
            line INTEGER,
            interactive BOOLEAN,
            args TEXT, -- JSON list
            doc_excerpt TEXT,
            keybindings TEXT, -- JSON list
            provides TEXT, -- JSON list
            requires TEXT, -- JSON list
            last_modified INTEGER,
            usage_count INTEGER DEFAULT 0
        )
        """)
        # A persistent log of successfully executed intents.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS intent_journal (
            intent_id TEXT PRIMARY KEY,
            intent_data TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)
        self.conn.commit()

    def update_symbols_batch(self, symbols: List[Dict[str, Any]]):
        """
        Insert or update a batch of symbols.
        If a symbol exists and the new `last_modified` is newer, it updates.
        """
        cursor = self.conn.cursor()
        for symbol_data in symbols:
            # Check if we should update
            cursor.execute("SELECT last_modified FROM symbols WHERE symbol = ?", (symbol_data['symbol'],))
            existing = cursor.fetchone()
            if existing and existing['last_modified'] >= symbol_data.get('last_modified', 0):
                continue # Skip update if our data is not newer

            # Prepare data for insertion/replacement
            data_tuple = (
                symbol_data.get('symbol'),
                symbol_data.get('kind'),
                symbol_data.get('package'),
                symbol_data.get('prefix'),
                symbol_data.get('file'),
                symbol_data.get('line'),
                symbol_data.get('interactive'),
                json.dumps(symbol_data.get('args')),
                symbol_data.get('doc_excerpt'),
                json.dumps(symbol_data.get('keybindings')),
                json.dumps(symbol_data.get('provides')),
                json.dumps(symbol_data.get('requires')),
                symbol_data.get('last_modified'),
            )
            cursor.execute("""
            INSERT OR REPLACE INTO symbols (symbol, kind, package, prefix, file, line, interactive, args, doc_excerpt, keybindings, provides, requires, last_modified, usage_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT usage_count FROM symbols WHERE symbol = ?), 0))
            """, data_tuple + (symbol_data.get('symbol'),))
        self.conn.commit()

    def search_symbols(self, query: str, kind: Optional[str] = None, package: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Search symbols in the database, ordered by usage_count descending.
        Increments usage_count for found symbols.
        """
        cursor = self.conn.cursor()
        sql_query = "SELECT * FROM symbols WHERE symbol LIKE ?"
        params = [f"%{query}%"]

        if kind:
            sql_query += " AND kind = ?"
            params.append(kind)
        if package:
            sql_query += " AND package = ?"
            params.append(package)

        sql_query += " ORDER BY usage_count DESC, symbol ASC"

        cursor.execute(sql_query, params)
        rows = cursor.fetchall()

        results = []
        found_symbols = []
        for row in rows:
            result_dict = dict(row)
            # Deserialize JSON fields
            for key in ['args', 'keybindings', 'provides', 'requires']:
                if result_dict[key]:
                    result_dict[key] = json.loads(result_dict[key])
            results.append(result_dict)
            found_symbols.append(result_dict['symbol'])

        # Increment usage_count for found symbols
        if found_symbols:
            # Create placeholders for the IN clause
            placeholders = ', '.join('?' for _ in found_symbols)
            cursor.execute(f"UPDATE symbols SET usage_count = usage_count + 1 WHERE symbol IN ({placeholders})", found_symbols)
            self.conn.commit()

        return results

    def get_doc_digest(self, path: str) -> Optional[int]:
        """Gets the last known modification time for a file."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT mtime FROM doc_digests WHERE path = ?", (path,))
        result = cursor.fetchone()
        return result[0] if result else None

    def update_doc_digest(self, path: str, mtime: int):
        """Updates the modification time for a file."""
        cursor = self.conn.cursor()
        # This table doesn't exist, so let's create it.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS doc_digests (
            path TEXT PRIMARY KEY,
            mtime INTEGER NOT NULL
        )
        """)
        cursor.execute("INSERT OR REPLACE INTO doc_digests (path, mtime) VALUES (?, ?)", (path, mtime))
        self.conn.commit()

    def log_intent(self, intent_data: Dict[str, Any]):
        """Logs a successfully executed intent to the journal."""
        cursor = self.conn.cursor()
        intent_id = intent_data.get("intent_id")
        if not intent_id:
            return
        cursor.execute("INSERT INTO intent_journal (intent_id, intent_data) VALUES (?, ?)",
                       (intent_id, json.dumps(intent_data)))
        self.conn.commit()

    def close(self):
        self.conn.close()

# Singleton instance
storage_manager = Storage()
