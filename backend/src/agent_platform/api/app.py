from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Agent Platform", version="0.1.0")

    @app.get("/api/v1/health/live")
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
