import os
import base64
import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FS_DOMAIN  = os.environ.get("FS_DOMAIN", "")
FS_API_KEY = os.environ.get("FS_API_KEY", "")
AUTH       = base64.b64encode(f"{FS_API_KEY}:X".encode()).decode()

@app.get("/health")
async def health():
    return {"status": "ok", "domain": FS_DOMAIN, "auth_set": bool(FS_API_KEY)}

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy(path: str, request: Request):
    url = f"https://{FS_DOMAIN}/api/v2/{path.lstrip('/')}"
    params = dict(request.query_params)
    body = await request.body()

    headers = {
        "Authorization": f"Basic {AUTH}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            r = await client.request(
                method  = request.method,
                url     = url,
                headers = headers,
                params  = params,
                content = body if body else None,
            )
            try:
                data = r.json()
            except Exception:
                data = {"error": "réponse non-JSON", "status": r.status_code, "body": r.text[:200]}
            return JSONResponse(status_code=r.status_code, content=data)
        except httpx.ConnectError:
            return JSONResponse(status_code=502, content={"error": f"Impossible de joindre {FS_DOMAIN}"})
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})
