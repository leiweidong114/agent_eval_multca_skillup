from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from agent_eval.env_config import apply_root_env

apply_root_env(Path(__file__).resolve().parents[1])

from app.api import routes_auth, routes_eval, routes_runs, routes_schematic, routes_skill
from app.model_eval import model_eval_app

app = FastAPI(
    title="Agent Eval Multca Skillup API",
    version="0.1.0",
    description="Agent Skill evaluation backend with claimed employee identity",
)

# 允许前端开发服务器跨域调用（Vite 默认 http://localhost:5173）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_claimed_login(request: Request, call_next):
    path = request.url.path
    protected = path.startswith("/api/") or path.startswith("/prism/api/")
    public = path == "/api/health" or path.startswith("/api/auth/") or not protected
    if not public:
        from app.auth import identity_from_request

        if identity_from_request(request, required=False) is None:
            return JSONResponse({"detail": "请先登录"}, status_code=401)
    return await call_next(request)

app.include_router(routes_auth.router)
app.include_router(routes_skill.router)
app.include_router(routes_eval.router)
app.include_router(routes_runs.router)
app.include_router(routes_schematic.router)

# Full model/question-bank evaluation subsystem migrated from model-agent-eval.
# Keep it under an explicit prefix so its `/api/*` routes and static SPA do not
# collide with the existing Skill evaluation API.
app.mount("/prism", model_eval_app, name="model-agent-eval")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "agent-eval-backend"}
