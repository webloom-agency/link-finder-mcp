"""
Link Finder MCP server.

Exposes the Link Finder API (https://app.link-finder.net) as Model Context
Protocol tools so AI agents (Claude, ChatGPT, Cursor, ...) can find backlink
opportunities, analyze competitors, and manage prospecting projects.

Design goals (matching the sibling Python MCP projects in this GitHub folder):
  - No secrets in code. Everything comes from environment variables.
  - Render-friendly but host-agnostic: binds 0.0.0.0:$PORT in hosted mode.
  - Two transports: `stdio` for local clients, `sse`/`http` for hosted servers
    (protected by a shared bearer token).
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

load_dotenv()

# GET endpoints carry the API key in the query string, and httpx logs the full
# request URL at INFO level — which would leak the key into hosted logs (Render,
# etc.). Silence httpx's request logging so the key never appears in plaintext.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# --------------------------------------------------------------------------- #
# Configuration (everything via environment variables — no secrets in code)
# --------------------------------------------------------------------------- #
API_KEY = os.getenv("LINK_FINDER_API_KEY")
BASE_URL = os.getenv(
    "LINK_FINDER_BASE_URL", "https://app.link-finder.net/api/v2"
).rstrip("/")
TRANSPORT = os.getenv("MCP_TRANSPORT", "stdio").strip().lower()
MCP_BEARER_TOKEN = os.getenv("MCP_BEARER_TOKEN")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
DATA_DIR = os.getenv("LINK_FINDER_DATA_DIR", "data").strip()
HTTP_TIMEOUT = float(os.getenv("LINK_FINDER_HTTP_TIMEOUT", "120"))

# Result shaping defaults. The Link Finder search endpoints return the full
# result set (no server-side limit), so we bound memory/payload on our side:
# filter + sort + slice + project to a slim set before returning or saving.
DEFAULT_LIMIT = 50
MAX_LIMIT = 500
SORTABLE_FIELDS = ("rd", "tf", "cf", "dr", "traffic", "backlinks")

# Slim projection: scalar fields needed to assess relevance, authority, and
# price — instead of the ~131 columns the API returns per domain.
SLIM_FIELDS = (
    "domain_id",
    "domain",
    "title",
    "dr",
    "tf",
    "cf",
    "rd",
    "backlinks",
    "traffic",
    "anchors_text",
    "ttf0",
    "ai_lang",
    "gg_news",
    "best_price_platform",
)

# Behind a PaaS/proxy (Render, Railway, Fly, ...) the public Host header won't
# be localhost, which trips FastMCP's DNS-rebinding protection (HTTP 421). The
# endpoint is already protected by MCP_BEARER_TOKEN, so we disable host
# checking by default and let users opt back in with an explicit allowlist.
_allowed_hosts = [h.strip() for h in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
_allowed_origins = [o.strip() for o in os.getenv("MCP_ALLOWED_ORIGINS", "").split(",") if o.strip()]
if _allowed_hosts or _allowed_origins:
    _transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts or ["*"],
        allowed_origins=_allowed_origins or ["*"],
    )
else:
    _transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# Streamable HTTP is the robust transport behind PaaS proxies (Render, etc.):
# unlike SSE, it doesn't keep the session glued to a single long-lived stream
# that proxies love to buffer/reset. Running it stateless (default) sidesteps
# the "request before initialization complete" handshake failures entirely.
STATELESS_HTTP = _env_bool("MCP_STATELESS_HTTP", True)
# Keep SSE-framed responses by default — every Streamable HTTP client accepts
# them, whereas plain-JSON responses (json_response=True) are an optimization
# some clients reject. Opt in with MCP_JSON_RESPONSE=true only if needed.
JSON_RESPONSE = _env_bool("MCP_JSON_RESPONSE", False)

mcp = FastMCP(
    "Link Finder MCP",
    transport_security=_transport_security,
    stateless_http=STATELESS_HTTP,
    json_response=JSON_RESPONSE,
)


# --------------------------------------------------------------------------- #
# Reference data (kept in sync with the public API docs)
# --------------------------------------------------------------------------- #
LOCATIONS: list[dict[str, Any]] = [
    {"id": 2840, "name": "United States", "lang_code": "en"},
    {"id": 2826, "name": "UK", "lang_code": "en"},
    {"id": 2250, "name": "France", "lang_code": "fr"},
    {"id": 2724, "name": "Spain", "lang_code": "es"},
    {"id": 2032, "name": "Argentina", "lang_code": "ar"},
    {"id": 2036, "name": "Australia", "lang_code": "en"},
    {"id": 2056, "name": "Belgium", "lang_code": "be"},
    {"id": 2076, "name": "Brazil", "lang_code": "pt"},
    {"id": 2124, "name": "Canada", "lang_code": "ca"},
    {"id": 2170, "name": "Colombia", "lang_code": "es"},
    {"id": 2203, "name": "Czechia", "lang_code": "cs"},
    {"id": 2276, "name": "Germany", "lang_code": "de"},
    {"id": 2380, "name": "Italy", "lang_code": "it"},
    {"id": 2442, "name": "Luxembourg", "lang_code": "fr"},
    {"id": 2484, "name": "Mexico", "lang_code": "es"},
    {"id": 2528, "name": "Netherlands", "lang_code": "nl"},
    {"id": 2616, "name": "Poland", "lang_code": "pl"},
    {"id": 2620, "name": "Portugal", "lang_code": "pt"},
    {"id": 2642, "name": "Romania", "lang_code": "ro"},
    {"id": 2703, "name": "Slovakia", "lang_code": "sk"},
    {"id": 2752, "name": "Sweden", "lang_code": "se"},
    {"id": 2756, "name": "Switzerland", "lang_code": "fr"},
]


# --------------------------------------------------------------------------- #
# HTTP helper
# --------------------------------------------------------------------------- #
async def _lf_request(
    method: str,
    endpoint: str,
    *,
    params: Optional[dict[str, Any]] = None,
    data: Optional[dict[str, Any]] = None,
) -> Any:
    """Call a Link Finder endpoint, injecting the API key from the environment.

    The API key is never accepted as a tool argument — it is read from
    LINK_FINDER_API_KEY so it can never leak through the model context.
    """
    if not API_KEY:
        return {
            "error": "LINK_FINDER_API_KEY is not set. Add it to your environment "
            "or .env file. Get a key at https://app.link-finder.net/account/"
        }

    url = f"{BASE_URL}/{endpoint}"
    method = method.upper()

    # Drop None values so we only send the parameters the caller actually set.
    clean_params = {k: v for k, v in (params or {}).items() if v is not None}
    clean_data = {k: v for k, v in (data or {}).items() if v is not None}

    if method == "GET":
        clean_params["apiKey"] = API_KEY
    else:
        clean_data["apiKey"] = API_KEY

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.request(
                method,
                url,
                params=clean_params or None,
                data=clean_data or None,
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        body = exc.response.text
        try:
            return exc.response.json()
        except Exception:
            return {
                "error": f"HTTP {exc.response.status_code}",
                "detail": body[:1000],
            }
    except httpx.HTTPError as exc:
        return {"error": f"Request failed: {exc}"}
    except json.JSONDecodeError:
        return {"error": "Link Finder API returned a non-JSON response."}


# --------------------------------------------------------------------------- #
# Local persistence (best-practice logging from the API docs)
# --------------------------------------------------------------------------- #
def _slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return (slug or "search")[:max_len]


def _count_results(data: Any) -> int:
    if isinstance(data, dict):
        if isinstance(data.get("results"), list):
            return len(data["results"])
        # similarDomains shape: {"domain.com": {"rows": [...]}}
        total = 0
        found_rows = False
        for value in data.values():
            if isinstance(value, dict) and isinstance(value.get("rows"), list):
                total += len(value["rows"])
                found_rows = True
        if found_rows:
            return total
        if isinstance(data.get("favorites"), list):
            return len(data["favorites"])
        if isinstance(data.get("projects"), list):
            return len(data["projects"])
    if isinstance(data, list):
        return len(data)
    return 0


def _save_search(search_type: str, params: dict[str, Any], data: Any) -> Optional[str]:
    """Persist results to disk and append to data/searchHistory.json.

    Returns the relative results file path, or None when saving is disabled or
    the response was an error.
    """
    if not DATA_DIR:
        return None
    if isinstance(data, dict) and "error" in data and len(data) == 1:
        return None

    try:
        data_path = Path(DATA_DIR)
        data_path.mkdir(parents=True, exist_ok=True)

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        seed = (
            params.get("keywords")
            or params.get("keyword")
            or params.get("competitor")
            or params.get("domain")
            or params.get("url")
            or params.get("urls")
            or params.get("name")
            or (f"project-{params['project_id']}" if params.get("project_id") else "")
            or search_type
        )
        slug = _slugify(seed)
        stamp = datetime.now(timezone.utc).strftime("%H%M%S")
        results_file = data_path / f"{search_type}_{date_str}_{slug}_{stamp}.json"
        results_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        credits_used = None
        if isinstance(data, dict):
            credits_used = data.get("keywords_used")

        history_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": search_type,
            "params": params,
            "results_file": str(results_file),
            "results_count": _count_results(data),
            "credits_used": credits_used,
        }

        history_path = data_path / "searchHistory.json"
        history: list[dict[str, Any]] = []
        if history_path.exists():
            try:
                history = json.loads(history_path.read_text(encoding="utf-8"))
                if not isinstance(history, list):
                    history = []
            except Exception:
                history = []
        history.append(history_entry)
        history_path.write_text(
            json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return str(results_file)
    except Exception:
        # Never let a disk problem break an otherwise successful API call.
        return None


def _with_saved_path(data: Any, saved_path: Optional[str]) -> Any:
    """Attach the local file path to a dict response (non-destructive)."""
    if saved_path and isinstance(data, dict):
        data = {**data, "_saved_to": saved_path}
    return data


def _num(value: Any) -> float:
    """Coerce a metric to a float for sorting/filtering; missing -> -inf."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("-inf")


def _best_price(row: dict[str, Any]) -> tuple[Any, Any]:
    """Return (best_price, best_price_url) using the row's best_price_platform."""
    platform = row.get("best_price_platform")
    if not platform:
        return None, None
    return row.get(platform), row.get(f"{platform}_url")


def _slim_row(row: dict[str, Any]) -> dict[str, Any]:
    """Project a fat (~131-column) domain row down to the essential fields."""
    if not isinstance(row, dict):
        return row
    slim = {k: row.get(k) for k in SLIM_FIELDS if k in row}
    price, url = _best_price(row)
    if price is not None:
        slim["best_price"] = price
    if url is not None:
        slim["best_price_url"] = url
    return slim


def _shape_results(
    data: Any,
    *,
    limit: int,
    offset: int,
    order_by: str,
    min_tf: Optional[float],
    full: bool,
) -> Any:
    """Filter, sort, paginate and (by default) slim a search response.

    Bounds both memory and payload: the big parsed result lists are replaced by
    a small bounded slice, and the original fat structure is dropped so it can
    be garbage-collected before we serialize the response.
    """
    if not isinstance(data, dict) or "error" in data:
        return data

    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))
    order_by = order_by if order_by in SORTABLE_FIELDS else "rd"

    def shape_list(rows: Any) -> tuple[list[Any], int]:
        if not isinstance(rows, list):
            return [], 0
        if min_tf is not None:
            rows = [r for r in rows if isinstance(r, dict) and _num(r.get("tf")) >= min_tf]
        rows.sort(key=lambda r: _num(r.get(order_by)) if isinstance(r, dict) else float("-inf"),
                  reverse=True)
        total = len(rows)
        window = rows[offset : offset + limit]
        if not full:
            window = [_slim_row(r) for r in window]
        return window, total

    results, results_total = shape_list(data.get("results"))
    forums, forums_total = shape_list(data.get("forums"))
    expireds, expireds_total = shape_list(data.get("expireds"))

    shaped: dict[str, Any] = {
        "results": results,
        "forums": forums,
        "expireds": expireds,
        "pagination": {
            "order_by": order_by,
            "limit": limit,
            "offset": offset,
            "min_tf": min_tf,
            "slim": not full,
            "results_total": results_total,
            "results_returned": len(results),
            "forums_total": forums_total,
            "expireds_total": expireds_total,
        },
    }
    # Pass through useful scalar metadata without the fat lists.
    for key in ("stats", "keywords_used", "search_id", "title"):
        if key in data:
            shaped[key] = data[key]
    return shaped


# ===========================================================================
# UTILITIES
# ===========================================================================
@mcp.tool()
async def get_account() -> Any:
    """Get your Link Finder plan, remaining credits, and available features.

    Always call this FIRST in an automated workflow to confirm which endpoints
    your plan unlocks and how many credits remain before spending any.
    """
    return await _lf_request("GET", "getAccount.php")


@mcp.tool()
async def list_platforms() -> Any:
    """List every supported netlinking platform (ereferer, paperclub, ...)."""
    return await _lf_request("GET", "listPlatforms.php")


@mcp.tool()
async def list_locations() -> Any:
    """List available countries/locations for keyword search.

    Use the returned `id` as the `language` argument of `keyword_search` and
    `ai_search`. Served from the live API, with a built-in fallback list.
    """
    data = await _lf_request("GET", "listLocations.php")
    if isinstance(data, dict) and "locations" in data:
        return data
    # Fallback to the bundled reference list if the API call failed.
    return {"locations": LOCATIONS, "_source": "bundled-fallback"}


# ===========================================================================
# SEARCH
# ===========================================================================
@mcp.tool()
async def keyword_search(
    language: int,
    keyword: Optional[str] = None,
    keywords: Optional[str] = None,
    search_engine: str = "google",
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    order_by: str = "rd",
    min_tf: Optional[float] = None,
    full: bool = False,
) -> Any:
    """Find backlink opportunities from keywords (SERP analysis).

    Costs 1 `keywords_search` credit per keyword. Credits are only consumed
    when results are found. This is the best entry point to discover relevant
    sites in a niche.

    Results are sorted, bounded and slimmed before being returned (and saved)
    to keep memory/payload small — the API returns every match in one blob, so
    `limit`/`offset` paginate over that blob on this server's side.

    Args:
        language: Location ID from `list_locations` (e.g. 2250 France, 2840 US).
        keyword: A single keyword. Use this OR `keywords`.
        keywords: Multiple keywords separated by ";" (e.g. "best vpn;vpn free").
        search_engine: "google" (default) or "bing".
        limit: Max domains to return (default 50, hard cap 500).
        offset: Skip this many domains (for pagination).
        order_by: Sort field, descending: rd, tf, cf, dr, traffic, backlinks.
        min_tf: Keep only domains with Trust Flow >= this value.
        full: Return all ~131 columns per domain instead of the slim projection.
    """
    if not keyword and not keywords:
        return {"error": "Provide either `keyword` or `keywords`."}
    params = {
        "keyword": keyword,
        "keywords": keywords,
        "language": language,
        "search_engine": search_engine,
    }
    data = await _lf_request("POST", "kwSearch.php", data=params)
    shaped = _shape_results(
        data, limit=limit, offset=offset, order_by=order_by, min_tf=min_tf, full=full
    )
    del data
    saved = _save_search("kwSearch", {k: v for k, v in params.items() if v is not None}, shaped)
    return _with_saved_path(shaped, saved)


@mcp.tool()
async def competitor_analysis(
    competitor: str,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    order_by: str = "rd",
    min_tf: Optional[float] = None,
    full: bool = False,
) -> Any:
    """Analyze a competitor's referring domains available on netlinking platforms.

    Costs 1 `analyse_concurentielle` credit per request (only if results found).

    Big competitors can return hundreds of referring domains (~hundreds of KB).
    The API has no server-side limit, so results are sorted, bounded and slimmed
    here before being returned (and saved) to keep memory/payload small.

    Args:
        competitor: Competitor domain, e.g. "competitor.com".
        limit: Max domains to return (default 50, hard cap 500).
        offset: Skip this many domains (for pagination).
        order_by: Sort field, descending: rd, tf, cf, dr, traffic, backlinks.
        min_tf: Keep only domains with Trust Flow >= this value.
        full: Return all ~131 columns per domain instead of the slim projection.
    """
    params = {"competitor": competitor}
    data = await _lf_request("POST", "competitor.php", data=params)
    shaped = _shape_results(
        data, limit=limit, offset=offset, order_by=order_by, min_tf=min_tf, full=full
    )
    del data
    saved = _save_search("competitor", params, shaped)
    return _with_saved_path(shaped, saved)


@mcp.tool()
async def ai_search(
    url: str,
    ia_desc: str,
    ia_kw: str,
    language: int,
    conc1: Optional[str] = None,
    conc2: Optional[str] = None,
    conc3: Optional[str] = None,
) -> Any:
    """AI-powered backlink prospecting (SERP + competitor + semantic matching).

    Costs 1 `ai_search` credit per request. Returns extra scoring fields such
    as `linkFinderScore` (1-99 relevance), `kwsFound`, `categoryScore`,
    `serpScore`, and `concCount`.

    Args:
        url: Your domain, e.g. "mysite.com".
        ia_desc: Short description of your website / niche.
        ia_kw: Focus keywords, comma separated.
        language: Location ID from `list_locations`.
        conc1: Optional competitor domain #1.
        conc2: Optional competitor domain #2.
        conc3: Optional competitor domain #3.
    """
    params = {
        "url": url,
        "ia_desc": ia_desc,
        "ia_kw": ia_kw,
        "language": language,
        "conc1": conc1,
        "conc2": conc2,
        "conc3": conc3,
    }
    data = await _lf_request("POST", "aiSearch.php", data=params)
    saved = _save_search("aiSearch", {k: v for k, v in params.items() if v is not None}, data)
    return _with_saved_path(data, saved)


@mcp.tool()
async def similar_domains(
    domain: Optional[str] = None,
    project_id: Optional[int] = None,
    currency: str = "euros",
) -> Any:
    """Find domains similar to a seed domain (or to a whole project) via AI embeddings.

    One of the most powerful features: it surfaces hidden gems you won't find
    through keyword search. Costs 1 `similar_domains_api` credit per domain
    search, or 1 `similar_search` credit per project search. Returns up to 50
    similar domains with SEO metrics.

    Args:
        domain: Seed domain, e.g. "example.com". Use this OR `project_id`.
        project_id: Use all domains in this project as seeds. Use this OR `domain`.
        currency: "euros" (default) or "dollars".
    """
    if not domain and not project_id:
        return {"error": "Provide either `domain` or `project_id`."}
    params = {"domain": domain, "project_id": project_id, "currency": currency}
    data = await _lf_request("GET", "similarDomains.php", params=params)
    saved = _save_search(
        "similarDomains", {k: v for k, v in params.items() if v is not None}, data
    )
    return _with_saved_path(data, saved)


# ===========================================================================
# PROJECTS
# ===========================================================================
@mcp.tool()
async def create_project(name: str, domain: Optional[str] = None) -> Any:
    """Create a project to organize favorite domains.

    Args:
        name: Project name (max 255 characters).
        domain: Optional main domain for the project, e.g. "mysite.com".
    """
    return await _lf_request("POST", "createProject.php", data={"name": name, "domain": domain})


@mcp.tool()
async def list_projects() -> Any:
    """List all projects with their favorite and ordered counts."""
    return await _lf_request("GET", "listProjects.php")


@mcp.tool()
async def project_favorites(project_id: int) -> Any:
    """Get all favorite domains in a project, with full SEO metrics and prices.

    Args:
        project_id: Project ID (from `list_projects`).
    """
    return await _lf_request(
        "GET", "projectFavorites.php", params={"project_id": project_id}
    )


@mcp.tool()
async def add_favorite(project_id: int, domain_id: int, action: str = "add") -> Any:
    """Add or remove a domain from a project.

    Args:
        project_id: Project ID (from `list_projects`).
        domain_id: Domain ID (from any search result).
        action: "add" (default) or "remove".
    """
    return await _lf_request(
        "POST",
        "addFavorite.php",
        data={"project_id": project_id, "domain_id": domain_id, "action": action},
    )


@mcp.tool()
async def update_note(project_id: int, domain_id: int, note: str) -> Any:
    """Add or update a note on a favorite domain.

    Reserve notes for standout opportunities (exceptional value, perfect
    thematic fit). Do NOT annotate every domain.

    Args:
        project_id: Project ID (from `list_projects`).
        domain_id: Domain ID (must already be a favorite in the project).
        note: Note text (max 500 characters).
    """
    return await _lf_request(
        "POST",
        "updateNote.php",
        data={"project_id": project_id, "domain_id": domain_id, "note": note},
    )


# ===========================================================================
# DATA (API plan only — checkDomain & bulk)
# ===========================================================================
@mcp.tool()
async def check_domain(domain: str) -> Any:
    """Check a single domain across all netlinking platforms (API plan only).

    Returns SEO metrics, per-platform prices, and direct `_url` links.
    Exclusive to the API plan (250€/month).

    Args:
        domain: Domain or URL to check, e.g. "example.com".
    """
    data = await _lf_request("GET", "checkDomain.php", params={"domain": domain})
    saved = _save_search("checkDomain", {"domain": domain}, data)
    return _with_saved_path(data, saved)


@mcp.tool()
async def bulk_check(urls: str) -> Any:
    """Check up to 50,000 domains at once (API plan only).

    Args:
        urls: Domains separated by ";" (e.g. "site1.com;site2.com;site3.com").
    """
    data = await _lf_request("POST", "bulk.php", data={"urls": urls})
    saved = _save_search("bulk", {"urls": urls}, data)
    return _with_saved_path(data, saved)


# ===========================================================================
# LOCAL HISTORY
# ===========================================================================
@mcp.tool()
async def get_search_history() -> Any:
    """Read the locally saved search history (data/searchHistory.json).

    Check this before launching a new search to avoid duplicate work and save
    credits. Returns an empty list when nothing has been saved yet.
    """
    if not DATA_DIR:
        return {"history": [], "note": "Local saving is disabled (LINK_FINDER_DATA_DIR is empty)."}
    history_path = Path(DATA_DIR) / "searchHistory.json"
    if not history_path.exists():
        return {"history": []}
    try:
        return {"history": json.loads(history_path.read_text(encoding="utf-8"))}
    except Exception as exc:
        return {"error": f"Could not read search history: {exc}"}


# ===========================================================================
# WORKFLOW PROMPT
# ===========================================================================
@mcp.prompt()
def backlink_workflow() -> str:
    """Guided interview + workflow for finding backlink opportunities."""
    return (
        "You are an assistant that helps users find backlink opportunities using "
        "the Link Finder MCP tools.\n\n"
        "Start by asking what they want to do:\n"
        "1. Search for backlinks (guided, step by step)\n"
        "2. Integrate the API into code\n\n"
        "If they choose Search, interview them ONE QUESTION AT A TIME:\n"
        "1. Website / niche (domain + what they do)\n"
        "2. Target keywords\n"
        "3. Country/language (call `list_locations` and show the options)\n"
        "4. Authority preference: DR (Ahrefs) or TF/CF (Majestic)?\n"
        "5. Minimum authority score (suggest DR 20+ / TF 15+ as defaults)\n"
        "6. Minimum organic traffic (suggest 500+)\n"
        "7. Budget per link\n"
        "8. Need Google News sites? (filter gg_news == 1)\n"
        "9. Competitors to analyze?\n\n"
        "Then run the workflow:\n"
        "- Step 1: `get_account` to verify credits and available features.\n"
        "- Step 2: `keyword_search` first (best entry point); add `competitor_analysis` "
        "if they gave competitors. Filter by dr/tf, traffic, ai_lang, price, ttf0/title, gg_news. "
        "Present a table: domain, title, preferred metric, traffic, language, best price + platform.\n"
        "- Step 3: `create_project`, save the best with `add_favorite` (action=add); only "
        "`update_note` on exceptional gems; remove irrelevant ones with action=remove.\n"
        "- Step 4: expand with `similar_domains` on the top thematic matches, then chain on "
        "the best of those. Save the best finds to the same project.\n"
        "- Step 5: results are saved locally automatically; use `get_search_history` to avoid "
        "duplicate searches.\n\n"
        "Always show `title` next to `domain` so relevance is obvious at a glance."
    )


# --------------------------------------------------------------------------- #
# Entry point / transport handling
# --------------------------------------------------------------------------- #
def _build_hosted_app(transport: str):
    """Build a Starlette app guarded by a bearer-token middleware."""
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    if not MCP_BEARER_TOKEN:
        raise ValueError(
            "MCP_BEARER_TOKEN is required when MCP_TRANSPORT is 'sse' or 'http'. "
            "Set a long random string so only authorized clients can connect."
        )

    protected_prefixes = ("/sse", "/messages", "/mcp")

    class BearerTokenMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if request.url.path.startswith(protected_prefixes):
                auth_header = request.headers.get("Authorization", "")
                if not auth_header.startswith("Bearer "):
                    return JSONResponse(
                        status_code=401,
                        content={"error": "Missing or malformed Authorization header. "
                                          "Expected 'Bearer <token>'."},
                    )
                if auth_header[7:] != MCP_BEARER_TOKEN:
                    return JSONResponse(
                        status_code=401, content={"error": "Invalid bearer token"}
                    )
            return await call_next(request)

    if transport == "http":
        app = mcp.streamable_http_app()
    else:
        app = mcp.sse_app()
    app.add_middleware(BearerTokenMiddleware)
    return app


def main() -> None:
    if not API_KEY:
        raise ValueError(
            "LINK_FINDER_API_KEY not found. Set it in your environment or .env file. "
            "Get a key at https://app.link-finder.net/account/"
        )

    if TRANSPORT == "stdio":
        mcp.run(transport="stdio")
        return

    if TRANSPORT in ("sse", "http"):
        import uvicorn

        app = _build_hosted_app(TRANSPORT)
        uvicorn.run(app, host=HOST, port=PORT, log_level="info")
        return

    raise ValueError(
        f"Unknown MCP_TRANSPORT '{TRANSPORT}'. Use 'stdio', 'sse', or 'http'."
    )


if __name__ == "__main__":
    main()
