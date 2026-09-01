from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import routes_eval, routes_runs, routes_schematic, routes_skill
from app.config import FRONTEND_DIST

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


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "agent-eval-backend"}


# API routes must be registered first. StaticFiles then provides the production
# Vue application from the same origin in an offline deployment.
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
