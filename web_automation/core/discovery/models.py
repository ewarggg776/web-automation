"""Discovery models - generic dataclasses for discovered items."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, List
from abc import ABC, abstractmethod


@dataclass
class DiscoveredItem:
    """Base dataclass for any discovered item from a source."""
    name: str
    url: str
    source: str
    chain: str = "unknown"
    metadata: Dict[str, Any] = field(default_factory=dict)
    discovered_at: datetime = field(default_factory=datetime.now)
    verified: bool = False
    verified_at: Optional[datetime] = None
    score: float = 0.0
    enabled: bool = True
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "url": self.url,
            "source": self.source,
            "chain": self.chain,
            "metadata": self.metadata,
            "discovered_at": self.discovered_at.isoformat(),
            "verified": self.verified,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "score": self.score,
            "enabled": self.enabled,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DiscoveredItem":
        """Create from dictionary."""
        item = cls(
            name=data["name"],
            url=data["url"],
            source=data["source"],
            chain=data.get("chain", "unknown"),
            metadata=data.get("metadata", {}),
            score=data.get("score", 0.0),
            enabled=data.get("enabled", True),
            notes=data.get("notes", ""),
        )
        if "discovered_at" in data:
            item.discovered_at = datetime.fromisoformat(data["discovered_at"])
        if data.get("verified_at"):
            item.verified_at = datetime.fromisoformat(data["verified_at"])
            item.verified = data.get("verified", False)
        return item


@dataclass
class ScoredItem(DiscoveredItem):
    """Discovered item with computed score and ranking metadata."""
    raw_score: float = 0.0
    rank: int = 0
    score_breakdown: Dict[str, float] = field(default_factory=dict)

    def to_yaml_dict(self) -> Dict[str, Any]:
        """Convert to dict for YAML output (minimal fields)."""
        return {
            "name": self.name,
            "url": self.url,
            "chain": self.chain,
            "metadata": {k: v for k, v in self.metadata.items() 
                        if k in ("payout", "gas_cost", "cooldown_hours", "captcha_type")},
            "score": round(self.score, 4),
        }


class AbstractSource(ABC):
    """Abstract base class for all discovery sources."""

    def __init__(self, name: str, base_url: str, rate_limit: float = 1.0):
        self.name = name
        self.base_url = base_url
        self.rate_limit = rate_limit  # requests per second
        self._last_request = 0.0

    @abstractmethod
    async def discover(self) -> List[DiscoveredItem]:
        """Discover items from this source. Must be implemented by subclasses."""
        pass

    @abstractmethod
    async def validate(self, item: DiscoveredItem) -> bool:
        """Validate a discovered item. Must be implemented by subclasses."""
        pass

    async def rate_limited_request(self, *args, **kwargs):
        """Make a rate-limited request. Override in subclasses."""
        import asyncio
        import time
        now = time.time()
        wait = max(0, (1.0 / self.rate_limit) - (now - self._last_request))
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request = time.time()
        return await self._make_request(*args, **kwargs)

    @abstractmethod
    async def _make_request(self, *args, **kwargs):
        """Actual request implementation."""
        pass

    def normalize_url(self, url: str) -> str:
        """Normalize URL for deduplication."""
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(url)
        # Remove fragment, normalize scheme/host
        return urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip('/'),
            parsed.params,
            parsed.query,
            ''
        ))

    def infer_chain(self, url: str, metadata: Dict) -> str:
        """Infer chain from URL and metadata. Override for domain-specific logic."""
        return "unknown"