import os
import base64
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FS_DOMAIN  = os.environ["FS_DOMAIN"]
FS_API_KEY = os.environ["FS_API_KEY"]
AUTH       = base64.b64encode(f"{FS_API_KEY}:X".encode()).decode()
BASE_URL   = f"https://{FS_DOMAIN}/api/v2"

@app.get("/health")
async def health():
    return {"status": "ok", "domain": FS_DOMAIN}

@app.api_route("/{path:path}", methods=["GET","POST","PUT","DELETE"])
async def proxy(path: str, request: Request):
    url  = f"{BASE_URL}/{path}"
    body = await request.body()
    params = dict(request.query_params)
    headers = {
        "Authorization": f"Basic {AUTH}",
        "Content-Type":  "application/json",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            r = await client.request(
                method  = request.method,
                url     = url,
                headers = headers,
                params  = params,
                content = body or None,
            )
            return JSONResponse(status_code=r.status_code, content=r.json())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
