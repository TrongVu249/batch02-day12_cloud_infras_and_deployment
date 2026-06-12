"""Redis-backed Sliding Window Rate Limiter Module."""
import time
import uuid
import logging
from collections import defaultdict, deque
from fastapi import HTTPException
import redis
from app.config import settings

logger = logging.getLogger(__name__)

# Connect to Redis
redis_client = None
if settings.redis_url:
    try:
        redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        redis_client.ping()
        logger.info("Rate limiter successfully connected to Redis")
    except Exception as e:
        logger.warning(f"Rate limiter failed to connect to Redis: {e}. Falling back to in-memory.")
        redis_client = None

# Fallback in-memory store
_in_memory_windows: dict[str, deque] = defaultdict(deque)

def check_rate_limit(user_id: str):
    """
    Checks rate limit for a user (10 requests per minute by default).
    If exceeded, raises HTTP 429 Too Many Requests.
    Uses Redis sliding window if connected, otherwise falls back to memory.
    """
    limit = settings.rate_limit_per_minute
    window_seconds = 60
    now = time.time()

    if redis_client is not None:
        try:
            key = f"rate_limit:{user_id}"
            pipe = redis_client.pipeline()
            # Remove timestamps older than the sliding window (60s)
            pipe.zremrangebyscore(key, 0, now - window_seconds)
            # Add current request with a unique value to avoid overrides
            pipe.zadd(key, {f"{now}-{uuid.uuid4().hex}": now})
            # Count elements in the window
            pipe.zcard(key)
            # Set key expiry slightly longer than the window
            pipe.expire(key, window_seconds + 5)
            # Execute pipeline
            results = pipe.execute()
            current_requests = results[2]  # index 2 is zcard count
            
            if current_requests > limit:
                # Get the oldest timestamp in the zset to compute retry after
                oldest_elements = redis_client.zrange(key, 0, 0, withscores=True)
                retry_after = 60
                if oldest_elements:
                    _, oldest_ts = oldest_elements[0]
                    retry_after = max(1, int(oldest_ts + window_seconds - now))
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded: {limit} req/min",
                    headers={"Retry-After": str(retry_after)},
                )
            return
        except redis.RedisError as e:
            logger.error(f"Redis error in rate limiter: {e}. Falling back to memory.")

    # Fallback to in-memory sliding window
    window = _in_memory_windows[user_id]
    while window and window[0] < now - window_seconds:
        window.popleft()

    if len(window) >= limit:
        oldest = window[0]
        retry_after = max(1, int(oldest + window_seconds - now))
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {limit} req/min",
            headers={"Retry-After": str(retry_after)},
        )
    window.append(now)
