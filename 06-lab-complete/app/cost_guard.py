"""Redis-backed Cost Guard Module."""
import time
import logging
from fastapi import HTTPException
import redis
from app.config import settings

logger = logging.getLogger(__name__)

PRICE_PER_1K_INPUT_TOKENS = 0.00015
PRICE_PER_1K_OUTPUT_TOKENS = 0.0006

# Connect to Redis
redis_client = None
if settings.redis_url:
    try:
        redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        redis_client.ping()
        logger.info("Cost guard successfully connected to Redis")
    except Exception as e:
        logger.warning(f"Cost guard failed to connect to Redis: {e}. Falling back to in-memory.")
        redis_client = None

# Fallback in-memory store for monthly costs
# key: user_id -> dict of {month: spent}
_in_memory_costs: dict[str, dict[str, float]] = {}

def _get_current_month() -> str:
    return time.strftime("%Y-%m")

def check_budget(user_id: str, estimated_cost: float):
    """
    Verify that the user has not exceeded their monthly budget.
    Raises HTTP 402 Payment Required if exceeded.
    """
    month = _get_current_month()
    limit = settings.monthly_budget_usd

    if redis_client is not None:
        try:
            key = f"budget:{user_id}:{month}"
            current_spent = float(redis_client.get(key) or 0.0)
            if current_spent + estimated_cost > limit:
                raise HTTPException(
                    status_code=402,
                    detail={
                        "error": "Monthly budget exceeded",
                        "limit_usd": limit,
                        "spent_usd": current_spent,
                        "requested_usd": estimated_cost,
                        "resets_at": "first day of next month"
                    }
                )
            return
        except HTTPException:
            raise
        except redis.RedisError as e:
            logger.error(f"Redis error in cost guard check: {e}. Falling back to memory.")

    # Fallback to memory
    user_costs = _in_memory_costs.setdefault(user_id, {})
    current_spent = user_costs.get(month, 0.0)
    if current_spent + estimated_cost > limit:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "Monthly budget exceeded",
                "limit_usd": limit,
                "spent_usd": current_spent,
                "requested_usd": estimated_cost,
                "resets_at": "first day of next month"
            }
        )

def record_usage(user_id: str, cost: float):
    """
    Record the cost of a transaction for a user in the budget.
    """
    month = _get_current_month()

    if redis_client is not None:
        try:
            key = f"budget:{user_id}:{month}"
            # Increment cost in Redis and set a 32-day expiry
            redis_client.incrbyfloat(key, cost)
            redis_client.expire(key, 32 * 24 * 3600)
            return
        except redis.RedisError as e:
            logger.error(f"Redis error in cost guard record: {e}. Falling back to memory.")

    # Fallback to memory
    user_costs = _in_memory_costs.setdefault(user_id, {})
    user_costs[month] = user_costs.get(month, 0.0) + cost

def get_usage(user_id: str) -> dict:
    """
    Return current spending stats for a user.
    """
    month = _get_current_month()
    limit = settings.monthly_budget_usd

    spent = 0.0
    if redis_client is not None:
        try:
            key = f"budget:{user_id}:{month}"
            spent = float(redis_client.get(key) or 0.0)
          # Convert to float and round
        except redis.RedisError as e:
            logger.error(f"Redis error in cost guard get_usage: {e}")
    else:
        spent = _in_memory_costs.get(user_id, {}).get(month, 0.0)

    return {
        "user_id": user_id,
        "month": month,
        "spent_usd": round(spent, 6),
        "limit_usd": limit,
        "remaining_usd": round(max(0.0, limit - spent), 6),
        "used_percentage": round((spent / limit) * 100, 2) if limit > 0 else 100.0
    }
