"""Generic merger for deduplication and scoring of discovered items."""

import yaml
import os
from typing import List, Dict, Any, Callable, Optional
from datetime import datetime
from collections import defaultdict

from .models import DiscoveredItem, ScoredItem


class GenericMerger:
    """Merge, deduplicate, and score discovered items from multiple sources."""

    def __init__(
        self,
        key_func: Callable[[DiscoveredItem], str] = None,
        score_func: Callable[[DiscoveredItem], float] = None,
        config_path: str = "config/discovery.yaml",
    ):
        """
        Args:
            key_func: Function to generate deduplication key from item (default: normalized URL)
            score_func: Function to compute score from item (default: metadata-based)
            config_path: Path to save merged results
        """
        self.key_func = key_func or (lambda item: item.normalize_url(item.url))
        self.score_func = score_func or self._default_score
        self.config_path = config_path
        self.seen: Dict[str, ScoredItem] = {}

    def _default_score(self, item: DiscoveredItem) -> float:
        """Default scoring based on metadata."""
        score = 0.0
        meta = item.metadata
        
        # Score based on payout if present
        if "payout" in meta:
            score += min(float(meta.get("payout", 0)) * 100, 50)
        
        # Score based on gas cost (lower is better)
        if "gas_cost" in meta:
            gas = float(meta.get("gas_cost", 1))
            score += max(0, 20 - gas * 1000)
        
        # Score based on cooldown (shorter is better)
        if "cooldown_hours" in meta:
            cooldown = int(meta.get("cooldown_hours", 24))
            score += max(0, 30 - cooldown)
        
        # Bonus for known sources
        source_scores = {
            "faucetlink": 15,
            "quicknode": 10,
            "alchemy": 10,
            "chainlink": 10,
        }
        score += source_scores.get(item.source.lower(), 0)
        
        return max(0, min(100, score))

    def merge(self, items: List[DiscoveredItem]) -> List[ScoredItem]:
        """Merge items from multiple sources, deduplicate, and score."""
        # Group by deduplication key
        grouped = defaultdict(list)
        for item in items:
            key = self.key_func(item)
            grouped[key].append(item)

        # For each group, pick best item and create ScoredItem
        merged = []
        for key, group in grouped.items():
            # Pick item with highest metadata completeness
            best = max(group, key=lambda x: len(x.metadata))
            best.verified = any(g.verified for g in group)
            best.verified_at = max(
                (g.verified_at for g in group if g.verified_at),
                default=None
            )
            best.metadata = {**best.metadata, **{
                k: v for g in group for k, v in g.metadata.items()
            }}
            
            # Score the item
            score = self.score_func(best)
            
            scored = ScoredItem(
                name=best.name,
                url=best.url,
                source=best.source,
                chain=best.chain,
                metadata=best.metadata,
                discovered_at=best.discovered_at,
                verified=best.verified,
                verified_at=best.verified_at,
                score=score,
                enabled=best.enabled,
                notes=best.notes,
            )
            scored.raw_score = score
            self.seen[key] = scored
            merged.append(scored)

        # Sort by score descending
        merged.sort(key=lambda x: x.score, reverse=True)
        
        # Assign ranks
        for i, item in enumerate(merged):
            item.rank = i + 1
            
        return merged

    def export_yaml(self, items: List[ScoredItem], output_path: str = None) -> str:
        """Export scored items to YAML config file."""
        path = output_path or self.config_path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        config = {
            "items": [item.to_yaml_dict() for item in items],
            "merged_at": datetime.now().isoformat(),
            "total_items": len(items),
        }
        
        with open(path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        
        return path

    def load_yaml(self, path: str = None) -> List[ScoredItem]:
        """Load scored items from YAML config."""
        path = path or self.config_path
        if not os.path.exists(path):
            return []
        
        with open(path) as f:
            config = yaml.safe_load(f) or {}
        
        items = []
        for item_data in config.get("items", []):
            item = ScoredItem(
                name=item_data["name"],
                url=item_data["url"],
                chain=item_data.get("chain", "unknown"),
                metadata=item_data.get("metadata", {}),
                source=item_data.get("source", "unknown"),
                score=item_data.get("score", 0.0),
                enabled=item_data.get("enabled", True),
            )
            items.append(item)
        
        return items

    def get_stats(self, items: List[ScoredItem]) -> Dict[str, Any]:
        """Get statistics about merged items."""
        by_source = defaultdict(int)
        by_chain = defaultdict(int)
        enabled = sum(1 for i in items if i.enabled)
        verified = sum(1 for i in items if i.verified)
        
        for item in items:
            by_source[item.source] += 1
            by_chain[item.chain] += 1
        
        return {
            "total": len(items),
            "enabled": enabled,
            "disabled": len(items) - enabled,
            "verified": verified,
            "by_source": dict(by_source),
            "by_chain": dict(by_chain),
            "avg_score": sum(i.score for i in items) / len(items) if items else 0,
        }


class ItemRegistry:
    """Registry for tracking items across runs with persistent storage."""
    
    def __init__(self, db_path: str = "data/registry.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        import sqlite3
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    key TEXT PRIMARY KEY,
                    name TEXT,
                    url TEXT,
                    source TEXT,
                    chain TEXT,
                    metadata TEXT,
                    score REAL,
                    enabled INTEGER,
                    first_seen TEXT,
                    last_seen TEXT,
                    verified INTEGER,
                    verified_at TEXT
                )
            """)
            conn.commit()
    
    def register(self, item: ScoredItem):
        import sqlite3
        key = item.normalize_url(item.url)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO items 
                (key, name, url, source, chain, metadata, score, enabled, first_seen, last_seen, verified, verified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                key, item.name, item.url, item.source, item.chain,
                yaml.dump(item.metadata), item.score, int(item.enabled),
                item.discovered_at.isoformat(), datetime.now().isoformat(),
                int(item.verified), item.verified_at.isoformat() if item.verified_at else None
            ))
            conn.commit()
    
    def get(self, url: str) -> Optional[ScoredItem]:
        import sqlite3
        key = DiscoveredItem("", url, "").normalize_url(url)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM items WHERE key = ?", (key,)).fetchone()
            if not row:
                return None
            return ScoredItem(
                name=row[1], url=row[2], source=row[3], chain=row[4],
                metadata=yaml.safe_load(row[5]) or {}, score=row[6],
                enabled=bool(row[7]),
                discovered_at=datetime.fromisoformat(row[8]),
                last_seen=datetime.fromisoformat(row[9]),
                verified=bool(row[10]),
                verified_at=datetime.fromisoformat(row[11]) if row[11] else None,
            )
    
    def get_all(self, enabled_only: bool = False) -> List[ScoredItem]:
        import sqlite3
        query = "SELECT * FROM items"
        if enabled_only:
            query += " WHERE enabled = 1"
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query).fetchall()
            return [
                ScoredItem(
                    name=r[1], url=r[2], source=r[3], chain=r[4],
                    metadata=yaml.safe_load(r[5]) or {}, score=r[6],
                    enabled=bool(r[7]),
                    discovered_at=datetime.fromisoformat(r[8]),
                    verified=bool(r[10]),
                    verified_at=datetime.fromisoformat(r[11]) if r[11] else None,
                ) for r in rows
            ]