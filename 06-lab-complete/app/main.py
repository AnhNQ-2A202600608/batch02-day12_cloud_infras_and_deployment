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
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Security, Depends, Request, Response
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

from app.config import settings

# Mock LLM (thay bằng OpenAI/Anthropic khi có API key)
from utils.mock_llm import ask as llm_ask

# ─────────────────────────────────────────────────────────
# Logging — JSON structured
# ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format='{"ts":"%(asctime)s","lvl":"%(levelname)s","msg":"%(message)s"}',
)
logger = logging.getLogger(__name__)

START_TIME = time.time()
_is_ready = False
_request_count = 0
_error_count = 0

# ─────────────────────────────────────────────────────────
# Redis Connection & Session Storage (Stateless)
# ─────────────────────────────────────────────────────────
import redis

_redis = None
if settings.redis_url:
    try:
        _redis = redis.from_url(settings.redis_url, decode_responses=True)
        _redis.ping()
        logger.info(json.dumps({"event": "redis_connect", "status": "ok"}))
    except Exception as e:
        logger.error(json.dumps({"event": "redis_connect", "status": "error", "detail": str(e)}))

_memory_store = {}

def save_session(session_id: str, data: dict, ttl_seconds: int = 3600):
    serialized = json.dumps(data)
    if _redis:
        try:
            _redis.setex(f"session:{session_id}", ttl_seconds, serialized)
        except Exception as e:
            logger.error(json.dumps({"event": "redis_error", "op": "save_session", "detail": str(e)}))
            _memory_store[f"session:{session_id}"] = data
    else:
        _memory_store[f"session:{session_id}"] = data

def load_session(session_id: str) -> dict:
    if _redis:
        try:
            data = _redis.get(f"session:{session_id}")
            return json.loads(data) if data else {}
        except Exception as e:
            logger.error(json.dumps({"event": "redis_error", "op": "load_session", "detail": str(e)}))
            return _memory_store.get(f"session:{session_id}", {})
    return _memory_store.get(f"session:{session_id}", {})

def append_to_history(session_id: str, role: str, content: str):
    session = load_session(session_id)
    history = session.get("history", [])
    history.append({
        "role": role,
        "content": content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    if len(history) > 20:
        history = history[-20:]
    session["history"] = history
    save_session(session_id, session)
    return history

# ─────────────────────────────────────────────────────────
# Rate Limiter (Redis-backed with In-memory Fallback)
# ─────────────────────────────────────────────────────────
_rate_windows: dict[str, deque] = defaultdict(deque)

def check_rate_limit(key: str):
    if _redis:
        try:
            now = time.time()
            redis_key = f"rate_limit:{key}"
            pipe = _redis.pipeline()
            pipe.zremrangebyscore(redis_key, 0, now - 60)
            pipe.zcard(redis_key)
            pipe.zadd(redis_key, {str(now): now})
            pipe.expire(redis_key, 60)
            _, count, _, _ = pipe.execute()
            if count > settings.rate_limit_per_minute:
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded: {settings.rate_limit_per_minute} req/min",
                    headers={"Retry-After": "60"},
                )
            return
        except HTTPException:
            raise
        except Exception as e:
            logger.error(json.dumps({"event": "redis_error", "op": "rate_limit", "detail": str(e)}))
            # Fall through to in-memory fallback below

    # In-memory fallback
    now = time.time()
    window = _rate_windows[key]
    while window and window[0] < now - 60:
        window.popleft()
    if len(window) >= settings.rate_limit_per_minute:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {settings.rate_limit_per_minute} req/min",
            headers={"Retry-After": "60"},
        )
    window.append(now)

# ─────────────────────────────────────────────────────────
# Cost Guard (Redis-backed with In-memory Fallback)
# ─────────────────────────────────────────────────────────
_daily_cost = 0.0
_cost_reset_day = time.strftime("%Y-%m-%d")

def check_and_record_cost(input_tokens: int, output_tokens: int):
    global _daily_cost, _cost_reset_day
    today = time.strftime("%Y-%m-%d")
    cost = (input_tokens / 1000) * 0.00015 + (output_tokens / 1000) * 0.0006

    if _redis:
        try:
            redis_key = f"cost_guard:{today}"
            current_cost_str = _redis.get(redis_key)
            current_cost = float(current_cost_str) if current_cost_str else 0.0
            
            if current_cost + cost > settings.daily_budget_usd:
                raise HTTPException(
                    status_code=402,
                    detail=f"Daily budget exceeded: {settings.daily_budget_usd} USD",
                )
            
            _redis.incrbyfloat(redis_key, cost)
            _redis.expire(redis_key, 86400)
            return
        except HTTPException:
            raise
        except Exception as e:
            logger.error(json.dumps({"event": "redis_error", "op": "cost_guard", "detail": str(e)}))
            # Fall through to in-memory fallback below

    # In-memory fallback
    if today != _cost_reset_day:
        _daily_cost = 0.0
        _cost_reset_day = today
    if _daily_cost + cost > settings.daily_budget_usd:
        raise HTTPException(
            status_code=402,
            detail=f"Daily budget exceeded: {settings.daily_budget_usd} USD",
        )
    _daily_cost += cost

# ─────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    if not api_key or api_key != settings.agent_api_key:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key. Include header: X-API-Key: <key>",
        )
    return api_key

# ─────────────────────────────────────────────────────────
# Lifespan
# ─────────────────────────────────────────────────────────
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

# ─────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────
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
        # Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers.pop("server", None)
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
        raise

# ─────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000,
                          description="Your question for the agent")
    session_id: str | None = Field(None, description="Optional conversation session ID")

class AskResponse(BaseModel):
    question: str
    answer: str
    model: str
    timestamp: str
    session_id: str | None = None

# ─────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────

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
    # Rate limit per API key
    check_rate_limit(_key[:8])  # use first 8 chars as key bucket

    # Budget check
    input_tokens = len(body.question.split()) * 2
    check_and_record_cost(input_tokens, 0)

    logger.info(json.dumps({
        "event": "agent_call",
        "q_len": len(body.question),
        "client": str(request.client.host) if request.client else "unknown",
    }))

    session_id = body.session_id or f"session-{_key[:8]}"
    append_to_history(session_id, "user", body.question)

    answer = llm_ask(body.question)

    output_tokens = len(answer.split()) * 2
    check_and_record_cost(0, output_tokens)

    append_to_history(session_id, "assistant", answer)

    return AskResponse(
        question=body.question,
        answer=answer,
        model=settings.llm_model,
        timestamp=datetime.now(timezone.utc).isoformat(),
        session_id=session_id,
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
    if _redis:
        try:
            _redis.ping()
        except Exception:
            raise HTTPException(503, "Redis not available")
    return {"ready": True}


@app.get("/metrics", tags=["Operations"])
def metrics(_key: str = Depends(verify_api_key)):
    """Basic metrics (protected)."""
    return {
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "total_requests": _request_count,
        "error_count": _error_count,
        "daily_cost_usd": round(_daily_cost, 4),
        "daily_budget_usd": settings.daily_budget_usd,
        "budget_used_pct": round(_daily_cost / settings.daily_budget_usd * 100, 1),
    }


# ─────────────────────────────────────────────────────────
# Graceful Shutdown
# ─────────────────────────────────────────────────────────
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
