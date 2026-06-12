"""
Production AI Agent — Kết hợp tất cả Day 12 concepts

Checklist:
  ✅ Config từ environment (12-factor)
  ✅ Structured JSON logging
  ✅ API Key authentication
  ✅ Rate limiting
  ✅ Cost guard
  ✅ Input validation (Pydantic)
  ✅ Health check + Readiness probe
  ✅ Graceful shutdown
  ✅ Security headers
  ✅ CORS
  ✅ Error handling
"""
import os
import time
import signal
import logging
import json
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Security, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn
import redis

from app.config import settings
from app.auth import verify_api_key
from app.rate_limiter import check_rate_limit
from app.cost_guard import check_budget, record_usage, get_usage

# Mock LLM
from utils.mock_llm import ask as llm_ask

# Logging — JSON structured
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format='{"ts":"%(asctime)s","lvl":"%(levelname)s","msg":"%(message)s"}',
)
logger = logging.getLogger(__name__)

START_TIME = time.time()
_is_ready = False
_request_count = 0
_error_count = 0

# Connect to Redis for session history
redis_client = None
if settings.redis_url:
    try:
        redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        redis_client.ping()
        logger.info("Main application successfully connected to Redis for history")
    except Exception as e:
        logger.warning(f"Main application failed to connect to Redis for history: {e}")
        redis_client = None

# Fallback memory store for conversation history
_in_memory_history: dict[str, list[dict]] = {}

def save_history(user_id: str, role: str, content: str):
    message = {
        "role": role,
        "content": content,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    if redis_client is not None:
        try:
            key = f"history:{user_id}"
            redis_client.rpush(key, json.dumps(message))
            redis_client.ltrim(key, -20, -1)
            redis_client.expire(key, 3600)
            return
        except Exception as e:
            logger.error(f"Redis history save error: {e}")
    
    hist = _in_memory_history.setdefault(user_id, [])
    hist.append(message)
    if len(hist) > 20:
        _in_memory_history[user_id] = hist[-20:]

def load_history(user_id: str) -> list[dict]:
    if redis_client is not None:
        try:
            key = f"history:{user_id}"
            items = redis_client.lrange(key, 0, -1)
            return [json.loads(item) for item in items]
        except Exception as e:
            logger.error(f"Redis history load error: {e}")
            
    return _in_memory_history.get(user_id, [])

# Lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _is_ready
    logger.info(json.dumps({
        "event": "startup",
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
    }))
    time.sleep(0.1)  # simulate init
    _is_ready = True
    logger.info(json.dumps({"event": "ready"}))

    yield

    _is_ready = False
    logger.info(json.dumps({"event": "shutdown"}))

# App
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)

@app.middleware("http")
async def request_middleware(request: Request, call_next):
    global _request_count, _error_count
    start = time.time()
    _request_count += 1
    try:
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if "server" in response.headers:
            del response.headers["server"]
        duration = round((time.time() - start) * 1000, 1)
        logger.info(json.dumps({
            "event": "request",
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "ms": duration,
        }))
        return response
    except Exception as e:
        _error_count += 1
        logger.error(json.dumps({
            "event": "request_error",
            "method": request.method,
            "path": request.url.path,
            "error": str(e)
        }))
        raise

# Models
class AskRequest(BaseModel):
    user_id: str = Field(..., description="ID of the user making the request")
    question: str = Field(..., min_length=1, max_length=2000,
                          description="Your question for the agent")

class AskResponse(BaseModel):
    question: str
    answer: str
    model: str
    timestamp: str

# Endpoints
@app.get("/", tags=["Info"])
def root():
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "endpoints": {
            "ask": "POST /ask (requires X-API-Key)",
            "health": "GET /health",
            "ready": "GET /ready",
        },
    }

@app.post("/ask", response_model=AskResponse, tags=["Agent"])
async def ask_agent(
    body: AskRequest,
    request: Request,
    _key: str = Depends(verify_api_key),
):
    """
    Send a question to the AI agent.

    **Authentication:** Include header `X-API-Key: <your-key>`
    """
    # Rate limit check
    check_rate_limit(body.user_id)

    # Cost guard check (estimated input)
    input_tokens = len(body.question.split()) * 2
    estimated_input_cost = (input_tokens / 1000) * 0.00015
    check_budget(body.user_id, estimated_input_cost)

    logger.info(json.dumps({
        "event": "agent_call",
        "user_id": body.user_id,
        "q_len": len(body.question),
        "client": str(request.client.host) if request.client else "unknown",
    }))

    # Get conversation history
    _history = load_history(body.user_id)

    # Call LLM
    answer = llm_ask(body.question)

    # Save turns to stateless history in Redis/Memory
    save_history(body.user_id, "user", body.question)
    save_history(body.user_id, "assistant", answer)

    # Record actual cost
    output_tokens = len(answer.split()) * 2
    actual_cost = estimated_input_cost + (output_tokens / 1000) * 0.0006
    record_usage(body.user_id, actual_cost)

    return AskResponse(
        question=body.question,
        answer=answer,
        model=settings.llm_model,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

@app.get("/health", tags=["Operations"])
def health():
    """Liveness probe. Platform restarts container if this fails."""
    status = "ok"
    checks = {"llm": "mock" if not settings.openai_api_key else "openai"}
    return {
        "status": status,
        "version": settings.app_version,
        "environment": settings.environment,
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "total_requests": _request_count,
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

@app.get("/ready", tags=["Operations"])
def ready():
    """Readiness probe. Load balancer stops routing here if not ready."""
    if not _is_ready:
        raise HTTPException(503, "Not ready")
    if redis_client is not None:
        try:
            redis_client.ping()
        except Exception:
            raise HTTPException(503, "Redis connection failed")
    return {"ready": True}

@app.get("/metrics", tags=["Operations"])
def metrics(user_id: str = "default", _key: str = Depends(verify_api_key)):
    """Basic metrics per user (protected)."""
    usage = get_usage(user_id)
    return {
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "total_requests": _request_count,
        "error_count": _error_count,
        "spent_usd": usage["spent_usd"],
        "limit_usd": usage["limit_usd"],
        "budget_used_pct": usage["used_percentage"],
    }

# Graceful Shutdown
def _handle_signal(signum, _frame):
    logger.info(json.dumps({"event": "signal", "signum": signum}))

signal.signal(signal.SIGTERM, _handle_signal)

if __name__ == "__main__":
    logger.info(f"Starting {settings.app_name} on {settings.host}:{settings.port}")
    logger.info(f"API Key: {settings.agent_api_key[:4]}****")
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        timeout_graceful_shutdown=30,
    )
