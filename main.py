# main.py
"""
FastAPI backend for Custom Domain Redirector (extension).
Endpoints:
 - POST /check       { "domain": "example.com" } -> { "redirect": "https://..." } or { "redirect": null }
 - POST /register    protected by API_KEY -> { "domain": "...", "redirect": "..." }
 - GET  /mappings    protected by API_KEY -> list of mappings
 - DELETE /unregister?domain=... protected by API_KEY
"""

import os
import sqlite3
from typing import Optional, List, Dict

from fastapi import FastAPI, HTTPException, Header, Request, status, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, AnyHttpUrl
from dotenv import load_dotenv

load_dotenv()  # load .env if exists

DB_PATH = os.getenv("DB_PATH", "/data/mappings.db")
API_KEY = os.getenv("API_KEY")  # if set, register/unregister/mappings require this key
ALLOW_ORIGINS = os.getenv("ALLOW_ORIGINS", "*")  # default allow all for testing

# ensure DB exists and table created
def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mappings (
            domain TEXT PRIMARY KEY,
            redirect TEXT
        )
        """
    )
    con.commit()
    con.close()

init_db()

app = FastAPI(title="Custom Redirector Backend")

# Configure CORS (for testing we allow all origins; in production set explicit origins)
if ALLOW_ORIGINS == "*":
    origins = ["*"]
else:
    origins = [o.strip() for o in ALLOW_ORIGINS.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Pydantic models
class CheckRequest(BaseModel):
    domain: str

class RegisterRequest(BaseModel):
    domain: str
    redirect: Optional[AnyHttpUrl] = None

class MappingItem(BaseModel):
    domain: str
    redirect: Optional[str] = None

# Helper DB functions
def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def normalize_domain(domain: str) -> str:
    return domain.strip().lower().removeprefix("http://").removeprefix("https://").removeprefix("www.").split("/")[0]

def get_redirect_for(domain: str) -> Optional[str]:
    d = normalize_domain(domain)
    con = get_conn()
    cur = con.cursor()
    cur.execute("SELECT redirect FROM mappings WHERE domain = ?", (d,))
    row = cur.fetchone()
    con.close()
    return row[0] if row else None

def set_mapping(domain: str, redirect: Optional[str]) -> None:
    d = normalize_domain(domain)
    con = get_conn()
    cur = con.cursor()
    cur.execute("INSERT INTO mappings(domain, redirect) VALUES(?, ?) ON CONFLICT(domain) DO UPDATE SET redirect=excluded.redirect", (d, redirect))
    con.commit()
    con.close()

def remove_mapping(domain: str) -> bool:
    d = normalize_domain(domain)
    con = get_conn()
    cur = con.cursor()
    cur.execute("DELETE FROM mappings WHERE domain = ?", (d,))
    changed = cur.rowcount
    con.commit()
    con.close()
    return bool(changed)

def list_mappings() -> List[MappingItem]:
    con = get_conn()
    cur = con.cursor()
    cur.execute("SELECT domain, redirect FROM mappings ORDER BY domain")
    rows = cur.fetchall()
    con.close()
    return [MappingItem(domain=r[0], redirect=r[1]) for r in rows]

# Dependency for API key protected endpoints
def require_api_key(x_api_key: Optional[str] = Header(None)):
    if API_KEY:
        if not x_api_key or x_api_key != API_KEY:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key")
    # if API_KEY is not set, don't require anything (open)
    return True

# Routes
@app.post("/check")
async def check(payload: CheckRequest, request: Request):
    """
    Called by extension: POST {"domain": "instagram.com"}
    Returns {"redirect": "https://..."} or {"redirect": null}
    """
    if not payload.domain:
        raise HTTPException(status_code=400, detail="missing domain")
    redirect = get_redirect_for(payload.domain)
    # return JSON with explicit null when missing
    return {"redirect": redirect}

@app.post("/register", dependencies=[Depends(require_api_key)])
async def register(payload: RegisterRequest):
    """
    Register or update a domain mapping.
    Protected by API_KEY header if API_KEY env var is set.
    Example request body:
      {"domain": "instagram.com", "redirect": "https://my-private/instagram"}
    To remove mapping using register, set redirect to null.
    """
    if not payload.domain:
        raise HTTPException(status_code=400, detail="missing domain")
    set_mapping(payload.domain, payload.redirect if payload.redirect else None)
    return {"ok": True, "domain": normalize_domain(payload.domain), "redirect": payload.redirect}

@app.get("/mappings", dependencies=[Depends(require_api_key)])
async def mappings():
    """
    Return all mappings.
    """
    return {"mapping": [m.dict() for m in list_mappings()]}

@app.delete("/unregister", dependencies=[Depends(require_api_key)])
async def unregister(domain: str = Query(..., description="domain to remove, e.g. example.com")):
    """
    Remove a mapping.
    """
    if not domain:
        raise HTTPException(status_code=400, detail="missing domain")
    ok = remove_mapping(domain)
    if not ok:
        raise HTTPException(status_code=404, detail="mapping not found")
    return {"ok": True, "domain": normalize_domain(domain)}

# small health endpoint
@app.get("/health")
async def health():
    return {"ok": True}

# optional convenience: bulk register (protected)
@app.post("/bulk_register", dependencies=[Depends(require_api_key)])
async def bulk_register(items: List[RegisterRequest]):
    for it in items:
        if it.domain:
            set_mapping(it.domain, str(it.redirect) if it.redirect else None)
    return {"ok": True, "added": len(items)}
