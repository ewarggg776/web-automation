"""Automation runner - orchestrates the full pipeline with dry-run/live modes."""

import asyncio
import logging
from typing import List, Dict, Any, Callable, Awaitable, Optional, TypeVar, Generic
from dataclasses import dataclass
from abc import ABC, abstractmethod
from datetime import datetime

from .discovery import (
    DiscoveredItem, 
    ScoredItem, 
    AbstractSource, 
    GenericMerger, 
    GenericScheduler,
    ItemRegistry,
)
from .validators import (
    HTTPValidator,
    CaptchaDetector,
    SelectorProbe,
    ContentExtractor,
    ValidatorPipeline,
    ValidationResult,
)
from .storage import (
    StateDatabase,
    BudgetManager,
    EncryptedConfig,
)

logger = logging.getLogger(__name__)

T = TypeVar('T', bound=DiscoveredItem)


@dataclass
class RunResult:
    """Result of a single automation run."""
    item: DiscoveredItem
    status: str  # success, failed, skipped, cooldown, dry_run
    result: Dict[str, Any] = None
    error: str = None
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class AutomationRunner(Generic[T]):
    """Main automation runner - orchestrates the full pipeline."""

    def __init__(
        self,
        source: AbstractSource,
        state_db: StateDatabase,
        budget: BudgetManager = None,
        config: EncryptedConfig = None,
        max_concurrent: int = 3,
        dry_run: bool = False,
        wallet_address: str = None,
    ):
        self.source = source
        self.state_db = state_db
        self.budget = budget
        self.config = config
        self.dry_run = dry_run
        self.wallet_address = wallet_address
        
        # Validators
        self.http_validator = HTTPValidator()
        self.captcha_detector = None  # Needs browser
        self.selector_probe = None    # Needs browser
        self.content_extractor = ContentExtractor()
        
        # Pipeline components
        self.merger = GenericMerger()
        self.scheduler = GenericScheduler(max_concurrent=max_concurrent)
        self.registry = ItemRegistry()
        
        # Execution state
        self.results: List[RunResult] = []
        self._shutdown = False

    def set_browser(self, browser):
        """Set Playwright browser for CAPTCHA/selector validation."""
        self.captcha_detector = CaptchaDetector(browser)
        self.selector_probe = SelectorProbe(browser)
        self.content_extractor.browser = browser

    async def run_discovery(self) -> List[T]:
        """Run discovery phase - find and validate items."""
        logger.info("Starting discovery phase...")
        
        # Discover from source
        raw_items = await self.source.discover()
        logger.info(f"Discovered {len(raw_items)} raw items")
        
        # Validate reachability
        if raw_items:
            urls = [item.url for item in raw_items]
            http_results = await self.http_validator.validate_batch(urls)
            
            valid_items = []
            for item in raw_items:
                result = http_results.get(item.url)
                if result and result.valid:
                    valid_items.append(item)
                else:
                    logger.debug(f"Skipping unreachable: {item.url}")
            
            logger.info(f"Valid items after HTTP check: {len(valid_items)}")
            return valid_items
        
        return []

    async def run_validation(self, items: List[T]) -> List[T]:
        """Run deep validation on items (CAPTCHA, selectors, content)."""
        logger.info(f"Running deep validation on {len(items)} items...")
        
        if not self.captcha_detector or not self.selector_probe:
            logger.warning("Browser not set - skipping deep validation")
            return items
        
        validated = []
        for item in items:
            # Check CAPTCHA
            captcha_result = await self.captcha_detector.validate(item.url)
            if not captcha_result.valid:
                logger.info(f"CAPTCHA detected on {item.url}: {captcha_result.details.get('primary')}")
                item.metadata["captcha_type"] = captcha_result.details.get("primary", "unknown")
                # Don't skip - just note it
            
            # Probe selectors if we have expected ones
            if hasattr(self.source, 'expected_selectors'):
                selector_result = await self.selector_probe.validate(
                    item.url, 
                    self.source.expected_selectors
                )
                if not selector_result.valid:
                    logger.warning(f"Missing selectors for {item.url}: {selector_result.details['missing']}")
            
            validated.append(item)
        
        return validated

    async def run_claim(self, item: T, **kwargs) -> RunResult:
        """Execute claim for a single item."""
        # Check cooldown
        if self.state_db:
            last_claim = self.state_db.get_last_claim(item.url, self.wallet_address)
            if last_claim:
                cooldown_hours = item.metadata.get("cooldown_hours", 24)
                elapsed = datetime.now() - last_claim
                if elapsed.total_seconds() < cooldown_hours * 3600:
                    return RunResult(
                        item=item,
                        status="cooldown",
                        error=f"Cooldown active: {cooldown_hours - elapsed.total_seconds()/3600:.1f}h remaining",
                    )
        
        # Check budget
        if self.budget:
            estimated_cost = self._estimate_claim_cost(item)
            if not self.budget.spend("default", item.source, estimated_cost, f"Claim from {item.name}"):
                return RunResult(
                    item=item,
                    status="failed",
                    error="Insufficient budget",
                )
        
        if self.dry_run:
            return RunResult(
                item=item,
                status="dry_run",
                result={"estimated_gas": item.metadata.get("gas_cost", 0.001)},
            )
        
        # Actual claim execution - delegate to source
        try:
            result = await self.source.claim(item, self.wallet_address, **kwargs)
            
            # Record claim
            if self.state_db:
                self.state_db.record_claim(
                    item.url,
                    self.wallet_address,
                    result.get("status", "success"),
                    result.get("tx_hash"),
                    result.get("amount", 0),
                )
            
            return RunResult(
                item=item,
                status=result.get("status", "success"),
                result=result,
            )
        except Exception as e:
            logger.error(f"Claim failed for {item.name}: {e}")
            return RunResult(
                item=item,
                status="failed",
                error=str(e),
            )

    def _estimate_claim_cost(self, item: T) -> float:
        """Estimate gas cost for a claim."""
        return item.metadata.get("gas_cost", 0.001)  # Default 0.001 ETH

    async def run_all(self, dry_run: bool = None) -> List[RunResult]:
        """Run the full automation pipeline."""
        if dry_run is not None:
            self.dry_run = dry_run
        
        mode = "DRY RUN" if self.dry_run else "LIVE"
        logger.info(f"Starting automation run ({mode})")
        
        # Phase 1: Discovery
        items = await self.run_discovery()
        if not items:
            logger.warning("No items discovered")
            return []
        
        # Phase 2: Validation
        items = await self.run_validation(items)
        
        # Phase 3: Merge and score
        merged = self.merger.merge(items)
        logger.info(f"Merged into {len(merged)} unique items")
        
        # Phase 4: Execute claims
        self.results = []
        for item in merged:
            if self._shutdown:
                break
            
            result = await self.run_claim(item)
            self.results.append(result)
            
            # Log result
            status_emoji = {
                "success": "✅",
                "failed": "❌",
                "skipped": "⏭️",
                "cooldown": "⏳",
                "dry_run": "🔍",
            }.get(result.status, "❓")
            
            logger.info(f"{status_emoji} {item.name}: {result.status}")
            if result.error:
                logger.debug(f"  Error: {result.error}")
            
            # Small delay between claims
            await asyncio.sleep(2)
        
        # Summary
        summary = self.get_summary()
        logger.info(f"Run complete: {summary}")
        
        return self.results

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of run results."""
        if not self.results:
            return {"total": 0}
        
        status_counts = {}
        for r in self.results:
            status_counts[r.status] = status_counts.get(r.status, 0) + 1
        
        return {
            "total": len(self.results),
            "by_status": status_counts,
            "success_rate": status_counts.get("success", 0) / len(self.results) if self.results else 0,
        }

    def shutdown(self):
        """Graceful shutdown."""
        self._shutdown = True
        self.scheduler.shutdown()
        if self.state_db:
            self.state_db.close()
        asyncio.create_task(self.http_validator.close())


class AutomationPipeline:
    """Higher-level pipeline for running multiple sources sequentially."""

    def __init__(
        self,
        state_db: StateDatabase,
        budget: BudgetManager = None,
        config: EncryptedConfig = None,
        max_concurrent: int = 3,
    ):
        self.state_db = state_db
        self.budget = budget
        self.config = config
        self.max_concurrent = max_concurrent
        self.sources: List[AbstractSource] = []

    def add_source(self, source: AbstractSource):
        self.sources.append(source)

    async def run_all(self, dry_run: bool = False, wallet_address: str = None) -> List[RunResult]:
        """Run all sources sequentially."""
        all_results = []
        
        for source in self.sources:
            runner = AutomationRunner(
                source=source,
                state_db=self.state_db,
                budget=self.budget,
                config=self.config,
                max_concurrent=self.max_concurrent,
                dry_run=dry_run,
                wallet_address=wallet_address,
            )
            
            results = await runner.run_all(dry_run)
            all_results.extend(results)
        
        return all_results