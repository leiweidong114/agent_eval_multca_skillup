from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import routes_eval, routes_runs, routes_schematic, routes_skill
from app.model_eval import model_eval_app

app = FastAPI(
    title="Agent Eval Multca Skillup API",
    version="0.1.0",
    description="Local, login-free Agent Skill evaluation backend",
)

# 允许前端开发服务器跨域调用（Vite 默认 http://localhost:5173）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
