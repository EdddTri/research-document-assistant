from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from research_core.config import settings
from research_core.db import engine
from . import auth, chat, documents


def create_app():
    cfg = settings()
    app = FastAPI(title="Research Document Assistant", version="1.0.0")
    app.add_middleware(CORSMiddleware, allow_origins=[cfg.frontend_origin], allow_credentials=True,
                       allow_methods=["GET", "POST", "DELETE"], allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"])

    @app.middleware("http")
    async def browser_security(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            # Cookie operations require Origin. Bearer-only API clients can omit it.
            origin = request.headers.get("origin")
            cookie_auth = request.url.path in {"/auth/login", "/auth/register", "/auth/refresh", "/auth/logout"}
            if (origin and origin != cfg.frontend_origin) or (cookie_auth and origin != cfg.frontend_origin):
                return JSONResponse({"detail": "Untrusted request origin"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    for router in (auth.router, documents.router, chat.router):
        app.include_router(router)

    @app.get("/health")
    def health():
        with engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok"}

    return app


app = create_app()
