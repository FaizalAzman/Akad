"""Akad Observability Dashboard.

A small FastAPI + Jinja2 app that renders the contract registry's data:
overview stats, per-contract validation history, breach history, and
contract discovery/search. Server-rendered HTML styled with Tailwind
(loaded from the CDN — no frontend build step).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

REGISTRY_URL = os.environ.get("REGISTRY_URL", "http://localhost:8000")

app = FastAPI(title="Akad Dashboard")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


async def _get(path: str) -> list | dict:
    # Only needed when the registry requires auth for reads.
    token = os.environ.get("AKAD_API_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(base_url=REGISTRY_URL, headers=headers, timeout=5) as client:
        resp = await client.get(path)
        resp.raise_for_status()
        return resp.json()


@app.get("/", response_class=HTMLResponse)
async def overview(request: Request):
    error: str | None = None
    contracts: list = []
    results: list = []

    try:
        contracts = await _get("/contracts/")
        results = await _get("/validation-results/?limit=100")
    except Exception as exc:
        error = str(exc)

    counts = {"COMPLIANT": 0, "BREACH": 0, "ERROR": 0}
    for r in results:
        counts[r["overall_status"]] = counts.get(r["overall_status"], 0) + 1

    breach_events = [r for r in results if r["overall_status"] == "BREACH"][:10]

    return templates.TemplateResponse(request, "index.html", {
        "active": "overview",
        "error": error,
        "contracts": contracts,
        "counts": counts,
        "breach_events": breach_events,
    })


@app.get("/contracts/{name}", response_class=HTMLResponse)
async def contract_detail(request: Request, name: str):
    error: str | None = None
    detail: dict | None = None
    detail_json: str | None = None
    results: list = []

    try:
        detail = await _get(f"/contracts/{name}")
        detail_json = json.dumps(detail["content"], indent=2)
        results = await _get(f"/validation-results/?contract_name={name}&limit=30")
    except Exception as exc:
        error = str(exc)

    return templates.TemplateResponse(request, "contract_detail.html", {
        "active": "overview",
        "error": error,
        "name": name,
        "detail": detail,
        "detail_json": detail_json,
        "results": results,
    })


@app.get("/breaches", response_class=HTMLResponse)
async def breaches(request: Request, status: list[str] = Query(default=["BREACH", "ERROR"])):
    error: str | None = None
    results: list = []

    try:
        results = await _get("/validation-results/?limit=200")
    except Exception as exc:
        error = str(exc)

    filtered = [r for r in results if r["overall_status"] in status] if status else results

    return templates.TemplateResponse(request, "breaches.html", {
        "active": "breaches",
        "error": error,
        "results": filtered,
        "selected_status": status,
        "all_statuses": ["COMPLIANT", "BREACH", "ERROR"],
    })


@app.get("/discovery", response_class=HTMLResponse)
async def discovery(request: Request, q: str = ""):
    error: str | None = None
    contracts: list = []

    try:
        contracts = await _get("/contracts/")
    except Exception as exc:
        error = str(exc)

    if q:
        contracts = [c for c in contracts if q.lower() in c["name"].lower()]

    return templates.TemplateResponse(request, "discovery.html", {
        "active": "discovery",
        "error": error,
        "contracts": contracts,
        "query": q,
    })
