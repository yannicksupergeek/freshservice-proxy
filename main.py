import os
import base64
import json
import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, Mount
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent

# ── Config ────────────────────────────────────────────────────────────────────
FS_DOMAIN  = os.environ.get("FS_DOMAIN", "")
FS_API_KEY = os.environ.get("FS_API_KEY", "")
AUTH       = base64.b64encode(f"{FS_API_KEY}:X".encode()).decode()

STATUS = {2:"Ouvert", 3:"En attente", 4:"Résolu", 5:"Fermé"}
PRIO   = {1:"Faible", 2:"Moyen", 3:"Élevé", 4:"Urgent"}
SOURCE = {1:"Email", 2:"Portail", 3:"Téléphone", 7:"Chat", 8:"Mattermost", 9:"Dexem"}

# ── Client Freshservice ───────────────────────────────────────────────────────
async def fs(method: str, path: str, body: dict = None):
    url = f"https://{FS_DOMAIN}/api/v2/{path.lstrip('/')}"
    headers = {
        "Authorization": f"Basic {AUTH}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.request(method, url, headers=headers, json=body)
        r.raise_for_status()
        return r.json()

async def fs_raw(method: str, path: str, body: dict = None):
    """Retourne le JSON brut complet sans traitement."""
    url = f"https://{FS_DOMAIN}/api/v2/{path.lstrip('/')}"
    headers = {
        "Authorization": f"Basic {AUTH}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.request(method, url, headers=headers, json=body)
        r.raise_for_status()
        return r.text

def fmt(val):
    if val is None: return "—"
    if isinstance(val, bool): return "Oui" if val else "Non"
    if isinstance(val, list): return ", ".join(str(v) for v in val) if val else "—"
    return str(val)

def safe_date(val, length=19):
    if not val: return "—"
    return str(val)[:length]

# ── Serveur MCP ───────────────────────────────────────────────────────────────
server = Server("freshservice-mcp")

@server.list_tools()
async def list_tools():
    return [

        # ── TICKETS ──────────────────────────────────────────────────────────
        Tool(
            name="list_tickets",
            description="Lister les tickets Freshservice avec tous leurs champs. Filtrable par agent, statut, priorité, workspace.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filter":        {"type": "string",  "description": "new_and_my_open | watching | spam | deleted (défaut: new_and_my_open)"},
                    "agent_id":      {"type": "integer", "description": "ID de l'agent assigné"},
                    "requester_id":  {"type": "integer", "description": "ID du demandeur"},
                    "workspace_id":  {"type": "integer", "description": "ID du workspace"},
                    "page":          {"type": "integer", "description": "Numéro de page (défaut: 1)"},
                    "per_page":      {"type": "integer", "description": "Tickets par page, max 100 (défaut: 30)"},
                    "updated_since": {"type": "string",  "description": "Depuis cette date ISO ex: 2026-04-01T00:00:00Z"}
                }
            }
        ),
        Tool(
            name="get_ticket",
            description="Obtenir TOUS les champs d'un ticket : description, champs personnalisés, tags, SLA, stats, workspace.",
            inputSchema={
                "type": "object",
                "properties": {"id": {"type": "integer", "description": "ID du ticket"}},
                "required": ["id"]
            }
        ),
        Tool(
            name="get_ticket_raw",
            description="Retourner le JSON brut complet d'un ticket tel que renvoyé par l'API Freshservice (tous les champs sans exception).",
            inputSchema={
                "type": "object",
                "properties": {"id": {"type": "integer", "description": "ID du ticket"}},
                "required": ["id"]
            }
        ),
        Tool(
            name="get_ticket_conversations",
            description="Obtenir toutes les conversations d'un ticket : notes publiques, notes privées, réponses emails.",
            inputSchema={
                "type": "object",
                "properties": {"id": {"type": "integer", "description": "ID du ticket"}},
                "required": ["id"]
            }
        ),
        Tool(
            name="get_ticket_activities",
            description="Obtenir l'historique complet des activités d'un ticket (changements statut, assignations, etc.).",
            inputSchema={
                "type": "object",
                "properties": {"id": {"type": "integer", "description": "ID du ticket"}},
                "required": ["id"]
            }
        ),
        Tool(
            name="search_tickets",
            description="Rechercher des tickets par mot-clé dans le sujet ou la description.",
            inputSchema={
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Mot-clé ou requête"}},
                "required": ["query"]
            }
        ),
        Tool(
            name="create_ticket",
            description="Créer un nouveau ticket dans Freshservice.",
            inputSchema={
                "type": "object",
                "properties": {
                    "subject":       {"type": "string"},
                    "description":   {"type": "string"},
                    "email":         {"type": "string"},
                    "priority":      {"type": "integer", "description": "1=Faible 2=Moyen 3=Élevé 4=Urgent"},
                    "status":        {"type": "integer", "description": "2=Ouvert 3=En attente 4=Résolu 5=Fermé"},
                    "responder_id":  {"type": "integer", "description": "ID de l'agent à assigner"},
                    "workspace_id":  {"type": "integer", "description": "ID du workspace"}
                },
                "required": ["subject", "email"]
            }
        ),
        Tool(
            name="update_ticket",
            description="Mettre à jour statut, priorité, agent d'un ticket, ou ajouter une note publique/privée.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id":            {"type": "integer"},
                    "status":        {"type": "integer", "description": "2=Ouvert 3=En attente 4=Résolu 5=Fermé"},
                    "priority":      {"type": "integer", "description": "1=Faible 2=Moyen 3=Élevé 4=Urgent"},
                    "responder_id":  {"type": "integer", "description": "ID du nouvel agent assigné"},
                    "note":          {"type": "string",  "description": "Note publique"},
                    "private_note":  {"type": "string",  "description": "Note privée (agents seulement)"}
                },
                "required": ["id"]
            }
        ),

        # ── WORKSPACES ───────────────────────────────────────────────────────
        Tool(
            name="list_workspaces",
            description="Lister tous les workspaces Freshservice.",
            inputSchema={"type": "object", "properties": {}}
        ),

        # ── CONTACTS ─────────────────────────────────────────────────────────
        Tool(
            name="list_contacts",
            description="Lister tous les contacts (demandeurs) avec leurs informations complètes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "page":     {"type": "integer"},
                    "per_page": {"type": "integer", "description": "max 100"},
                    "query":    {"type": "string",  "description": "Filtrer par nom ou email"}
                }
            }
        ),
        Tool(
            name="get_contact",
            description="Obtenir tous les détails d'un contact : nom, email, téléphone, société, champs personnalisés.",
            inputSchema={
                "type": "object",
                "properties": {"id": {"type": "integer", "description": "ID du contact"}},
                "required": ["id"]
            }
        ),

        # ── AGENTS ───────────────────────────────────────────────────────────
        Tool(
            name="list_agents",
            description="Lister tous les agents avec nom, email, disponibilité, groupes.",
            inputSchema={"type": "object", "properties": {}}
        ),
        Tool(
            name="get_agent",
            description="Obtenir les détails complets d'un agent par son ID.",
            inputSchema={
                "type": "object",
                "properties": {"id": {"type": "integer", "description": "ID de l'agent"}},
                "required": ["id"]
            }
        ),

        # ── PROJETS ──────────────────────────────────────────────────────────
        Tool(
            name="list_projects",
            description="Lister tous les projets Freshservice avec statut, dates, manager.",
            inputSchema={
                "type": "object",
                "properties": {
                    "page":     {"type": "integer"},
                    "per_page": {"type": "integer"}
                }
            }
        ),
        Tool(
            name="get_project",
            description="Obtenir tous les détails d'un projet par son ID.",
            inputSchema={
                "type": "object",
                "properties": {"id": {"type": "integer", "description": "ID du projet"}},
                "required": ["id"]
            }
        ),

        # ── TÂCHES ───────────────────────────────────────────────────────────
        Tool(
            name="list_ticket_tasks",
            description="Lister toutes les tâches associées à un ticket.",
            inputSchema={
                "type": "object",
                "properties": {"ticket_id": {"type": "integer", "description": "ID du ticket"}},
                "required": ["ticket_id"]
            }
        ),
        Tool(
            name="list_project_tasks",
            description="Lister toutes les tâches d'un projet.",
            inputSchema={
                "type": "object",
                "properties": {"project_id": {"type": "integer", "description": "ID du projet"}},
                "required": ["project_id"]
            }
        ),

        # ── ASSETS ───────────────────────────────────────────────────────────
        Tool(
            name="list_assets",
            description="Lister les assets (équipements, logiciels) du parc informatique.",
            inputSchema={
                "type": "object",
                "properties": {
                    "page":     {"type": "integer"},
                    "per_page": {"type": "integer", "description": "max 100"}
                }
            }
        ),

        # ── STATS ────────────────────────────────────────────────────────────
        Tool(
            name="get_stats",
            description="Statistiques globales : tickets par statut, par priorité, par agent.",
            inputSchema={"type": "object", "properties": {}}
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    try:

        # ── list_tickets ──────────────────────────────────────────────────────
        if name == "list_tickets":
            f   = arguments.get("filter", "new_and_my_open")
            p   = arguments.get("page", 1)
            pp  = arguments.get("per_page", 30)
            params = f"filter={f}&page={p}&per_page={pp}&order_by=created_at&order_type=desc&include=requester,stats,tags"
            if arguments.get("workspace_id"):
                params += f"&workspace_id={arguments['workspace_id']}"
            if arguments.get("updated_since"):
                params += f"&updated_since={arguments['updated_since']}"
            data    = await fs("GET", f"tickets?{params}")
            tickets = data.get("tickets", [])
            if not tickets:
                return [TextContent(type="text", text="Aucun ticket trouvé.")]
            lines = []
            for t in tickets:
                req   = t.get("requester") or {}
                stats = t.get("stats") or {}
                tags  = fmt(t.get("tags", []))
                agent_id = t.get("responder_id") or t.get("agent_id") or "—"
                lines.append(
                    f"#{t['id']} | {STATUS.get(t['status'],'?')} | {PRIO.get(t['priority'],'?')} | "
                    f"Agent:{agent_id} | "
                    f"Demandeur:{req.get('name', fmt(t.get('requester_id')))} | "
                    f"Workspace:{fmt(t.get('workspace_id'))} | "
                    f"Tags:{tags} | {t['subject']} | {safe_date(t.get('created_at'),10)}"
                )
            return [TextContent(type="text", text=f"Total : {len(tickets)} tickets\n{'─'*60}\n" + "\n".join(lines))]

        # ── get_ticket ────────────────────────────────────────────────────────
        elif name == "get_ticket":
            data   = await fs("GET", f"tickets/{arguments['id']}?include=requester,stats,tags")
            t      = data.get("ticket") or data
            stats  = t.get("stats") or {}
            custom = t.get("custom_fields") or {}
            tags   = fmt(t.get("tags", []))
            custom_lines = "\n".join([f"  {k}: {fmt(v)}" for k, v in custom.items()]) or "  Aucun"
            text = (
                f"{'═'*60}\n"
                f"TICKET #{t.get('id')}\n"
                f"{'═'*60}\n"
                f"Sujet              : {fmt(t.get('subject'))}\n"
                f"Statut             : {STATUS.get(t.get('status'), fmt(t.get('status')))}\n"
                f"Priorité           : {PRIO.get(t.get('priority'), fmt(t.get('priority')))}\n"
                f"Type               : {fmt(t.get('type'))}\n"
                f"Source             : {SOURCE.get(t.get('source'), fmt(t.get('source')))}\n"
                f"Workspace          : {fmt(t.get('workspace_id'))}\n"
                f"Agent assigné      : {fmt(t.get('responder_id'))}\n"
                f"Groupe             : {fmt(t.get('group_id'))}\n"
                f"Demandeur ID       : {fmt(t.get('requester_id'))}\n"
                f"Email              : {fmt(t.get('email'))}\n"
                f"Catégorie          : {fmt(t.get('category'))}\n"
                f"Sous-catégorie     : {fmt(t.get('sub_category'))}\n"
                f"Élément            : {fmt(t.get('item_category'))}\n"
                f"Tags               : {tags}\n"
                f"Spam               : {fmt(t.get('spam'))}\n"
                f"Escaladé           : {fmt(t.get('is_escalated'))}\n"
                f"Supprimé           : {fmt(t.get('deleted'))}\n"
                f"Créé le            : {safe_date(t.get('created_at'))}\n"
                f"Mis à jour         : {safe_date(t.get('updated_at'))}\n"
                f"Échéance           : {safe_date(t.get('due_by'))}\n"
                f"Échéance 1ère rép. : {safe_date(t.get('fr_due_by'))}\n"
                f"1ère rép. à        : {safe_date(stats.get('first_responded_at'))}\n"
                f"Résolu le          : {safe_date(stats.get('resolved_at'))}\n"
                f"Fermé le           : {safe_date(stats.get('closed_at'))}\n"
                f"{'─'*60}\n"
                f"DESCRIPTION :\n{t.get('description_text') or '(vide)'}\n"
                f"{'─'*60}\n"
                f"CHAMPS PERSONNALISÉS :\n{custom_lines}\n"
            )
            return [TextContent(type="text", text=text)]

        # ── get_ticket_raw ────────────────────────────────────────────────────
        elif name == "get_ticket_raw":
            raw = await fs_raw("GET", f"tickets/{arguments['id']}?include=requester,stats,tags,conversations")
            parsed = json.loads(raw)
            pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
            return [TextContent(type="text", text=f"JSON BRUT — Ticket #{arguments['id']} :\n\n{pretty[:4000]}")]

        # ── get_ticket_conversations ──────────────────────────────────────────
        elif name == "get_ticket_conversations":
            data  = await fs("GET", f"tickets/{arguments['id']}/conversations")
            convs = data.get("conversations") or []
            if not convs:
                return [TextContent(type="text", text="Aucune conversation sur ce ticket.")]
            lines = []
            for c in convs:
                kind = "NOTE PRIVÉE" if c.get("private") else ("EMAIL" if c.get("source") == 0 else "NOTE")
                body = c.get("body_text") or c.get("body") or ""
                lines.append(
                    f"[{kind}] {safe_date(c.get('created_at'))} — Agent:{fmt(c.get('user_id'))}\n"
                    f"{body[:800]}\n{'─'*40}"
                )
            return [TextContent(type="text", text=f"{len(convs)} conversation(s) :\n\n" + "\n".join(lines))]

        # ── get_ticket_activities ─────────────────────────────────────────────
        elif name == "get_ticket_activities":
            data = await fs("GET", f"tickets/{arguments['id']}/activities")
            acts = data.get("activities") or []
            if not acts:
                return [TextContent(type="text", text="Aucune activité trouvée.")]
            lines = [
                f"{safe_date(a.get('created_at'))} | {(a.get('actor') or {}).get('name','?')} | {a.get('content','')}"
                for a in acts
            ]
            return [TextContent(type="text", text=f"{len(acts)} activité(s) :\n" + "\n".join(lines))]

        # ── search_tickets ────────────────────────────────────────────────────
        elif name == "search_tickets":
            q = arguments["query"]
            try:
                data    = await fs("GET", f'tickets/filter?query="{q}"&per_page=30&include=requester')
                tickets = data.get("tickets") or []
            except Exception:
                data    = await fs("GET", "tickets?filter=new_and_my_open&per_page=100&include=requester")
                tickets = [t for t in (data.get("tickets") or []) if q.lower() in t.get("subject","").lower()]
            if not tickets:
                return [TextContent(type="text", text=f"Aucun ticket trouvé pour « {q} ».")]
            lines = [
                f"#{t['id']} | {STATUS.get(t.get('status'),'?')} | {PRIO.get(t.get('priority'),'?')} | "
                f"Agent:{fmt(t.get('responder_id'))} | Workspace:{fmt(t.get('workspace_id'))} | "
                f"{t.get('subject','')} | {safe_date(t.get('created_at'),10)}"
                for t in tickets
            ]
            return [TextContent(type="text", text=f"{len(tickets)} résultat(s) :\n" + "\n".join(lines))]

        # ── create_ticket ─────────────────────────────────────────────────────
        elif name == "create_ticket":
            payload = {
                "subject":     arguments["subject"],
                "description": arguments.get("description", ""),
                "email":       arguments["email"],
                "priority":    arguments.get("priority", 2),
                "status":      arguments.get("status", 2),
            }
            for k in ("responder_id", "workspace_id"):
                if arguments.get(k):
                    payload[k] = arguments[k]
            data = await fs("POST", "tickets", payload)
            t    = data.get("ticket") or data
            return [TextContent(type="text", text=
                f"Ticket créé : #{t.get('id')} — {t.get('subject')}\n"
                f"Workspace : {fmt(t.get('workspace_id'))}\n"
                f"Agent     : {fmt(t.get('responder_id'))}"
            )]

        # ── update_ticket ─────────────────────────────────────────────────────
        elif name == "update_ticket":
            tid     = arguments["id"]
            payload = {k: arguments[k] for k in ("status", "priority", "responder_id") if k in arguments}
            if payload:
                await fs("PUT", f"tickets/{tid}", payload)
            if arguments.get("note"):
                await fs("POST", f"tickets/{tid}/notes", {"body": arguments["note"], "private": False})
            if arguments.get("private_note"):
                await fs("POST", f"tickets/{tid}/notes", {"body": arguments["private_note"], "private": True})
            return [TextContent(type="text", text=f"Ticket #{tid} mis à jour.")]

        # ── list_workspaces ───────────────────────────────────────────────────
        elif name == "list_workspaces":
            data = await fs("GET", "workspaces")
            workspaces = data.get("workspaces") or []
            if not workspaces:
                raw = await fs_raw("GET", "workspaces")
                return [TextContent(type="text", text=f"Réponse brute workspaces :\n{raw[:1000]}")]
            lines = [
                f"#{w.get('id')} | {w.get('name','?')} | Actif:{fmt(w.get('active'))} | "
                f"Description:{fmt(w.get('description'))}"
                for w in workspaces
            ]
            return [TextContent(type="text", text=f"{len(workspaces)} workspace(s) :\n" + "\n".join(lines))]

        # ── list_contacts ─────────────────────────────────────────────────────
        elif name == "list_contacts":
            p    = arguments.get("page", 1)
            pp   = arguments.get("per_page", 30)
            q    = arguments.get("query", "")
            path = f"contacts?page={p}&per_page={pp}"
            if q: path += f"&query={q}"
            data     = await fs("GET", path)
            contacts = data.get("contacts") or []
            if not contacts:
                return [TextContent(type="text", text="Aucun contact trouvé.")]
            lines = [
                f"#{c['id']} | {c.get('name','?')} | {c.get('email','—')} | "
                f"Tél:{c.get('phone','—')} | Mob:{c.get('mobile','—')} | "
                f"Société:{c.get('company_name','—')} | Actif:{fmt(c.get('active'))}"
                for c in contacts
            ]
            return [TextContent(type="text", text=f"{len(contacts)} contact(s) :\n" + "\n".join(lines))]

        # ── get_contact ───────────────────────────────────────────────────────
        elif name == "get_contact":
            data   = await fs("GET", f"contacts/{arguments['id']}")
            c      = data.get("contact") or data
            custom = c.get("custom_fields") or {}
            custom_lines = "\n".join([f"  {k}: {fmt(v)}" for k, v in custom.items()]) or "  Aucun"
            text = (
                f"{'═'*60}\n"
                f"CONTACT #{c.get('id')}\n"
                f"{'═'*60}\n"
                f"Nom            : {fmt(c.get('name'))}\n"
                f"Email          : {fmt(c.get('email'))}\n"
                f"Téléphone      : {fmt(c.get('phone'))}\n"
                f"Mobile         : {fmt(c.get('mobile'))}\n"
                f"Société        : {fmt(c.get('company_name'))}\n"
                f"Département    : {fmt(c.get('department'))}\n"
                f"Actif          : {fmt(c.get('active'))}\n"
                f"VIP            : {fmt(c.get('vip_user'))}\n"
                f"Langue         : {fmt(c.get('language'))}\n"
                f"Fuseau horaire : {fmt(c.get('time_zone'))}\n"
                f"Créé le        : {safe_date(c.get('created_at'))}\n"
                f"Mis à jour     : {safe_date(c.get('updated_at'))}\n"
                f"{'─'*60}\n"
                f"CHAMPS PERSONNALISÉS :\n{custom_lines}\n"
            )
            return [TextContent(type="text", text=text)]

        # ── list_agents ───────────────────────────────────────────────────────
        elif name == "list_agents":
            data   = await fs("GET", "agents?per_page=100")
            agents = data.get("agents") or []
            if not agents:
                return [TextContent(type="text", text="Aucun agent trouvé.")]
            lines = [
                f"#{a['id']} | {a.get('first_name','')} {a.get('last_name','')} | "
                f"{a.get('email','—')} | Actif:{fmt(a.get('active'))} | Disponible:{fmt(a.get('available'))}"
                for a in agents
            ]
            return [TextContent(type="text", text="\n".join(lines))]

        # ── get_agent ─────────────────────────────────────────────────────────
        elif name == "get_agent":
            data = await fs("GET", f"agents/{arguments['id']}")
            a    = data.get("agent") or data
            text = (
                f"{'═'*60}\n"
                f"AGENT #{a.get('id')}\n"
                f"{'═'*60}\n"
                f"Nom            : {a.get('first_name','')} {a.get('last_name','')}\n"
                f"Email          : {fmt(a.get('email'))}\n"
                f"Téléphone      : {fmt(a.get('phone'))}\n"
                f"Mobile         : {fmt(a.get('mobile'))}\n"
                f"Actif          : {fmt(a.get('active'))}\n"
                f"Disponible     : {fmt(a.get('available'))}\n"
                f"Rôles          : {fmt(a.get('role_ids'))}\n"
                f"Groupes        : {fmt(a.get('group_ids'))}\n"
                f"Créé le        : {safe_date(a.get('created_at'))}\n"
            )
            return [TextContent(type="text", text=text)]

        # ── list_projects ─────────────────────────────────────────────────────
        elif name == "list_projects":
            p  = arguments.get("page", 1)
            pp = arguments.get("per_page", 30)
            data     = await fs("GET", f"projects?page={p}&per_page={pp}")
            projects = data.get("projects") or []
            if not projects:
                return [TextContent(type="text", text="Aucun projet trouvé.")]
            lines = [
                f"#{pj['id']} | {pj.get('name','?')} | Statut:{pj.get('status','—')} | "
                f"Manager:{pj.get('manager_id','—')} | "
                f"Début:{safe_date(pj.get('start_date'),10)} | Fin:{safe_date(pj.get('end_date'),10)}"
                for pj in projects
            ]
            return [TextContent(type="text", text=f"{len(projects)} projet(s) :\n" + "\n".join(lines))]

        # ── get_project ───────────────────────────────────────────────────────
        elif name == "get_project":
            data = await fs("GET", f"projects/{arguments['id']}")
            pj   = data.get("project") or data
            text = (
                f"{'═'*60}\n"
                f"PROJET #{pj.get('id')}\n"
                f"{'═'*60}\n"
                f"Nom            : {fmt(pj.get('name'))}\n"
                f"Description    : {fmt(pj.get('description'))}\n"
                f"Statut         : {fmt(pj.get('status'))}\n"
                f"Priorité       : {fmt(pj.get('priority'))}\n"
                f"Manager        : {fmt(pj.get('manager_id'))}\n"
                f"Date début     : {safe_date(pj.get('start_date'),10)}\n"
                f"Date fin       : {safe_date(pj.get('end_date'),10)}\n"
                f"Créé le        : {safe_date(pj.get('created_at'))}\n"
                f"Mis à jour     : {safe_date(pj.get('updated_at'))}\n"
            )
            return [TextContent(type="text", text=text)]

        # ── list_ticket_tasks ─────────────────────────────────────────────────
        elif name == "list_ticket_tasks":
            data  = await fs("GET", f"tickets/{arguments['ticket_id']}/tasks")
            tasks = data.get("tasks") or []
            if not tasks:
                return [TextContent(type="text", text="Aucune tâche sur ce ticket.")]
            lines = [
                f"#{t['id']} | {t.get('title','?')} | Statut:{t.get('status','—')} | "
                f"Agent:{t.get('agent_id','—')} | Échéance:{safe_date(t.get('due_date'),10)}\n"
                f"  Description: {t.get('description','—')[:200]}"
                for t in tasks
            ]
            return [TextContent(type="text", text=f"{len(tasks)} tâche(s) :\n" + "\n".join(lines))]

        # ── list_project_tasks ────────────────────────────────────────────────
        elif name == "list_project_tasks":
            data  = await fs("GET", f"projects/{arguments['project_id']}/tasks")
            tasks = data.get("tasks") or []
            if not tasks:
                return [TextContent(type="text", text="Aucune tâche sur ce projet.")]
            lines = [
                f"#{t['id']} | {t.get('title','?')} | Statut:{t.get('status','—')} | "
                f"Assigné:{t.get('assignee_id','—')} | Échéance:{safe_date(t.get('due_date'),10)}"
                for t in tasks
            ]
            return [TextContent(type="text", text=f"{len(tasks)} tâche(s) :\n" + "\n".join(lines))]

        # ── list_assets ───────────────────────────────────────────────────────
        elif name == "list_assets":
            p  = arguments.get("page", 1)
            pp = arguments.get("per_page", 30)
            data   = await fs("GET", f"assets?page={p}&per_page={pp}")
            assets = data.get("assets") or []
            if not assets:
                return [TextContent(type="text", text="Aucun asset trouvé.")]
            lines = [
                f"#{a['id']} | {a.get('name','?')} | Type:{a.get('asset_type_id','—')} | "
                f"Utilisateur:{a.get('user_id','—')} | État:{a.get('state','—')}"
                for a in assets
            ]
            return [TextContent(type="text", text=f"{len(assets)} asset(s) :\n" + "\n".join(lines))]

        # ── get_stats ─────────────────────────────────────────────────────────
        elif name == "get_stats":
            data    = await fs("GET", "tickets?filter=new_and_my_open&per_page=100&include=requester")
            tickets = data.get("tickets") or []
            by_status    = {}
            by_priority  = {}
            by_agent     = {}
            by_workspace = {}
            for t in tickets:
                s  = STATUS.get(t.get('status'), str(t.get('status')))
                p  = PRIO.get(t.get('priority'), str(t.get('priority')))
                a  = str(t.get('responder_id') or 'Non assigné')
                ws = str(t.get('workspace_id') or '—')
                by_status[s]    = by_status.get(s, 0) + 1
                by_priority[p]  = by_priority.get(p, 0) + 1
                by_agent[a]     = by_agent.get(a, 0) + 1
                by_workspace[ws]= by_workspace.get(ws, 0) + 1
            lines = [
                f"{'═'*50}",
                "STATISTIQUES FRESHSERVICE",
                f"{'═'*50}",
                f"Tickets analysés : {len(tickets)}",
                f"{'─'*50}",
                "PAR STATUT :",
                *[f"  {k}: {v}" for k, v in sorted(by_status.items(), key=lambda x: -x[1])],
                f"{'─'*50}",
                "PAR PRIORITÉ :",
                *[f"  {k}: {v}" for k, v in sorted(by_priority.items(), key=lambda x: -x[1])],
                f"{'─'*50}",
                "PAR WORKSPACE :",
                *[f"  Workspace {k}: {v}" for k, v in sorted(by_workspace.items(), key=lambda x: -x[1])],
                f"{'─'*50}",
                "PAR AGENT (top 10) :",
                *[f"  Agent {k}: {v} ticket(s)" for k, v in sorted(by_agent.items(), key=lambda x: -x[1])[:10]],
            ]
            return [TextContent(type="text", text="\n".join(lines))]

        else:
            return [TextContent(type="text", text=f"Outil inconnu : {name}")]

    except httpx.HTTPStatusError as e:
        return [TextContent(type="text", text=f"Erreur Freshservice {e.response.status_code} : {e.response.text[:300]}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Erreur : {str(e)}")]


# ── Transport SSE ─────────────────────────────────────────────────────────────
sse = SseServerTransport("/messages/")

async def handle_sse(request: Request):
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())

async def health(request: Request):
    return JSONResponse({
        "status":   "ok",
        "domain":   FS_DOMAIN,
        "auth_set": bool(FS_API_KEY),
        "tools":    19,
        "version":  "3.0"
    })

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
