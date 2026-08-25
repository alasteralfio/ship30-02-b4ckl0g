"""The request flow (Goal 7.1): a Steam ID in, an honest report out.

One route runs the whole live pipeline — read, reduce, place, recommend,
render — async where it talks to Steam. Nothing about a visitor is persisted:
the reference-store connection opened per request is read-only, and the only
in-process state that outlives a request is the rate limiter's hit counts and
the short-lived lookup cache (`rate_limit.py`), neither of which is a visitor
record.

Steam client, placement model, and store connection are all FastAPI
dependencies rather than built inline, so tests can override each one —
canned Steam responses, a synthetic placement model, an in-memory store —
without touching the network or a live key (CLAUDE.md: unit tests run against
recorded fixtures).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from b4cklog import store
from b4cklog.config import steam_api_key
from b4cklog.placement import PlacementModel, load
from b4cklog.steam import Library, PrivateProfile, SteamClient, SteamError, UnknownSteamID
from b4cklog.web.rate_limit import RateLimiter, TTLCache
from b4cklog.web.report import NoSignal, build_report
from b4cklog.web.svg import placement_svg

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

# Module-level singletons, not request state: their whole job is to persist
# across requests (rate-limit hit counts, cached lookups). Tests reach in and
# replace them directly for a deterministic threshold or clock.
rate_limiter = RateLimiter()
lookup_cache = TTLCache()

_model: PlacementModel | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _model
    # Loaded once at startup, never refit at request time — the frozen
    # artifact is the whole point of freezing it (PROJECT.md, "Placement").
    _model = load()
    yield


app = FastAPI(lifespan=lifespan)


def get_model() -> PlacementModel:
    if _model is None:
        raise RuntimeError("Placement model not loaded — app lifespan didn't run.")
    return _model


async def get_steam_client() -> AsyncIterator[SteamClient]:
    async with SteamClient(steam_api_key()) as client:
        yield client


async def get_conn() -> AsyncIterator:
    # Async, even though every call inside is synchronous sqlite3 — a *sync*
    # dependency on an async route gets dispatched to FastAPI's threadpool,
    # which would create the connection on a different thread than the one
    # `profile()` uses it on; sqlite3 connections are bound to their creating
    # thread and raise on cross-thread use. Async keeps dependency and route
    # on the same event-loop thread, which is what a brief local-file read
    # actually needs here — no threadpool required at this project's scale.
    conn = store.connect()
    try:
        yield conn
    finally:
        conn.close()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/profile", response_class=HTMLResponse)
async def profile(
    request: Request,
    steam_id: str,
    client: SteamClient = Depends(get_steam_client),
    model: PlacementModel = Depends(get_model),
    conn=Depends(get_conn),
) -> HTMLResponse:
    if not rate_limiter.allow(_client_ip(request)):
        return templates.TemplateResponse(request, "rate_limited.html", {}, status_code=429)

    cached = lookup_cache.get(steam_id)
    if cached is not None:
        library_result, achievements = cached
    else:
        try:
            library_result = await client.read_library(steam_id)
        except UnknownSteamID:
            return templates.TemplateResponse(
                request, "unknown.html", {"steam_id": steam_id}, status_code=404
            )
        except SteamError:
            return templates.TemplateResponse(request, "steam_error.html", {}, status_code=502)

        achievements = {}
        if isinstance(library_result, Library):
            achievements = await client.sample_achievements(library_result)
        lookup_cache.set(steam_id, (library_result, achievements))

    if isinstance(library_result, PrivateProfile):
        return templates.TemplateResponse(request, "private.html", {"result": library_result})

    report = build_report(library_result, achievements, model, conn)
    if isinstance(report, NoSignal):
        return templates.TemplateResponse(request, "no_signal.html", {"report": report})

    svg = placement_svg(report.placement)
    return templates.TemplateResponse(request, "report.html", {"report": report, "svg": svg})
