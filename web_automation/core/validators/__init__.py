"""Generic validators for HTTP, CAPTCHA, selectors, and content extraction."""

import asyncio
import re
import logging
from typing import Dict, List, Optional, Any, Set
from dataclasses import dataclass
from urllib.parse import urlparse
from abc import ABC, abstractmethod

import aiohttp
from playwright.async_api import Page, Browser
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of a validation check."""
    valid: bool
    score: float = 0.0
    details: Dict[str, Any] = None
    error: str = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}


class BaseValidator(ABC):
    """Abstract base class for validators."""

    @abstractmethod
    async def validate(self, target: Any, **kwargs) -> ValidationResult:
        """Validate a target. Must be implemented by subclasses."""
        pass


class HTTPValidator(BaseValidator):
    """Validates HTTP endpoints for reachability and basic health."""

    def __init__(
        self,
        timeout: int = 10,
        allowed_codes: Set[int] = None,
        follow_redirects: bool = True,
        user_agent: str = None,
    ):
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.allowed_codes = allowed_codes or {200, 201, 202, 204, 301, 302, 304, 307, 308}
        self.follow_redirects = follow_redirects
        self.user_agent = user_agent or "Mozilla/5.0 (compatible; WebAuto/0.1)"
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers={"User-Agent": self.user_agent},
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def validate(self, url: str, **kwargs) -> ValidationResult:
        """Validate a URL is reachable and returns allowed status."""
        session = await self._get_session()
        
        try:
            async with session.get(
                url,
                allow_redirects=self.follow_redirects,
                **kwargs,
            ) as response:
                status = response.status
                content_type = response.headers.get("Content-Type", "")
                content_length = response.headers.get("Content-Length", "unknown")
                
                # Check status code
                if status not in self.allowed_codes:
                    return ValidationResult(
                        valid=False,
                        error=f"HTTP {status} not in allowed codes",
                        details={"status": status, "content_type": content_type},
                    )
                
                # Try to read some content
                try:
                    content = await response.read()
                    content_size = len(content)
                except Exception:
                    content_size = 0
                
                return ValidationResult(
                    valid=True,
                    score=1.0,
                    details={
                        "status": status,
                        "content_type": content_type,
                        "content_length": content_length,
                        "content_size": content_size,
                        "final_url": str(response.url),
                    },
                )
                
        except asyncio.TimeoutError:
            return ValidationResult(
                valid=False,
                error="Request timeout",
                details={"timeout": self.timeout.total},
            )
        except aiohttp.ClientError as e:
            return ValidationResult(
                valid=False,
                error=f"Client error: {type(e).__name__}",
                details={"error": str(e)},
            )
        except Exception as e:
            return ValidationResult(
                valid=False,
                error=f"Unexpected error: {type(e).__name__}",
                details={"error": str(e)},
            )

    async def validate_batch(
        self, 
        urls: List[str], 
        concurrency: int = 5,
        **kwargs,
    ) -> Dict[str, ValidationResult]:
        """Validate multiple URLs concurrently."""
        semaphore = asyncio.Semaphore(concurrency)
        
        async def validate_one(url: str) -> tuple[str, ValidationResult]:
            async with semaphore:
                return url, await self.validate(url, **kwargs)
        
        results = await asyncio.gather(*[validate_one(url) for url in urls])
        return dict(results)


class CaptchaDetector(BaseValidator):
    """Detects CAPTCHA challenges on web pages."""

    # CAPTCHA indicators
    CAPTCHA_PATTERNS = {
        "recaptcha": [
            r"recaptcha",
            r"g-recaptcha",
            r"google\.com/recaptcha",
            r"data-sitekey",
        ],
        "hcaptcha": [
            r"hcaptcha",
            r"h-captcha",
            r"hcaptcha\.com",
            r"data-sitekey.*hcaptcha",
        ],
        "turnstile": [
            r"turnstile",
            r"cf-turnstile",
            r"cloudflare.*challenge",
            r"turnstile\.js",
        ],
        "cloudflare": [
            r"cloudflare.*challenge",
            r"__cf_chl",
            r"ray id",
            r"attention required",
            r"checking your browser",
        ],
        "generic": [
            r"captcha",
            r"prove you're human",
            r"verify you are human",
            r"security check",
            r"please verify",
        ],
    }

    def __init__(self, browser: Browser = None):
        self.browser = browser
        self._page: Optional[Page] = None

    async def _get_page(self) -> Page:
        if self._page is None or self._page.is_closed():
            if not self.browser:
                raise ValueError("Browser instance required")
            self._page = await self.browser.new_page()
        return self._page

    async def validate(self, url: str, **kwargs) -> ValidationResult:
        """Check if page has CAPTCHA challenges."""
        page = await self._get_page()
        
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)  # Wait for dynamic content
            
            content = await page.content()
            return self._analyze_content(content, url)
            
        except Exception as e:
            return ValidationResult(
                valid=False,
                error=f"Navigation failed: {e}",
                details={"error": str(e)},
            )

    def _analyze_content(self, html: str, url: str) -> ValidationResult:
        """Analyze HTML content for CAPTCHA indicators."""
        html_lower = html.lower()
        detected = {}
        
        for captcha_type, patterns in self.CAPTCHA_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, html_lower, re.IGNORECASE):
                    detected[captcha_type] = detected.get(captcha_type, 0) + 1
        
        if not detected:
            return ValidationResult(
                valid=True,  # No CAPTCHA detected = good
                score=1.0,
                details={"captcha_types": [], "url": url},
            )
        
        # Determine primary CAPTCHA type
        primary = max(detected.items(), key=lambda x: x[1])[0]
        
        return ValidationResult(
            valid=False,  # CAPTCHA detected = blocked
            score=0.0,
            details={
                "captcha_types": list(detected.keys()),
                "primary": primary,
                "counts": detected,
                "url": url,
            },
            error=f"CAPTCHA detected: {primary}",
        )

    async def validate_via_playwright(self, page: Page) -> ValidationResult:
        """Validate using existing page (faster)."""
        content = await page.content()
        return self._analyze_content(content, page.url)


class SelectorProbe(BaseValidator):
    """Probes for CSS/XPath selectors on pages."""

    COMMON_SELECTORS = {
        "address_input": [
            "input[name='address']",
            "input[id='address']",
            "input[placeholder*='address']",
            "input[placeholder*='wallet']",
            "input[type='text']",
        ],
        "submit_btn": [
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('claim')",
            "button:has-text('submit')",
            "button:has-text('get')",
        ],
        "success_msg": [
            ".success",
            ".alert-success",
            "[class*='success']",
            ":has-text('success')",
            ":has-text('sent')",
            ":has-text('claimed')",
        ],
        "error_msg": [
            ".error",
            ".alert-error",
            "[class*='error']",
            ":has-text('error')",
            ":has-text('failed')",
        ],
        "captcha_iframe": [
            "iframe[src*='recaptcha']",
            "iframe[src*='hcaptcha']",
            "iframe[src*='turnstile']",
            "iframe[src*='cloudflare']",
        ],
    }

    def __init__(self, browser: Browser = None):
        self.browser = browser
        self._page: Optional[Page] = None

    async def _get_page(self) -> Page:
        if self._page is None or self._page.is_closed():
            if not self.browser:
                raise ValueError("Browser instance required")
            self._page = await self.browser.new_page()
        return self._page

    async def validate(self, url: str, selectors: Dict[str, str] = None, **kwargs) -> ValidationResult:
        """Probe page for expected selectors."""
        page = await self._get_page()
        
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            
            # Use provided selectors or defaults
            test_selectors = selectors or self.COMMON_SELECTORS
            found = {}
            missing = []
            
            for name, selector_list in test_selectors.items():
                if isinstance(selector_list, str):
                    selector_list = [selector_list]
                
                for selector in selector_list:
                    try:
                        element = await page.query_selector(selector)
                        if element:
                            found[name] = selector
                            break
                    except Exception:
                        continue
                
                if name not in found:
                    missing.append(name)
            
            success = len(missing) == 0
            score = 1.0 - (len(missing) / len(test_selectors))
            
            return ValidationResult(
                valid=success,
                score=max(0, score),
                details={
                    "found": found,
                    "missing": missing,
                    "total_tested": len(test_selectors),
                },
                error=f"Missing selectors: {missing}" if missing else None,
            )
            
        except Exception as e:
            return ValidationResult(
                valid=False,
                error=f"Selector probe failed: {e}",
                details={"error": str(e)},
            )

    async def find_best_selector(
        self, 
        url: str, 
        target_type: str,
        custom_selectors: List[str] = None,
    ) -> ValidationResult:
        """Find the best selector for a target type."""
        selectors = custom_selectors or self.COMMON_SELECTORS.get(target_type, [])
        return await self.validate(url, {target_type: selectors})


class ContentExtractor(BaseValidator):
    """Extracts structured content from pages."""

    def __init__(self, browser: Browser = None):
        self.browser = browser
        self._page: Optional[Page] = None

    async def _get_page(self) -> Page:
        if self._page is None or self._page.is_closed():
            if not self.browser:
                raise ValueError("Browser instance required")
            self._page = await self.browser.new_page()
        return self._page

    async def validate(self, url: str, extractors: Dict[str, str] = None, **kwargs) -> ValidationResult:
        """Extract content using CSS selectors."""
        page = await self._get_page()
        
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1000)
            
            extractors = extractors or {
                "title": "title",
                "description": "meta[name='description']",
                "main_content": "main, article, .content, #content",
                "forms": "form",
                "buttons": "button, input[type='submit']",
            }
            
            extracted = {}
            for name, selector in extractors.items():
                try:
                    elements = await page.query_selector_all(selector)
                    if elements:
                        texts = []
                        for el in elements[:5]:  # Limit to 5
                            text = await el.inner_text()
                            if text and text.strip():
                                texts.append(text.strip()[:500])
                        extracted[name] = texts
                except Exception:
                    extracted[name] = []
            
            return ValidationResult(
                valid=True,
                score=1.0,
                details={"extracted": extracted, "url": url},
            )
            
        except Exception as e:
            return ValidationResult(
                valid=False,
                error=f"Extraction failed: {e}",
                details={"error": str(e)},
            )

    async def extract_with_soup(self, url: str, **kwargs) -> ValidationResult:
        """Extract using BeautifulSoup (lighter weight)."""
        import aiohttp
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url) as resp:
                    html = await resp.text()
                    soup = BeautifulSoup(html, "lxml")
                    
                    extracted = {
                        "title": soup.title.string if soup.title else None,
                        "description": soup.find("meta", {"name": "description"}).get("content", "") if soup.find("meta", {"name": "description"}) else None,
                        "links": [a.get("href") for a in soup.find_all("a", href=True)][:20],
                        "forms": len(soup.find_all("form")),
                        "inputs": len(soup.find_all("input")),
                    }
                    
                    return ValidationResult(
                        valid=True,
                        score=1.0,
                        details={"extracted": extracted, "url": url},
                    )
            except Exception as e:
                return ValidationResult(
                    valid=False,
                    error=f"Soup extraction failed: {e}",
                    details={"error": str(e)},
                )


class ValidatorPipeline:
    """Composable pipeline of validators."""

    def __init__(self):
        self.validators: List[BaseValidator] = []

    def add(self, validator: BaseValidator) -> "ValidatorPipeline":
        self.validators.append(validator)
        return self

    async def run(self, target: Any, **kwargs) -> Dict[str, ValidationResult]:
        """Run all validators on target."""
        results = {}
        for validator in self.validators:
            try:
                result = await validator.validate(target, **kwargs)
                results[type(validator).__name__] = result
                
                # Stop on first failure if critical
                if not result.valid and getattr(validator, "critical", False):
                    break
            except Exception as e:
                results[type(validator).__name__] = ValidationResult(
                    valid=False,
                    error=f"Validator crashed: {e}",
                )
        return results

    def overall_valid(self, results: Dict[str, ValidationResult]) -> bool:
        return all(r.valid for r in results.values())

    def overall_score(self, results: Dict[str, ValidationResult]) -> float:
        if not results:
            return 0.0
        return sum(r.score for r in results.values()) / len(results)