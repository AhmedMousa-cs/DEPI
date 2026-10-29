from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.jobs import router as jobs_router
from app.api.projects import router as projects_router
from app.api.sources import router as sources_router
from app.api.team import router as team_router
from app.api.users import router as users_router

app = FastAPI(title="NEXUS")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5500", "http://127.0.0.1:5500"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(projects_router)
app.include_router(sources_router)
app.include_router(team_router)
app.include_router(jobs_router)


@app.get("/health")
def health():
    return {"status": "ok"}
