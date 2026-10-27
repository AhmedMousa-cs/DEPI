from fastapi import FastAPI

app = FastAPI(title="NEXUS")


@app.get("/health")
def health():
    return {"status": "ok"}