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

STATUS = {
    2:  "Ouvert",
    3:  "En attente",
    4:  "Résolu",
    5:  "Fermé",
    6:  "RDV Planifié",
    7:  "Contact injoignable 1",
    8:  "Contact injoignable 2",
    9:  "Attente tiers",
    10: "Nouveau",
}
PRIO = {1:"Faible", 2:"Moyen", 3:"Élevé", 4:"Urgent"}
SOURCE = {1:"Email", 2:"Portail", 3:"Téléphone", 7:"Chat", 8:"Mattermost", 9:"Dexem"}
WORKSPACE = {2:"B2C/Pro", 3:"Protected", 6:"B2B"}
WORKSPACE_IDS = [2, 3, 6]

AGENTS = {
    37002903654:"Alice", 37002930870:"Anaïs", 37002903655:"Axel",
    37002903652:"Cedric", 37000266099:"James", 37002960378:"Jason",
    37002903656:"Jessica", 37002933996:"Khadijah", 37003614701:"Laeticia",
    37002903651:"Léa", 37000265923:"Michel", 37002903650:"Sam",
    37002903653:"Samuel", 37002903657:"Shelly", 37003554098:"Superviseur",
    37000266010:"Yannick",
}

async def fs(method, path, body=None):
    url = f"https://{FS_DOMAIN}/api/v2/{path.lstrip('/')}"
    headers = {"Authorization":f"Basic {AUTH}","Content-Type":"application/json","Accept":"application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.request(method, url, headers=headers, json=body)
        r.raise_for_status()
        return r.json()

async def fs_raw(method, path, body=None):
    url = f"https://{FS_DOMAIN}/api/v2/{path.lstrip('/')}"
    headers = {"Authorization":f"Basic {AUTH}","Content-Type":"application/json","Accept":"application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.request(method, url, headers=headers, json=body)
        r.raise_for_status()
        return r.text

def fmt(val):
    if val is None: return "—"
    if isinstance(val, bool): return "Oui" if val else "Non"
    if isinstance(val, list): return ", ".join(str(v) for v in val) if val else "—"
    return str(val)

def fmt_agent(aid):
    if not aid: return "—"
    try: return AGENTS.get(int(aid), f"Agent#{aid}")
    except: return str(aid)

def fmt_status(code):
    if not code: return "—"
    try: return STATUS.get(int(code), f"Statut#{code}")
    except: return str(code)

def fmt_prio(code):
    if not code: return "—"
    try: return PRIO.get(int(code), f"Priorité#{code}")
    except: return str(code)

def fmt_workspace(code):
    if not code: return "—"
    try: return WORKSPACE.get(int(code), f"Workspace#{code}")
    except: return str(code)

def fmt_source(code):
    if not code: return "—"
    try: return SOURCE.get(int(code), f"Source#{code}")
    except: return str(code)

def safe_date(val, length=19):
    if not val: return "—"
    return str(val)[:length]

async def fetch_all_tickets_for_agent(agent_id: int, status_filter: int = None) -> list:
    """
    Parcourt tous les workspaces (B2C/Pro, Protected, B2B) et tous les filtres
    pour trouver les tickets d'un agent, quel que soit le statut.
    """
    seen    = set()
    results = []

    # 1. Recherche avancée (tous workspaces d'un coup)
    try:
        query = f'responder_id:{agent_id}'
        if status_filter:
            query += f' AND status:{status_filter}'
        data = await fs("GET", f'tickets/filter?query="{query}"&per_page=100&include=requester')
        for t in (data.get("tickets") or []):
            if t["id"] not in seen:
                seen.add(t["id"])
                results.append(t)
        if results:
            return results
    except Exception:
        pass

    # 2. Fallback : parcourir chaque workspace × chaque filtre × plusieurs pages
    for ws_id in WORKSPACE_IDS:
        for filtre in ["new_and_my_open", "watching"]:
            for page in range(1, 6):
                try:
                    path = f"tickets?filter={filtre}&workspace_id={ws_id}&page={page}&per_page=100&include=requester"
                    d    = await fs("GET", path)
                    batch = d.get("tickets") or []
                    if not batch:
                        break
                    for t in batch:
                        if str(t.get("responder_id")) == str(agent_id):
                            if t["id"] not in seen:
                                seen.add(t["id"])
                                results.append(t)
                except Exception:
                    break

    # 3. Filtre par statut si demandé
    if status_filter:
        results = [t for t in results if t.get("status") == status_filter]

    return results

server = Server("freshservice-mcp")

@server.list_tools()
async def list_tools():
    return [
        Tool(name="list_tickets",
             description="Lister les tickets Freshservice avec tous leurs champs lisibles.",
             inputSchema={"type":"object","properties":{
                 "filter":       {"type":"string","description":"new_and_my_open | watching | spam | deleted"},
                 "requester_id": {"type":"integer"},
                 "workspace_id": {"type":"integer","description":"2=B2C/Pro 3=Protected 6=B2B"},
                 "page":         {"type":"integer"},
                 "per_page":     {"type":"integer","description":"max 100"},
                 "updated_since":{"type":"string"}
             }}),
        Tool(name="search_tickets_by_agent",
             description=(
                 "Rechercher tous les tickets d'un agent sur TOUS les workspaces "
                 "(B2C/Pro, Protected, B2B) et TOUS les statuts y compris RDV Planifié. "
                 "Agents: Alice=37002903654, Anaïs=37002930870, Axel=37002903655, "
                 "Cedric=37002903652, James=37000266099, Jason=37002960378, "
                 "Jessica=37002903656, Léa=37002903651, Sam=37002903650, "
                 "Samuel=37002903653, Shelly=37002903657, Yannick=37000266010. "
                 "Statuts: 2=Ouvert 3=En attente 4=Résolu 5=Fermé "
                 "6=RDV Planifié 7=Contact injoignable 1 8=Contact injoignable 2 "
                 "9=Attente tiers 10=Nouveau."
             ),
             inputSchema={"type":"object","required":["agent_id"],"properties":{
                 "agent_id": {"type":"integer"},
                 "status":   {"type":"integer","description":"Optionnel — code statut"},
             }}),
        Tool(name="get_ticket",
             description="Obtenir TOUS les champs d'un ticket avec labels lisibles.",
             inputSchema={"type":"object","required":["id"],"properties":{"id":{"type":"integer"}}}),
        Tool(name="get_ticket_raw",
             description="JSON brut complet d'un ticket.",
             inputSchema={"type":"object","required":["id"],"properties":{"id":{"type":"integer"}}}),
        Tool(name="get_ticket_conversations",
             description="Toutes les conversations d'un ticket (notes, emails).",
             inputSchema={"type":"object","required":["id"],"properties":{"id":{"type":"integer"}}}),
        Tool(name="get_ticket_activities",
             description="Historique complet des activités d'un ticket.",
             inputSchema={"type":"object","required":["id"],"properties":{"id":{"type":"integer"}}}),
        Tool(name="search_tickets",
             description="Rechercher des tickets par mot-clé.",
             inputSchema={"type":"object","required":["query"],"properties":{"query":{"type":"string"}}}),
        Tool(name="create_ticket",
             description="Créer un nouveau ticket.",
             inputSchema={"type":"object","required":["subject","email"],"properties":{
                 "subject":{"type":"string"},"description":{"type":"string"},
                 "email":{"type":"string"},
                 "priority":{"type":"integer","description":"1=Faible 2=Moyen 3=Élevé 4=Urgent"},
                 "status":{"type":"integer"},
                 "responder_id":{"type":"integer"},
                 "workspace_id":{"type":"integer","description":"2=B2C/Pro 3=Protected 6=B2B"}
             }}),
        Tool(name="update_ticket",
             description="Mettre à jour un ticket (statut, priorité, agent, notes).",
             inputSchema={"type":"object","required":["id"],"properties":{
                 "id":{"type":"integer"},
                 "status":{"type":"integer","description":"2=Ouvert 3=En attente 4=Résolu 5=Fermé 6=RDV Planifié 7=CI1 8=CI2 9=Attente tiers 10=Nouveau"},
                 "priority":{"type":"integer"},
                 "responder_id":{"type":"integer"},
                 "note":{"type":"string"},
                 "private_note":{"type":"string"}
             }}),
        Tool(name="list_workspaces",
             description="Lister les workspaces (2=B2C/Pro, 3=Protected, 6=B2B).",
             inputSchema={"type":"object","properties":{}}),
        Tool(name="list_contacts",
             description="Lister les contacts/demandeurs.",
             inputSchema={"type":"object","properties":{
                 "page":{"type":"integer"},"per_page":{"type":"integer"},"query":{"type":"string"}
             }}),
        Tool(name="get_contact",
             description="Détails complets d'un contact.",
             inputSchema={"type":"object","required":["id"],"properties":{"id":{"type":"integer"}}}),
        Tool(name="list_agents",
             description="Lister tous les agents Supergeek.",
             inputSchema={"type":"object","properties":{}}),
        Tool(name="get_agent",
             description="Détails d'un agent par son ID.",
             inputSchema={"type":"object","required":["id"],"properties":{"id":{"type":"integer"}}}),
        Tool(name="list_projects",
             description="Lister tous les projets.",
             inputSchema={"type":"object","properties":{"page":{"type":"integer"},"per_page":{"type":"integer"}}}),
        Tool(name="get_project",
             description="Détails d'un projet.",
             inputSchema={"type":"object","required":["id"],"properties":{"id":{"type":"integer"}}}),
        Tool(name="list_ticket_tasks",
             description="Tâches d'un ticket.",
             inputSchema={"type":"object","required":["ticket_id"],"properties":{"ticket_id":{"type":"integer"}}}),
        Tool(name="list_project_tasks",
             description="Tâches d'un projet.",
             inputSchema={"type":"object","required":["project_id"],"properties":{"project_id":{"type":"integer"}}}),
        Tool(name="list_assets",
             description="Assets du parc informatique.",
             inputSchema={"type":"object","properties":{"page":{"type":"integer"},"per_page":{"type":"integer"}}}),
        Tool(name="get_stats",
             description="Statistiques globales par statut, priorité, agent, workspace.",
             inputSchema={"type":"object","properties":{}}),
    ]

@server.call_tool()
async def call_tool(name, arguments):
    try:
        if name == "list_tickets":
            f   = arguments.get("filter","new_and_my_open")
            p   = arguments.get("page",1)
            pp  = arguments.get("per_page",30)
            params = f"filter={f}&page={p}&per_page={pp}&order_by=created_at&order_type=desc&include=requester,stats,tags"
            for k in ("workspace_id","requester_id"):
                if arguments.get(k): params += f"&{k}={arguments[k]}"
            if arguments.get("updated_since"): params += f"&updated_since={arguments['updated_since']}"
            data    = await fs("GET", f"tickets?{params}")
            tickets = data.get("tickets") or []
            if not tickets: return [TextContent(type="text",text="Aucun ticket trouvé.")]
            lines = []
            for t in tickets:
                req = t.get("requester") or {}
                cf  = t.get("custom_fields") or {}
                lines.append(
                    f"#{t['id']} | {fmt_status(t.get('status'))} | {fmt_prio(t.get('priority'))} | "
                    f"Agent:{fmt_agent(t.get('responder_id'))} | "
                    f"Créateur:{fmt_agent(cf.get('lf_createur_du_ticket'))} | "
                    f"Workspace:{fmt_workspace(t.get('workspace_id'))} | "
                    f"Demandeur:{req.get('name','—')} | "
                    f"{t.get('subject','')} | {safe_date(t.get('created_at'),10)}"
                )
            return [TextContent(type="text",text=f"Total : {len(tickets)} tickets\n{'─'*60}\n"+"\n".join(lines))]

        elif name == "search_tickets_by_agent":
            agent_id   = arguments["agent_id"]
            status     = arguments.get("status")
            agent_name = AGENTS.get(agent_id, f"Agent#{agent_id}")
            tickets    = await fetch_all_tickets_for_agent(agent_id, status)
            if not tickets:
                status_name = fmt_status(status) if status else "tous statuts"
                return [TextContent(type="text",text=f"Aucun ticket trouvé pour {agent_name} ({status_name}).")]
            status_name = fmt_status(status) if status else "tous statuts"
            lines = []
            for t in tickets:
                req = t.get("requester") or {}
                cf  = t.get("custom_fields") or {}
                rdv = cf.get("date_heure_rdv") or "—"
                lines.append(
                    f"#{t['id']} | {fmt_status(t.get('status'))} | {fmt_prio(t.get('priority'))} | "
                    f"Workspace:{fmt_workspace(t.get('workspace_id'))} | "
                    f"Demandeur:{req.get('name','—')} | "
                    f"RDV:{rdv} | "
                    f"{t.get('subject','')} | {safe_date(t.get('created_at'),10)}"
                )
            return [TextContent(type="text",text=
                f"{agent_name} — {status_name} — {len(tickets)} ticket(s) :\n{'─'*60}\n"+"\n".join(lines)
            )]

        elif name == "get_ticket":
            data  = await fs("GET", f"tickets/{arguments['id']}?include=requester,stats,tags")
            t     = data.get("ticket") or data
            stats = t.get("stats") or {}
            cf    = t.get("custom_fields") or {}
            tags  = fmt(t.get("tags",[]))
            custom_lines = "\n".join([f"  {k}: {fmt(v)}" for k,v in cf.items()]) or "  Aucun"
            text = (
                f"{'═'*60}\nTICKET #{t.get('id')}\n{'═'*60}\n"
                f"Sujet              : {fmt(t.get('subject'))}\n"
                f"Statut             : {fmt_status(t.get('status'))}\n"
                f"Priorité           : {fmt_prio(t.get('priority'))}\n"
                f"Type               : {fmt(t.get('type'))}\n"
                f"Source             : {fmt_source(t.get('source'))}\n"
                f"Workspace          : {fmt_workspace(t.get('workspace_id'))}\n"
                f"Agent assigné      : {fmt_agent(t.get('responder_id'))}\n"
                f"Créateur ticket    : {fmt_agent(cf.get('lf_createur_du_ticket'))}\n"
                f"Technicien initial : {fmt_agent(cf.get('lf_agent_initial'))}\n"
                f"Demandeur ID       : {fmt(t.get('requester_id'))}\n"
                f"Email              : {fmt(t.get('email'))}\n"
                f"Catégorie          : {fmt(t.get('category'))}\n"
                f"Sous-catégorie     : {fmt(t.get('sub_category'))}\n"
                f"Tags               : {tags}\n"
                f"Spam               : {fmt(t.get('spam'))}\n"
                f"Escaladé           : {fmt(t.get('is_escalated'))}\n"
                f"Créé le            : {safe_date(t.get('created_at'))}\n"
                f"Mis à jour         : {safe_date(t.get('updated_at'))}\n"
                f"Échéance           : {safe_date(t.get('due_by'))}\n"
                f"Résolu le          : {safe_date(stats.get('resolved_at'))}\n"
                f"Fermé le           : {safe_date(stats.get('closed_at'))}\n"
                f"{'─'*60}\nDESCRIPTION :\n{t.get('description_text') or '(vide)'}\n"
                f"{'─'*60}\nCHAMPS PERSONNALISÉS :\n{custom_lines}\n"
            )
            return [TextContent(type="text",text=text)]

        elif name == "get_ticket_raw":
            raw    = await fs_raw("GET", f"tickets/{arguments['id']}?include=requester,stats,tags,conversations")
            parsed = json.loads(raw)
            pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
            return [TextContent(type="text",text=f"JSON BRUT — Ticket #{arguments['id']} :\n\n{pretty[:4000]}")]

        elif name == "get_ticket_conversations":
            data  = await fs("GET", f"tickets/{arguments['id']}/conversations")
            convs = data.get("conversations") or []
            if not convs: return [TextContent(type="text",text="Aucune conversation.")]
            lines = []
            for c in convs:
                kind = "NOTE PRIVÉE" if c.get("private") else ("EMAIL" if c.get("source")==0 else "NOTE")
                body = c.get("body_text") or c.get("body") or ""
                lines.append(f"[{kind}] {safe_date(c.get('created_at'))} — {fmt_agent(c.get('user_id'))}\n{body[:800]}\n{'─'*40}")
            return [TextContent(type="text",text=f"{len(convs)} conversation(s) :\n\n"+"\n".join(lines))]

        elif name == "get_ticket_activities":
            data = await fs("GET", f"tickets/{arguments['id']}/activities")
            acts = data.get("activities") or []
            if not acts: return [TextContent(type="text",text="Aucune activité.")]
            lines = [f"{safe_date(a.get('created_at'))} | {(a.get('actor') or {}).get('name','?')} | {a.get('content','')}" for a in acts]
            return [TextContent(type="text",text=f"{len(acts)} activité(s) :\n"+"\n".join(lines))]

        elif name == "search_tickets":
            q = arguments["query"]
            try:
                data    = await fs("GET", f'tickets/filter?query="{q}"&per_page=30&include=requester')
                tickets = data.get("tickets") or []
            except Exception:
                data    = await fs("GET", "tickets?filter=new_and_my_open&per_page=100&include=requester")
                tickets = [t for t in (data.get("tickets") or []) if q.lower() in t.get("subject","").lower()]
            if not tickets: return [TextContent(type="text",text=f"Aucun ticket trouvé pour « {q} ».")]
            lines = [
                f"#{t['id']} | {fmt_status(t.get('status'))} | {fmt_prio(t.get('priority'))} | "
                f"Agent:{fmt_agent(t.get('responder_id'))} | Workspace:{fmt_workspace(t.get('workspace_id'))} | "
                f"{t.get('subject','')} | {safe_date(t.get('created_at'),10)}"
                for t in tickets
            ]
            return [TextContent(type="text",text=f"{len(tickets)} résultat(s) :\n"+"\n".join(lines))]

        elif name == "create_ticket":
            payload = {
                "subject":     arguments["subject"],
                "description": arguments.get("description",""),
                "email":       arguments["email"],
                "priority":    arguments.get("priority",2),
                "status":      arguments.get("status",2),
            }
            for k in ("responder_id","workspace_id"):
                if arguments.get(k): payload[k] = arguments[k]
            data = await fs("POST","tickets",payload)
            t    = data.get("ticket") or data
            return [TextContent(type="text",text=
                f"Ticket créé : #{t.get('id')} — {t.get('subject')}\n"
                f"Statut    : {fmt_status(t.get('status'))}\n"
                f"Workspace : {fmt_workspace(t.get('workspace_id'))}\n"
                f"Agent     : {fmt_agent(t.get('responder_id'))}"
            )]

        elif name == "update_ticket":
            tid     = arguments["id"]
            payload = {k:arguments[k] for k in ("status","priority","responder_id") if k in arguments}
            if payload: await fs("PUT",f"tickets/{tid}",payload)
            if arguments.get("note"):
                await fs("POST",f"tickets/{tid}/notes",{"body":arguments["note"],"private":False})
            if arguments.get("private_note"):
                await fs("POST",f"tickets/{tid}/notes",{"body":arguments["private_note"],"private":True})
            actions = []
            for k,v in payload.items():
                if k=="status":       actions.append(f"statut → {fmt_status(v)}")
                elif k=="priority":   actions.append(f"priorité → {fmt_prio(v)}")
                elif k=="responder_id": actions.append(f"agent → {fmt_agent(v)}")
            if arguments.get("note"):         actions.append("note publique ajoutée")
            if arguments.get("private_note"): actions.append("note privée ajoutée")
            return [TextContent(type="text",text=f"Ticket #{tid} mis à jour : {', '.join(actions) or 'aucun changement'}.")]

        elif name == "list_workspaces":
            lines = [f"#{k} | {v}" for k,v in WORKSPACE.items()]
            return [TextContent(type="text",text="Workspaces Supergeek :\n"+"\n".join(lines))]

        elif name == "list_contacts":
            p    = arguments.get("page",1)
            pp   = arguments.get("per_page",30)
            q    = arguments.get("query","")
            path = f"contacts?page={p}&per_page={pp}"
            if q: path += f"&query={q}"
            data     = await fs("GET",path)
            contacts = data.get("contacts") or []
            if not contacts: return [TextContent(type="text",text="Aucun contact trouvé.")]
            lines = [
                f"#{c['id']} | {c.get('name','?')} | {c.get('email','—')} | "
                f"Tél:{c.get('phone','—')} | Mob:{c.get('mobile','—')} | "
                f"Société:{c.get('company_name','—')} | Actif:{fmt(c.get('active'))}"
                for c in contacts
            ]
            return [TextContent(type="text",text=f"{len(contacts)} contact(s) :\n"+"\n".join(lines))]

        elif name == "get_contact":
            data   = await fs("GET",f"contacts/{arguments['id']}")
            c      = data.get("contact") or data
            custom = c.get("custom_fields") or {}
            custom_lines = "\n".join([f"  {k}: {fmt(v)}" for k,v in custom.items()]) or "  Aucun"
            text = (
                f"{'═'*60}\nCONTACT #{c.get('id')}\n{'═'*60}\n"
                f"Nom            : {fmt(c.get('name'))}\n"
                f"Email          : {fmt(c.get('email'))}\n"
                f"Téléphone      : {fmt(c.get('phone'))}\n"
                f"Mobile         : {fmt(c.get('mobile'))}\n"
                f"Société        : {fmt(c.get('company_name'))}\n"
                f"Département    : {fmt(c.get('department'))}\n"
                f"Actif          : {fmt(c.get('active'))}\n"
                f"VIP            : {fmt(c.get('vip_user'))}\n"
                f"Créé le        : {safe_date(c.get('created_at'))}\n"
                f"{'─'*60}\nCHAMPS PERSONNALISÉS :\n{custom_lines}\n"
            )
            return [TextContent(type="text",text=text)]

        elif name == "list_agents":
            data   = await fs("GET","agents?per_page=100")
            agents = data.get("agents") or []
            if not agents: return [TextContent(type="text",text="Aucun agent trouvé.")]
            POSTES = {
                "Alice":"Technicien","Axel":"Technicien","Cedric":"Technicien",
                "Jason":"Technicien","Jessica":"Technicien","Léa":"Technicien",
                "Sam":"Technicien","Samuel":"Technicien",
                "Anaïs":"Commercial","Laeticia":"Commercial","Shelly":"Commercial",
                "James":"Responsable B2B","Yannick":"Superviseur",
                "Michel":"Founder","Khadijah":"HR","Superviseur":"Associé",
            }
            lines = []
            for a in agents:
                aid    = a.get('id')
                prenom = AGENTS.get(aid, f"{a.get('first_name','')} {a.get('last_name','')}".strip())
                poste  = POSTES.get(prenom,"—")
                lines.append(f"#{aid} | {prenom} | {a.get('email','—')} | {poste} | Dispo:{fmt(a.get('available'))}")
            return [TextContent(type="text",text="\n".join(lines))]

        elif name == "get_agent":
            data = await fs("GET",f"agents/{arguments['id']}")
            a    = data.get("agent") or data
            aid  = a.get('id')
            text = (
                f"{'═'*60}\nAGENT #{aid} — {AGENTS.get(aid,'Inconnu')}\n{'═'*60}\n"
                f"Nom        : {a.get('first_name','')} {a.get('last_name','')}\n"
                f"Email      : {fmt(a.get('email'))}\n"
                f"Actif      : {fmt(a.get('active'))}\n"
                f"Disponible : {fmt(a.get('available'))}\n"
                f"Rôles      : {fmt(a.get('role_ids'))}\n"
                f"Groupes    : {fmt(a.get('group_ids'))}\n"
                f"Créé le    : {safe_date(a.get('created_at'))}\n"
            )
            return [TextContent(type="text",text=text)]

        elif name == "list_projects":
            p  = arguments.get("page",1)
            pp = arguments.get("per_page",30)
            data     = await fs("GET",f"projects?page={p}&per_page={pp}")
            projects = data.get("projects") or []
            if not projects: return [TextContent(type="text",text="Aucun projet trouvé.")]
            lines = [
                f"#{pj['id']} | {pj.get('name','?')} | Statut:{pj.get('status','—')} | "
                f"Manager:{fmt_agent(pj.get('manager_id'))} | "
                f"Début:{safe_date(pj.get('start_date'),10)} | Fin:{safe_date(pj.get('end_date'),10)}"
                for pj in projects
            ]
            return [TextContent(type="text",text=f"{len(projects)} projet(s) :\n"+"\n".join(lines))]

        elif name == "get_project":
            data = await fs("GET",f"projects/{arguments['id']}")
            pj   = data.get("project") or data
            text = (
                f"{'═'*60}\nPROJET #{pj.get('id')}\n{'═'*60}\n"
                f"Nom        : {fmt(pj.get('name'))}\n"
                f"Description: {fmt(pj.get('description'))}\n"
                f"Statut     : {fmt(pj.get('status'))}\n"
                f"Manager    : {fmt_agent(pj.get('manager_id'))}\n"
                f"Début      : {safe_date(pj.get('start_date'),10)}\n"
                f"Fin        : {safe_date(pj.get('end_date'),10)}\n"
                f"Créé le    : {safe_date(pj.get('created_at'))}\n"
            )
            return [TextContent(type="text",text=text)]

        elif name == "list_ticket_tasks":
            data  = await fs("GET",f"tickets/{arguments['ticket_id']}/tasks")
            tasks = data.get("tasks") or []
            if not tasks: return [TextContent(type="text",text="Aucune tâche.")]
            lines = [
                f"#{t['id']} | {t.get('title','?')} | Statut:{t.get('status','—')} | "
                f"Agent:{fmt_agent(t.get('agent_id'))} | Échéance:{safe_date(t.get('due_date'),10)}\n"
                f"  {t.get('description','')[:200]}"
                for t in tasks
            ]
            return [TextContent(type="text",text=f"{len(tasks)} tâche(s) :\n"+"\n".join(lines))]

        elif name == "list_project_tasks":
            data  = await fs("GET",f"projects/{arguments['project_id']}/tasks")
            tasks = data.get("tasks") or []
            if not tasks: return [TextContent(type="text",text="Aucune tâche.")]
            lines = [
                f"#{t['id']} | {t.get('title','?')} | Statut:{t.get('status','—')} | "
                f"Assigné:{fmt_agent(t.get('assignee_id'))} | Échéance:{safe_date(t.get('due_date'),10)}"
                for t in tasks
            ]
            return [TextContent(type="text",text=f"{len(tasks)} tâche(s) :\n"+"\n".join(lines))]

        elif name == "list_assets":
            p  = arguments.get("page",1)
            pp = arguments.get("per_page",30)
            data   = await fs("GET",f"assets?page={p}&per_page={pp}")
            assets = data.get("assets") or []
            if not assets: return [TextContent(type="text",text="Aucun asset trouvé.")]
            lines = [
                f"#{a['id']} | {a.get('name','?')} | "
                f"Utilisateur:{fmt_agent(a.get('user_id'))} | État:{a.get('state','—')}"
                for a in assets
            ]
            return [TextContent(type="text",text=f"{len(assets)} asset(s) :\n"+"\n".join(lines))]

        elif name == "get_stats":
            data    = await fs("GET","tickets?filter=new_and_my_open&per_page=100&include=requester")
            tickets = data.get("tickets") or []
            by_status={};by_priority={};by_agent={};by_workspace={}
            for t in tickets:
                s=fmt_status(t.get('status'));p=fmt_prio(t.get('priority'))
                a=fmt_agent(t.get('responder_id'));ws=fmt_workspace(t.get('workspace_id'))
                by_status[s]=by_status.get(s,0)+1;by_priority[p]=by_priority.get(p,0)+1
                by_agent[a]=by_agent.get(a,0)+1;by_workspace[ws]=by_workspace.get(ws,0)+1
            lines = [
                f"{'═'*50}","STATISTIQUES FRESHSERVICE — SUPERGEEK",f"{'═'*50}",
                f"Tickets analysés : {len(tickets)}",f"{'─'*50}","PAR STATUT :",
                *[f"  {k}: {v}" for k,v in sorted(by_status.items(),key=lambda x:-x[1])],
                f"{'─'*50}","PAR PRIORITÉ :",
                *[f"  {k}: {v}" for k,v in sorted(by_priority.items(),key=lambda x:-x[1])],
                f"{'─'*50}","PAR WORKSPACE :",
                *[f"  {k}: {v}" for k,v in sorted(by_workspace.items(),key=lambda x:-x[1])],
                f"{'─'*50}","PAR AGENT (top 10) :",
                *[f"  {k}: {v} ticket(s)" for k,v in sorted(by_agent.items(),key=lambda x:-x[1])[:10]],
            ]
            return [TextContent(type="text",text="\n".join(lines))]

        else:
            return [TextContent(type="text",text=f"Outil inconnu : {name}")]

    except httpx.HTTPStatusError as e:
        return [TextContent(type="text",text=f"Erreur Freshservice {e.response.status_code} : {e.response.text[:300]}")]
    except Exception as e:
        return [TextContent(type="text",text=f"Erreur : {str(e)}")]

sse = SseServerTransport("/messages/")

async def handle_sse(request):
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())

async def health(request):
    return JSONResponse({"status":"ok","domain":FS_DOMAIN,"auth_set":bool(FS_API_KEY),"tools":20,"version":"7.0"})

app = Starlette(
    routes=[
        Route("/health", endpoint=health),
        Route("/sse",    endpoint=handle_sse),
        Mount("/messages", app=sse.handle_post_message),
    ],
    middleware=[Middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["*"],allow_headers=["*"])]
)
