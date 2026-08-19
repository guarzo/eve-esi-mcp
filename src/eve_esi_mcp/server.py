from __future__ import annotations

import logging
import sys

import structlog
from fastmcp import FastMCP

from .config import get_settings
from .tools import activity, arbitrage, character, industry, market, universe, wormhole
from . import sso


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(message)s",
    )
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.KeyValueRenderer(key_order=["timestamp", "level", "event"]),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )


def build_server() -> FastMCP:
    _configure_logging()
    settings = get_settings()
    if not settings.contact.strip():
        print(
            "ERROR: EVE_ESI_MCP_CONTACT is not set. ESI requires a User-Agent "
            "identifying the operator (email, app URL, or Discord handle). Set "
            "it in .env or as an environment variable and restart.",
            file=sys.stderr,
        )
        sys.exit(2)

    mcp = FastMCP(
        name="eve-esi-mcp",
        instructions=(
            "Tools for EVE Online's ESI API: regional market orders/history, "
            "arbitrage comparison across trade hubs, industry cost indices and "
            "build-cost estimation, universe lookups, system kills/jumps activity, "
            "sovereignty claims, wormhole/J-space static info, and optional EVE SSO "
            "character-scoped tools (wallet/assets/orders/industry jobs). "
            "Common profit workflows: "
            "(1) resolve_ids([item_name]) → compare_hubs(type_id) to find a spread; "
            "(2) find_spreads(src, dst, candidate_type_ids) to scan a watchlist; "
            "(3) cheapest_system('manufacturing') → build_cost_estimate to sanity-check a build; "
            "(4) hottest_systems('ship_kills') to avoid dangerous routes. "
            "Prefer the narrow tool over the broad one: best_bid_ask over raw market_orders, "
            "cheapest_system over industry_systems, hottest_systems over system_kills. "
            "Always pass type_id to market_orders — an unfiltered region scan is hundreds "
            "of pages and returns only the first by default. "
            "Any result carrying \"truncated\": true is a partial dataset; do not treat it "
            "as complete. PLEX (type_id 44992) trades only on the global market, region "
            "19000001; market tools redirect there automatically and say so. "
            "Always respect ESI cache — repeated calls within the Expires window "
            "hit local disk, not the API."
        ),
    )

    # Market
    mcp.tool(market.market_orders)
    mcp.tool(market.market_history)
    mcp.tool(market.market_prices)
    mcp.tool(market.best_bid_ask)
    mcp.tool(market.top_traded)

    # Arbitrage
    mcp.tool(arbitrage.compare_hubs)
    mcp.tool(arbitrage.find_spreads)
    mcp.tool(arbitrage.freight_cost)
    mcp.tool(arbitrage.profit_after_fees)

    # Industry
    mcp.tool(industry.industry_systems)
    mcp.tool(industry.cheapest_system)
    mcp.tool(industry.build_cost_estimate)

    # Universe
    mcp.tool(universe.resolve_names)
    mcp.tool(universe.resolve_ids)
    mcp.tool(universe.region_info)
    mcp.tool(universe.constellation_info)
    mcp.tool(universe.system_info)
    mcp.tool(universe.type_info)
    mcp.tool(universe.list_regions)
    mcp.tool(universe.route)
    mcp.tool(universe.jumps_between)

    # Activity
    mcp.tool(activity.system_kills)
    mcp.tool(activity.system_jumps)
    mcp.tool(activity.sovereignty_systems)
    mcp.tool(activity.sovereignty_campaigns)
    mcp.tool(activity.hottest_systems)

    # Wormhole
    mcp.tool(wormhole.jspace_info)
    mcp.tool(wormhole.lookup_statics)

    # SSO (optional). Two-step is preferred — sso_login is a blocking convenience
    # wrapper that's awkward to drive from an MCP client.
    mcp.tool(sso.sso_login_start)
    mcp.tool(sso.sso_login_finish)
    mcp.tool(sso.sso_login)
    mcp.tool(sso.sso_status)
    mcp.tool(sso.list_characters)
    mcp.tool(sso.sso_logout)

    # Character (requires sso_login first)
    mcp.tool(character.my_wallet)
    mcp.tool(character.my_wallet_journal)
    mcp.tool(character.my_wallet_transactions)
    mcp.tool(character.my_assets)
    mcp.tool(character.my_open_orders)
    mcp.tool(character.my_skills)
    mcp.tool(character.my_industry_jobs)

    return mcp
