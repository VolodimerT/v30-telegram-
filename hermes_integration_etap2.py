"""
hermes_integration_etap2.py — Advanced Hermès Integration (Etap 2)
================================================================================
Features:
  ✅ Retry logic с exponential backoff
  ✅ Caching анализов (5-60 мин в зависимости от типа)
  ✅ Асинхронная очередь задач
  ✅ Детальное логирование и мониторинг
  ✅ Rate limiting (защита от перегрузок)
  ✅ Feedback loop для обучения
  ✅ Circuit breaker pattern
  ✅ Health checks и auto-recovery
"""

import os
import asyncio
import aiohttp
import logging
import json
from typing import Optional, List, Dict, Tuple
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from dataclasses import dataclass, asdict
from enum import Enum
import hashlib
import time

UTC = timezone.utc
logger = logging.getLogger("hermes_integration_etap2")


# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

HERMES_API_URL = os.getenv("HERMES_API_URL", "http://localhost:8000")
TIMEOUT = 15  # секунд
MAX_RETRIES = 3
INITIAL_RETRY_DELAY = 1  # секунда
MAX_RETRY_DELAY = 30  # секунд
RATE_LIMIT_REQUESTS = 100  # реквестов
RATE_LIMIT_WINDOW = 60  # в секундах
CACHE_TTL_ANALYSIS = 300  # 5 минут для анализа
CACHE_TTL_STATS = 60  # 1 минута для статистики
QUEUE_MAX_SIZE = 500
HEALTH_CHECK_INTERVAL = 30  # секунд


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Working normally
    OPEN = "open"          # Failing, rejecting requests
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CacheEntry:
    """Single cache entry."""
    data: Dict
    timestamp: datetime
    ttl: int  # seconds
    hits: int = 0
    
    def is_expired(self) -> bool:
        """Check if cache entry is expired."""
        return (datetime.now(UTC) - self.timestamp).total_seconds() > self.ttl


@dataclass
class RateLimitState:
    """Rate limit state."""
    requests: List[float] = None
    
    def __post_init__(self):
        if self.requests is None:
            self.requests = []
    
    def can_request(self, now: float = None) -> bool:
        """Check if request is allowed."""
        if now is None:
            now = time.time()
        
        # Remove old requests outside the window
        self.requests = [req for req in self.requests 
                        if now - req < RATE_LIMIT_WINDOW]
        
        return len(self.requests) < RATE_LIMIT_REQUESTS
    
    def add_request(self, now: float = None):
        """Add request to the rate limit."""
        if now is None:
            now = time.time()
        self.requests.append(now)


@dataclass
class CircuitBreaker:
    """Circuit breaker for Hermès service."""
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    failure_threshold: int = 5
    success_count: int = 0
    success_threshold: int = 2
    last_failure: Optional[datetime] = None
    recovery_timeout: int = 60  # секунд
    
    def record_success(self):
        """Record successful request."""
        self.failure_count = 0
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self.state = CircuitState.CLOSED
                self.success_count = 0
                logger.info("🟢 Circuit breaker CLOSED - Hermès is recovering")
    
    def record_failure(self):
        """Record failed request."""
        self.failure_count += 1
        self.last_failure = datetime.now(UTC)
        
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.error(f"🔴 Circuit breaker OPEN - Hermès unavailable ({self.failure_count} failures)")
    
    def can_attempt(self) -> bool:
        """Check if request can be attempted."""
        if self.state == CircuitState.CLOSED:
            return True
        
        if self.state == CircuitState.OPEN:
            # Check if recovery timeout has passed
            if self.last_failure:
                time_since_failure = (datetime.now(UTC) - self.last_failure).total_seconds()
                if time_since_failure > self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    self.success_count = 0
                    logger.info("🟡 Circuit breaker HALF_OPEN - testing recovery")
                    return True
            return False
        
        # HALF_OPEN - allow limited requests
        return True


# ═══════════════════════════════════════════════════════════════════════════
# ADVANCED HERMÈS CLIENT
# ═══════════════════════════════════════════════════════════════════════════

class AdvancedHermèsClient:
    """Advanced Hermès client with caching, retry logic, and monitoring."""
    
    def __init__(self, base_url: str = HERMES_API_URL):
        self.base_url = base_url
        self.session = None
        
        # Cache
        self.cache: Dict[str, CacheEntry] = {}
        
        # Rate limiting
        self.rate_limit = RateLimitState()
        
        # Circuit breaker
        self.circuit_breaker = CircuitBreaker()
        
        # Metrics
        self.metrics = {
            "requests_total": 0,
            "requests_success": 0,
            "requests_failed": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "retries": 0,
            "circuit_breaker_triggers": 0,
        }
        
        # Queue for async tasks
        self.task_queue: asyncio.Queue = None
        self.worker_task = None
    
    async def init(self):
        """Initialize the client."""
        self.task_queue = asyncio.Queue(maxsize=QUEUE_MAX_SIZE)
        self.worker_task = asyncio.create_task(self._queue_worker())
        logger.info("✅ Advanced Hermès Client initialized")
    
    async def shutdown(self):
        """Shutdown the client."""
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass
        await self.close()
    
    async def _ensure_session(self):
        """Ensure aiohttp session is open."""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
    
    async def close(self):
        """Close the session."""
        if self.session:
            await self.session.close()
    
    def _get_cache_key(self, method: str, data: Dict) -> str:
        """Generate cache key from method and data."""
        data_str = json.dumps(data, sort_keys=True)
        hash_obj = hashlib.md5(data_str.encode())
        return f"{method}:{hash_obj.hexdigest()}"
    
    def _get_from_cache(self, key: str) -> Optional[Dict]:
        """Get data from cache if available."""
        if key not in self.cache:
            self.metrics["cache_misses"] += 1
            return None
        
        entry = self.cache[key]
        if entry.is_expired():
            del self.cache[key]
            self.metrics["cache_misses"] += 1
            return None
        
        entry.hits += 1
        self.metrics["cache_hits"] += 1
        logger.debug(f"Cache hit for {key}")
        return entry.data
    
    def _set_cache(self, key: str, data: Dict, ttl: int):
        """Set data in cache."""
        self.cache[key] = CacheEntry(
            data=data,
            timestamp=datetime.now(UTC),
            ttl=ttl
        )
        logger.debug(f"Cached {key} (TTL: {ttl}s)")
    
    def _clean_expired_cache(self):
        """Remove expired cache entries."""
        expired_keys = [
            key for key, entry in self.cache.items()
            if entry.is_expired()
        ]
        for key in expired_keys:
            del self.cache[key]
        if expired_keys:
            logger.debug(f"Cleaned {len(expired_keys)} expired cache entries")
    
    async def _make_request(
        self,
        method: str,
        endpoint: str,
        json_data: Optional[Dict] = None,
        retries: int = 0
    ) -> Tuple[Optional[Dict], bool]:
        """Make HTTP request with retry logic."""
        
        # Check circuit breaker
        if not self.circuit_breaker.can_attempt():
            self.metrics["circuit_breaker_triggers"] += 1
            logger.warning("🔴 Circuit breaker OPEN - request rejected")
            return None, False
        
        # Check rate limit
        if not self.rate_limit.can_request():
            logger.warning("⚠️ Rate limit exceeded - queuing request")
            return None, False
        
        self.rate_limit.add_request()
        self.metrics["requests_total"] += 1
        
        try:
            await self._ensure_session()
            
            url = f"{self.base_url}{endpoint}"
            timeout = aiohttp.ClientTimeout(total=TIMEOUT)
            
            async with self.session.request(
                method,
                url,
                json=json_data,
                timeout=timeout
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.circuit_breaker.record_success()
                    self.metrics["requests_success"] += 1
                    return data, True
                else:
                    raise Exception(f"HTTP {resp.status}: {await resp.text()}")
        
        except Exception as e:
            self.metrics["requests_failed"] += 1
            self.circuit_breaker.record_failure()
            
            # Retry with exponential backoff
            if retries < MAX_RETRIES:
                self.metrics["retries"] += 1
                delay = min(
                    INITIAL_RETRY_DELAY * (2 ** retries),
                    MAX_RETRY_DELAY
                )
                logger.warning(
                    f"Request failed ({retries + 1}/{MAX_RETRIES}): {e}. "
                    f"Retrying in {delay}s..."
                )
                await asyncio.sleep(delay)
                return await self._make_request(method, endpoint, json_data, retries + 1)
            else:
                logger.error(f"Request failed after {MAX_RETRIES} retries: {e}")
                return None, False
    
    async def health_check(self) -> bool:
        """Check if Hermès is alive."""
        data, success = await self._make_request("GET", "/health")
        return success
    
    async def analyze_matches(
        self,
        matches: List[Dict],
        mode: str = "NORMAL",
        strict: bool = False,
        use_cache: bool = True
    ) -> Optional[Dict]:
        """Send matches to Hermès for analysis with caching."""
        
        # Generate cache key
        cache_key = self._get_cache_key("analyze", {
            "matches": matches,
            "mode": mode,
            "strict": strict
        })
        
        # Check cache
        if use_cache:
            cached = self._get_from_cache(cache_key)
            if cached:
                return cached
        
        # Make request
        payload = {
            "matches": matches,
            "mode": mode,
            "strict": strict,
        }
        
        data, success = await self._make_request("POST", "/analyze", payload)
        
        if success and data:
            self._set_cache(cache_key, data, CACHE_TTL_ANALYSIS)
            return data
        
        return None
    
    async def learn_result(
        self,
        match: str,
        selection: str,
        result: str,  # WIN, LOSS, PUSH
        pnl: float,
        confidence: float,
        queue_async: bool = True
    ) -> bool:
        """Report bet result to Hermès (can be queued async)."""
        
        payload = {
            "match": match,
            "selection": selection,
            "result": result,
            "pnl": pnl,
            "confidence_was": confidence,
        }
        
        if queue_async and self.task_queue:
            try:
                await self.task_queue.put(("learn", payload))
                logger.debug(f"Queued learning task for {match}")
                return True
            except asyncio.QueueFull:
                logger.warning("Task queue full - processing synchronously")
        
        # Synchronous processing
        data, success = await self._make_request("POST", "/learn", payload)
        
        if success:
            logger.info(f"Learned: {match} {selection} {result}")
        
        return success
    
    async def get_stats(self, use_cache: bool = True) -> Optional[Dict]:
        """Get Hermès statistics."""
        
        cache_key = self._get_cache_key("stats", {})
        
        if use_cache:
            cached = self._get_from_cache(cache_key)
            if cached:
                return cached
        
        data, success = await self._make_request("GET", "/stats")
        
        if success and data:
            self._set_cache(cache_key, data, CACHE_TTL_STATS)
            return data
        
        return None
    
    async def _queue_worker(self):
        """Background worker for async task queue."""
        logger.info("🔄 Queue worker started")
        
        while True:
            try:
                # Get task from queue with timeout
                task_type, payload = await asyncio.wait_for(
                    self.task_queue.get(),
                    timeout=1.0
                )
                
                # Process task
                if task_type == "learn":
                    await self._make_request("POST", "/learn", payload)
                    self.task_queue.task_done()
                
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                logger.info("🛑 Queue worker stopped")
                break
            except Exception as e:
                logger.error(f"Queue worker error: {e}")
    
    def get_metrics(self) -> Dict:
        """Get client metrics."""
        self._clean_expired_cache()
        return {
            **self.metrics,
            "cache_size": len(self.cache),
            "circuit_breaker_state": self.circuit_breaker.state.value,
            "rate_limit_requests": len(self.rate_limit.requests),
            "queue_size": self.task_queue.qsize() if self.task_queue else 0,
        }


# ═══════════════════════════════════════════════════════════════════════════
# GLOBAL INSTANCE
# ═══════════════════════════════════════════════════════════════════════════

hermes_client = AdvancedHermèsClient()


# ═══════════════════════════════════════════════════════════════════════════
# HIGH-LEVEL INTEGRATION FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

async def init_hermes():
    """Initialize Hermès client."""
    await hermes_client.init()


async def shutdown_hermes():
    """Shutdown Hermès client."""
    await hermes_client.shutdown()


async def enrich_picks_with_hermes(
    picks: List[Dict],
    mode: str = "NORMAL",
    use_cache: bool = True
) -> Dict:
    """
    Enrich bot picks with Hermès analysis.
    
    Args:
        picks: List of picks from bot
        mode: Bot mode (NORMAL, FROZEN, GROWTH, EMERGENCY)
        use_cache: Use cached results if available
    
    Returns:
        Dict with enriched picks and Hermès recommendations
    """
    
    if not picks:
        return {
            "total_picks": 0,
            "hermès_analyzed": 0,
            "enriched_picks": [],
            "hermes_available": False,
            "cached": False,
        }
    
    # Check Hermès availability
    hermes_healthy = await hermes_client.health_check()
    
    if not hermes_healthy:
        logger.warning("Hermès unhealthy - returning picks without enhancement")
        return {
            "total_picks": len(picks),
            "hermès_analyzed": 0,
            "enriched_picks": picks,
            "hermes_available": False,
            "cached": False,
        }
    
    # Convert picks to Hermès format
    matches = []
    for pick in picks:
        match = {
            "match": pick.get("match", ""),
            "sport": pick.get("sport", ""),
            "market": pick.get("market", ""),
            "selection": pick.get("selection", ""),
            "best_odds": pick.get("best_odds", 0.0),
            "book_count": pick.get("book_count", 0),
            "ev_calibrated": pick.get("ev_calibrated", 0.0),
            "ci_low": pick.get("ci_low", 0.0),
            "data_quality": pick.get("data_quality", "LOW"),
            "decision": pick.get("decision", "PASS"),
            "stake": pick.get("stake", 0.0),
        }
        matches.append(match)
    
    # Get Hermès analysis
    hermes_response = await hermes_client.analyze_matches(
        matches=matches,
        mode=mode,
        strict=False,
        use_cache=use_cache
    )
    
    if not hermes_response:
        return {
            "total_picks": len(picks),
            "hermès_analyzed": 0,
            "enriched_picks": picks,
            "hermes_available": False,
            "cached": False,
        }
    
    # Enrich picks with recommendations
    enriched_picks = []
    hermes_recs = {rec["match"]: rec for rec in hermes_response.get("recommendations", [])}
    
    for pick in picks:
        match_name = pick.get("match", "")
        rec = hermes_recs.get(match_name)
        
        if rec:
            pick["hermes_recommendation"] = rec.get("hermès_recommendation")
            pick["hermes_confidence"] = rec.get("confidence", 0.0)
            pick["hermes_reason"] = rec.get("reason", "")
            pick["stake_adjustment"] = rec.get("suggested_stake_adjustment", 1.0)
            
            # Calculate adjusted stake
            original_stake = pick.get("stake", 0.0)
            adjusted_stake = original_stake * pick["stake_adjustment"]
            pick["adjusted_stake"] = round(adjusted_stake, 2)
        else:
            pick["hermes_recommendation"] = "UNKNOWN"
            pick["hermes_confidence"] = 0.0
            pick["hermes_reason"] = ""
            pick["stake_adjustment"] = 1.0
            pick["adjusted_stake"] = pick.get("stake", 0.0)
        
        enriched_picks.append(pick)
    
    return {
        "total_picks": len(picks),
        "hermès_analyzed": len(hermes_recs),
        "enriched_picks": enriched_picks,
        "hermes_summary": hermes_response.get("summary"),
        "hermes_available": True,
        "cached": hermes_response.get("cached", False),
    }


async def report_bet_result_async(
    match: str,
    selection: str,
    result: str,  # WIN, LOSS, PUSH
    pnl: float,
    confidence: float = 0.5,
    async_queue: bool = True
) -> bool:
    """Report bet result to Hermès (async)."""
    
    return await hermes_client.learn_result(
        match=match,
        selection=selection,
        result=result,
        pnl=pnl,
        confidence=confidence,
        queue_async=async_queue
    )


async def get_hermes_stats() -> Optional[Dict]:
    """Get Hermès AI statistics."""
    return await hermes_client.get_stats(use_cache=True)


async def get_integration_metrics() -> Dict:
    """Get integration metrics and health."""
    return {
        "client_metrics": hermes_client.get_metrics(),
        "timestamp": datetime.now(UTC).isoformat(),
        "hermes_health": await hermes_client.health_check(),
    }


def format_integration_status(metrics: Dict) -> str:
    """Format integration status for Telegram."""
    
    msg = "🔗 HERMÈS INTEGRATION STATUS\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━\n"
    
    client_metrics = metrics.get("client_metrics", {})
    msg += f"✅ Requests: {client_metrics.get('requests_success', 0)}\n"
    msg += f"❌ Failed: {client_metrics.get('requests_failed', 0)}\n"
    msg += f"🔄 Retries: {client_metrics.get('retries', 0)}\n"
    msg += f"💾 Cache hits: {client_metrics.get('cache_hits', 0)}\n"
    msg += f"🟢 Circuit: {client_metrics.get('circuit_breaker_state', 'unknown')}\n"
    msg += f"📦 Queue: {client_metrics.get('queue_size', 0)}/{QUEUE_MAX_SIZE}\n"
    msg += f"❤️ Hermes: {'✅' if metrics.get('hermes_health') else '❌'}\n"
    
    return msg


# ═══════════════════════════════════════════════════════════════════════════
# TESTING
# ═══════════════════════════════════════════════════════════════════════════

async def test_advanced_integration():
    """Test advanced Hermès integration."""
    
    await init_hermes()
    
    try:
        print("🧪 Testing Advanced Hermès Integration (ETAP 2)\n")
        
        # Health check
        print("1️⃣ Health Check...")
        alive = await hermes_client.health_check()
        print(f"   Hermès: {'✅ Alive' if alive else '❌ Down'}\n")
        
        # Test caching
        print("2️⃣ Testing Caching...")
        sample_picks = [
            {
                "match": "Chelsea vs Liverpool",
                "sport": "football",
                "market": "h2h",
                "selection": "Chelsea",
                "best_odds": 2.10,
                "book_count": 8,
                "ev_calibrated": 8.5,
                "ci_low": 2.5,
                "data_quality": "HIGH",
                "decision": "CORE",
                "stake": 50,
            }
        ]
        
        # First call (cache miss)
        start = time.time()
        result1 = await enrich_picks_with_hermes(sample_picks, use_cache=True)
        time1 = time.time() - start
        
        # Second call (cache hit)
        start = time.time()
        result2 = await enrich_picks_with_hermes(sample_picks, use_cache=True)
        time2 = time.time() - start
        
        print(f"   First call: {time1:.2f}s")
        print(f"   Cached call: {time2:.2f}s (cache: {result2.get('cached', False)})")
        print(f"   Speedup: {time1/time2:.1f}x\n")
        
        # Test metrics
        print("3️⃣ Integration Metrics...")
        metrics = await get_integration_metrics()
        print(format_integration_status(metrics))
        
        # Test learning (async queue)
        print("\n4️⃣ Testing Async Learning Queue...")
        success = await report_bet_result_async(
            match="Chelsea vs Liverpool",
            selection="Chelsea",
            result="WIN",
            pnl=75.5,
            confidence=0.85,
            async_queue=True
        )
        print(f"   Queued: {success}")
        
        # Wait for queue to process
        await asyncio.sleep(2)
        
        # Final metrics
        print("\n5️⃣ Final Metrics...")
        metrics = await get_integration_metrics()
        client_metrics = metrics.get("client_metrics", {})
        print(f"   Total requests: {client_metrics.get('requests_total', 0)}")
        print(f"   Cache size: {client_metrics.get('cache_size', 0)}")
        print(f"   Queue size: {client_metrics.get('queue_size', 0)}")
        
        print("\n✅ ETAP 2 Testing Complete!")
    
    finally:
        await shutdown_hermes()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    asyncio.run(test_advanced_integration())
