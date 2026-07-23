"""Storage layer - encrypted config, SQLite state, budget management."""

import os
import sqlite3
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from contextlib import contextmanager
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64

logger = logging.getLogger(__name__)


@dataclass
class EncryptedConfig:
    """Encrypted configuration storage."""
    config_path: Path
    _fernet: Fernet = None
    _data: Dict[str, Any] = None

    def __post_init__(self):
        self.config_path = Path(self.config_path)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

    def _get_fernet(self, password: str = None) -> Fernet:
        """Get or create Fernet cipher from password or stored key."""
        if self._fernet:
            return self._fernet

        key_path = self.config_path.with_suffix(".key")
        
        if password:
            # Derive key from password
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=b"web_automation_salt",  # In production, use random salt stored separately
                iterations=100000,
            )
            key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
            self._fernet = Fernet(key)
        elif key_path.exists():
            # Load existing key
            with open(key_path, "rb") as f:
                key = f.read()
            self._fernet = Fernet(key)
        else:
            # Generate new key
            key = Fernet.generate_key()
            with open(key_path, "wb") as f:
                f.write(key)
            # Restrict permissions
            os.chmod(key_path, 0o600)
            self._fernet = Fernet(key)
        
        return self._fernet

    def load(self, password: str = None) -> Dict[str, Any]:
        """Load and decrypt config."""
        if not self.config_path.exists():
            self._data = {}
            return self._data

        fernet = self._get_fernet(password)
        
        with open(self.config_path, "rb") as f:
            encrypted = f.read()
        
        try:
            decrypted = fernet.decrypt(encrypted)
            self._data = json.loads(decrypted.decode())
        except Exception as e:
            logger.warning(f"Failed to decrypt config: {e}")
            self._data = {}
        
        return self._data

    def save(self, data: Dict[str, Any], password: str = None):
        """Encrypt and save config."""
        fernet = self._get_fernet(password)
        self._data = data
        
        encrypted = fernet.encrypt(json.dumps(data).encode())
        
        with open(self.config_path, "wb") as f:
            f.write(encrypted)
        
        # Restrict permissions
        os.chmod(self.config_path, 0o600)

    def get(self, key: str, default: Any = None) -> Any:
        if self._data is None:
            self.load()
        return self._data.get(key, default)

    def set(self, key: str, value: Any, password: str = None):
        if self._data is None:
            self.load()
        self._data[key] = value
        self.save(self._data, password)

    def delete(self, key: str, password: str = None):
        if self._data is None:
            self.load()
        if key in self._data:
            del self._data[key]
            self.save(self._data, password)


@dataclass
class BudgetManager:
    """Manages budget tracking for automation runs."""
    db_path: Path
    _conn: sqlite3.Connection = None

    def __post_init__(self):
        self.db_path = Path(self.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS budgets (
                    id INTEGER PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    initial_budget REAL NOT NULL,
                    period_start TEXT NOT NULL,
                    period_days INTEGER DEFAULT 30,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS expenses (
                    id INTEGER PRIMARY KEY,
                    budget_id INTEGER NOT NULL,
                    service TEXT NOT NULL,
                    cost REAL NOT NULL,
                    description TEXT,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (budget_id) REFERENCES budgets(id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_expenses_budget 
                ON expenses(budget_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_expenses_timestamp 
                ON expenses(timestamp)
            """)
            conn.commit()

    @contextmanager
    def _get_conn(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
        try:
            yield self._conn
        except Exception:
            self._conn.rollback()
            raise

    def create_budget(self, name: str, amount: float, period_days: int = 30) -> int:
        """Create a new budget period."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT OR REPLACE INTO budgets 
                   (name, initial_budget, period_start, period_days)
                   VALUES (?, ?, ?, ?)""",
                (name, amount, datetime.now().isoformat(), period_days),
            )
            conn.commit()
            return cursor.lastrowid

    def get_budget(self, name: str = "default") -> Optional[Dict]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM budgets WHERE name = ?", (name,)
            ).fetchone()
            return dict(row) if row else None

    def spend(self, budget_name: str, service: str, cost: float, description: str = "") -> bool:
        """Record a spend against budget. Returns False if would exceed budget."""
        budget = self.get_budget(budget_name)
        if not budget:
            return False
        
        remaining = self.get_remaining(budget_name)
        if cost > remaining:
            return False
        
        with self._get_conn() as conn:
            budget_id = conn.execute(
                "SELECT id FROM budgets WHERE name = ?", (budget_name,)
            ).fetchone()[0]
            
            conn.execute(
                """INSERT INTO expenses (budget_id, service, cost, description)
                   VALUES (?, ?, ?, ?)""",
                (budget_id, service, cost, description),
            )
            conn.commit()
        return True

    def get_total_spent(self, budget_name: str = "default") -> float:
        budget = self.get_budget(budget_name)
        if not budget:
            return 0.0
        
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT SUM(cost) FROM expenses WHERE budget_id = ?",
                (budget["id"],),
            ).fetchone()
            return row[0] or 0.0

    def get_remaining(self, budget_name: str = "default") -> float:
        budget = self.get_budget(budget_name)
        if not budget:
            return 0.0
        return budget["initial_budget"] - self.get_total_spent(budget_name)

    def get_expense_history(self, budget_name: str = "default", limit: int = 100) -> List[Dict]:
        budget = self.get_budget(budget_name)
        if not budget:
            return []
        
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT service, cost, description, timestamp 
                   FROM expenses WHERE budget_id = ? 
                   ORDER BY timestamp DESC LIMIT ?""",
                (budget["id"], limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def reset_period(self, budget_name: str = "default"):
        """Reset budget period (archive old, start new)."""
        budget = self.get_budget(budget_name)
        if budget:
            with self._get_conn() as conn:
                conn.execute(
                    "UPDATE budgets SET period_start = ? WHERE name = ?",
                    (datetime.now().isoformat(), budget_name),
                )
                conn.commit()

    def export_report(self, budget_name: str = "default") -> Dict:
        budget = self.get_budget(budget_name)
        if not budget:
            return {}
        
        return {
            "budget": dict(budget),
            "total_spent": self.get_total_spent(budget_name),
            "remaining": self.get_remaining(budget_name),
            "utilization_pct": (self.get_total_spent(budget_name) / budget["initial_budget"] * 100) if budget["initial_budget"] > 0 else 0,
            "expenses": self.get_expense_history(budget_name),
        }


class StateDatabase:
    """SQLite-backed state database for tracking automation state."""

    def __init__(self, db_path: Union[str, Path]):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY,
                    source TEXT NOT NULL,
                    url TEXT UNIQUE NOT NULL,
                    name TEXT,
                    chain TEXT,
                    metadata TEXT,
                    enabled INTEGER DEFAULT 1,
                    score REAL DEFAULT 0,
                    discovered_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_items_source 
                ON items(source)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_items_chain 
                ON items(chain)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_items_enabled 
                ON items(enabled)
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS claims (
                    id INTEGER PRIMARY KEY,
                    item_id INTEGER NOT NULL,
                    address TEXT NOT NULL,
                    status TEXT NOT NULL,
                    tx_hash TEXT,
                    amount REAL DEFAULT 0,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (item_id) REFERENCES items(id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_claims_address 
                ON claims(address)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_claims_timestamp 
                ON claims(timestamp)
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS cooldowns (
                    key TEXT PRIMARY KEY,
                    expires_at TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.commit()

    @contextmanager
    def _get_conn(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
        try:
            yield self._conn
        except Exception:
            self._conn.rollback()
            raise

    # Item operations
    def upsert_item(self, item: Dict[str, Any]) -> int:
        """Insert or update a discovered item. Returns item ID."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "SELECT id FROM items WHERE url = ?", (item["url"],)
            )
            existing = cursor.fetchone()

            metadata = json.dumps(item.get("metadata", {}))

            if existing:
                item_id = existing[0]
                conn.execute("""
                    UPDATE items SET
                        name=?, chain=?, metadata=?, enabled=?, score=?, updated_at=?
                    WHERE id=?
                """, (
                    item.get("name"),
                    item.get("chain"),
                    metadata,
                    int(item.get("enabled", True)),
                    item.get("score", 0),
                    datetime.now().isoformat(),
                    item_id,
                ))
            else:
                cursor = conn.execute("""
                    INSERT INTO items (source, url, name, chain, metadata, enabled, score)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    item.get("source"),
                    item["url"],
                    item.get("name"),
                    item.get("chain"),
                    metadata,
                    int(item.get("enabled", True)),
                    item.get("score", 0),
                ))
                item_id = cursor.lastrowid
            
            conn.commit()
            return item_id

    def get_item(self, url: str) -> Optional[Dict]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM items WHERE url = ?", (url,)).fetchone()
            return dict(row) if row else None

    def get_all_items(self, enabled_only: bool = True, chain: str = None) -> List[Dict]:
        with self._get_conn() as conn:
            query = "SELECT * FROM items WHERE 1=1"
            params = []
            if enabled_only:
                query += " AND enabled = 1"
            if chain:
                query += " AND chain = ?"
                params.append(chain)
            query += " ORDER BY score DESC"
            
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def update_score(self, url: str, score: float):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE items SET score = ?, updated_at = ? WHERE url = ?",
                (score, datetime.now().isoformat(), url),
            )
            conn.commit()

    def set_enabled(self, url: str, enabled: bool):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE items SET enabled = ?, updated_at = ? WHERE url = ?",
                (int(enabled), datetime.now().isoformat(), url),
            )
            conn.commit()

    # Claim operations
    def record_claim(self, url: str, address: str, status: str, tx_hash: str = None, amount: float = 0) -> int:
        item = self.get_item(url)
        if not item:
            return 0
        
        with self._get_conn() as conn:
            cursor = conn.execute("""
                INSERT INTO claims (item_id, address, status, tx_hash, amount)
                VALUES (?, ?, ?, ?, ?)
            """, (item["id"], address, status, tx_hash, amount))
            conn.commit()
            return cursor.lastrowid

    def get_last_claim(self, url: str, address: str) -> Optional[datetime]:
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT timestamp FROM claims 
                WHERE item_id = (SELECT id FROM items WHERE url = ?) 
                AND address = ? AND status = 'success'
                ORDER BY timestamp DESC LIMIT 1
            """, (url, address)).fetchone()
            return datetime.fromisoformat(row[0]) if row else None

    def get_claim_stats(self, address: str = None) -> Dict:
        with self._get_conn() as conn:
            query = """
                SELECT status, COUNT(*) as count, SUM(amount) as total_amount
                FROM claims
            """
            params = []
            if address:
                query += " WHERE address = ?"
                params.append(address)
            query += " GROUP BY status"
            
            rows = conn.execute(query, params).fetchall()
            return {row["status"]: {"count": row["count"], "total": row["total_amount"] or 0} for row in rows}

    # Cooldown operations
    def set_cooldown(self, key: str, hours: int = 24):
        expires = datetime.now() + timedelta(hours=hours)
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO cooldowns (key, expires_at)
                VALUES (?, ?)
            """, (key, expires.isoformat()))
            conn.commit()

    def is_cooling(self, key: str) -> bool:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT expires_at FROM cooldowns WHERE key = ?", (key,)
            ).fetchone()
            if not row:
                return False
            expires = datetime.fromisoformat(row[0])
            if datetime.now() >= expires:
                conn.execute("DELETE FROM cooldowns WHERE key = ?", (key,))
                conn.commit()
                return False
            return True

    def get_cooldown_remaining(self, key: str) -> float:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT expires_at FROM cooldowns WHERE key = ?", (key,)
            ).fetchone()
            if not row:
                return 0
            expires = datetime.fromisoformat(row[0])
            remaining = (expires - datetime.now()).total_seconds()
            return max(0, remaining)

    def clear_cooldown(self, key: str):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM cooldowns WHERE key = ?", (key,))
            conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None