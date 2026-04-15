import os
import base64
import json
import httpx
import asyncio
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, Mount
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent

# ── Config ───────────────────────────────────────────────────────────────────
FS_DOMAIN  = os.environ.get("FS_DOMAIN", "")
FS_API_KEY = os.environ.get("FS_API_KEY", "")
AUTH       = base64.b64encode(f"{FS_API_KEY}:X".encode()).decode()

# ── Client Freshservice ───────────────────────────────────────────────────────
async def fs(method: str, path: str, body: dict = None):
    url = f"https://{FS_DOMAIN}/api/v2/{path.lstrip('/')}"
    headers = {
        "Authorization": f"Basic {AUTH}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.request(method, url, headers=headers, json=body)
        r.raise_for_status()
        return r.json()

# ── Serveur MCP ───────────────────────────────────────────────────────────────
server = Server("freshservice-mcp")

@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="list_tickets",
            description="Lister les tickets Freshservice. Utile pour analyser, compter, filtrer les tickets.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "string",
                        "description": "open | resolved | all (défaut: open)"
                    },
                    "page": {
                        "type": "integer",
                        "description": "Numéro de page (défaut: 1)"
                    },
                    "per_page": {
                        "type": "integer",
                        "description": "Nombre de tickets par page, max 100 (défaut: 30)"
                    }
                }
            }
        ),
        Tool(
            name="get_ticket",
            description="Obtenir tous les détails d'un ticket spécifique par son ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "ID du ticket"}
                },
                "required": ["id"]
            }
        ),
        Tool(
            name="search_tickets",
            description="Rechercher des tickets par mot-clé dans le sujet.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Mot-clé à chercher"}
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="create_ticket",
            description="Créer un nouveau ticket dans Freshservice.",
            inputSchema={
                "type": "object",
                "properties": {
                    "subject":     {"type": "string"},
                    "description": {"type": "string"},
                    "email":       {"type": "string"},
                    "priority":    {"type": "integer", "description": "1=Faible 2=Moyen 3=Élevé 4=Urgent"}
                },
                "required": ["subject", "email"]
            }
        ),
        Tool(
            name="update_ticket",
            description="Mettre à jour le statut, la priorité d'un ticket, ou ajouter une note.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id":       {"type": "integer"},
                    "status":   {"type": "integer", "description": "2=Ouvert 3=En attente 4=Résolu 5=Fermé"},
                    "priority": {"type": "integer", "description": "1=Faible 2=Moyen 3=Élevé 4=Urgent"},
                    "note":     {"type": "string", "description": "Note publique à ajouter"}
                },
                "required": ["id"]
            }
        ),
        Tool(
            name="list_agents",
            description="Lister les agents disponibles dans Freshservice.",
            inputSchema={"type": "object", "properties": {}}
        ),
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    STATUS = {2:"Ouvert", 3:"En attente", 4:"Résolu", 5:"Fermé"}
    PRIO   = {1:"Faible", 2:"Moyen", 3:"Élevé", 4:"Urgent"}

    try:
        if name == "list_tickets":
            f  = arguments.get("filter", "open")
            p  = arguments.get("page", 1)
            pp = arguments.get("per_page", 30)
            data = await fs("GET", f"tickets?filter={f}&page={p}&per_page={pp}&order_by=created_at&order_type=desc")
            tickets = data.get("tickets", [])
            if not tickets:
                return [TextContent(type="text", text="Aucun ticket trouvé.")]
            lines = [
                f"#{t['id']} | {STATUS.get(t['status'],'?')} | {PRIO.get(t['priority'],'?')} | {t['subject']} | {t.get('created_at','')[:10]}"
                for t in tickets
            ]
            header = f"Total : {len(tickets)} tickets\n{'─'*60}\n"
            return [TextContent(type="text", text=header + "\n".join(lines))]

        elif name == "get_ticket":
            data = await fs("GET", f"tickets/{arguments['id']}")
            t = data["ticket"]
            text = (
                f"Ticket #{t['id']}\n"
                f"Sujet      : {t['subject']}\n"
                f"Statut     : {STATUS.get(t['status'], t['status'])}\n"
                f"Priorité   : {PRIO.get(t['priority'], t['priority'])}\n"
                f"Créé le    : {t.get('created_at','')[:10]}\n"
                f"Mis à jour : {t.get('updated_at','')[:10]}\n"
                f"Description: {t.get('description_text','')[:600]}"
            )
            return [TextContent(type="text", text=text)]

        elif name == "search_tickets":
            q = arguments["query"].lower()
            data = await fs("GET", "tickets?filter=all&per_page=100")
            hits = [t for t in data.get("tickets", []) if q in t["subject"].lower()]
            if not hits:
                return [TextContent(type="text", text=f"Aucun ticket trouvé pour « {q} ».")]
            lines = [f"#{t['id']} | {STATUS.get(t['status'],'?')} | {t['subject']}" for t in hits]
            return [TextContent(type="text", text=f"{len(hits)} résultat(s) :\n" + "\n".join(lines))]

        elif name == "create_ticket":
            data = await fs("POST", "tickets", {
                "subject":     arguments["subject"],
                "description": arguments.get("description", ""),
                "email":       arguments["email"],
                "priority":    arguments.get("priority", 2),
                "status":      2,
            })
            t = data["ticket"]
            return [TextContent(type="text", text=f"Ticket créé : #{t['id']} — {t['subject']}")]

        elif name == "update_ticket":
            tid = arguments["id"]
            payload = {k: arguments[k] for k in ("status", "priority") if k in arguments}
            if payload:
                await fs("PUT", f"tickets/{tid}", payload)
            if "note" in arguments:
                await fs("POST", f"tickets/{tid}/notes", {"body": arguments["note"], "private": False})
            return [TextContent(type="text", text=f"Ticket #{tid} mis à jour.")]

        elif name == "list_agents":
            data = await fs("GET", "agents?per_page=50")
            agents = data.get("agents", [])
            if not agents:
                return [TextContent(type="text", text="Aucun agent trouvé.")]
            lines = [f"#{a['id']} | {a.get('first_name','')} {a.get('last_name','')} | {a.get('email','')}" for a in agents]
            return [TextContent(type="text", text="\n".join(lines))]

        else:
            return [TextContent(type="text", text=f"Outil inconnu : {name}")]

    except httpx.HTTPStatusError as e:
        return [TextContent(type="text", text=f"Erreur Freshservice {e.response.status_code} : {e.response.text[:200]}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Erreur : {str(e)}")]

# ── Transport SSE ─────────────────────────────────────────────────────────────
sse = SseServerTransport("/messages/")

async def handle_sse(request: Request):
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())

async def health(request: Request):
    return JSONResponse({"status": "ok", "domain": FS_DOMAIN, "auth_set": bool(FS_API_KEY)})

# ── App Starlette ─────────────────────────────────────────────────────────────
app = Starlette(
    routes=[
        Route("/health",   endpoint=health),
        Route("/sse",      endpoint=handle_sse),
        Mount("/messages", app=sse.handle_post_message),
    ],
    middleware=[
        Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    ]
)
