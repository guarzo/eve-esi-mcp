# eve-esi-mcp

A local [Model Context Protocol](https://modelcontextprotocol.io) server that gives
an LLM client (Claude Code, Claude Desktop, any MCP-compatible app) structured
access to the **EVE Online ESI API** — market data, industry cost indices,
universe lookups, system activity, wormhole/J-space statics, and (optionally)
your own character's wallet/assets/orders via EVE SSO.

The goal is turning questions like:

- *"What's the Jita→Amarr spread on PLEX right now, and is it worth hauling after fees?"*
- *"Which system has the cheapest manufacturing cost index this week, within 10 jumps of Jita?"*
- *"Which low-sec systems are hottest by ship kills right now — avoid this route."*
- *"What's the static chain for a C5 wolf-rayet wormhole?"*

…into tool calls the model can chain together, rather than copy-paste screenshots.

## ESI compliance

This server follows CCP's [ESI best practices](https://developers.eveonline.com/docs/services/esi/best-practices/):

- **User-Agent** built from `EVE_ESI_MCP_CONTACT` (required — the server refuses to start without it).
- **HTTP caching** via `hishel` on disk, honouring `Expires` and `ETag` responses; repeated calls within the cache window never hit CCP.
- **Error-limit awareness** — reads `X-ESI-Error-Limit-Remain`/`Reset` after every response and sleeps when low; 420s back off with exponential jitter.
- **Pagination** — `X-Pages` walked with bounded concurrency and jitter.
- **Read-only** — no write endpoints wired up.

## Install / run

Requires Python 3.11+.

```bash
git clone <this-repo> && cd eve-esi-mcp
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
# edit .env: set EVE_ESI_MCP_CONTACT to an email / app URL / Discord handle
.venv/bin/eve-esi-mcp            # stdio transport (default)
```

Or with `uv`:

```bash
uvx --from . eve-esi-mcp
```

### Wire into Claude Desktop / Claude Code

`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or equivalent:

```json
{
  "mcpServers": {
    "eve-esi": {
      "command": "/absolute/path/to/eve-esi-mcp/.venv/bin/eve-esi-mcp",
      "env": {
        "EVE_ESI_MCP_CONTACT": "you@example.com"
      }
    }
  }
}
```

## Tools exposed

### Market

- `market_orders(region, type_id?, order_type, location_id?, page_limit?)`
- `market_history(region, type_id)` — 400 days of daily OHLC-ish
- `market_prices()` — global average + adjusted price
- `best_bid_ask(region, type_id)` — top bid/ask, spread, 7d liquidity
- `top_traded(region, type_ids[], days, limit)` — rank a watchlist by ISK turnover

### Arbitrage

- `compare_hubs(type_id, hubs?)` — Jita/Amarr/Dodixie/Rens/Hek side-by-side
- `find_spreads(src_hub, dst_hub, type_ids[], min_margin_pct, min_daily_isk)`
- `freight_cost(m3, collateral, rate_per_m3, collateral_pct, minimum_fee)`
- `profit_after_fees(buy, sell, qty, sales_tax_pct, broker_fee_pct_buy, broker_fee_pct_sell, freight_isk)`

### Industry

- `industry_systems(activity?)` — cost indices per system
- `cheapest_system(activity, limit, nearest_to?)` — rank + optional jump distance
- `build_cost_estimate(product_type_id, materials[], system_id, runs, me_pct, job_tax_pct, facility_bonus_pct)` — EIV-style

### Universe

- `resolve_ids(names[])` / `resolve_names(ids[])`
- `region_info`, `constellation_info`, `system_info`, `type_info`, `list_regions`
- `route(origin, destination, flag, avoid?)` / `jumps_between(origin, destination)`

### Activity

- `system_kills()` / `system_jumps()` — last-hour heat map
- `hottest_systems(kind, limit)`
- `sovereignty_map()` / `sovereignty_campaigns()`

### Wormhole / J-space

- `jspace_info(system)` — bundled static data + ESI universe data
- `lookup_statics(wh_class)` — all J-systems of a given class

> **Note**: Live wormhole connections are *not* in ESI — maintain those in
> Pathfinder/Tripwire. The bundled `src/eve_esi_mcp/data/jspace_statics.csv` is
> a small sample; replace with an SDE-derived full dump for real use. See
> [CCP's Static Data Export](https://developers.eveonline.com/docs/services/sde/).

### Character (optional — EVE SSO)

These require a logged-in character. Register a developer app at
<https://developers.eveonline.com/applications>, type *Authentication Only* or
*Authentication & API Access* with **PKCE enabled**, and set the callback to
`http://localhost:8765/callback`. Then set `EVE_SSO_CLIENT_ID` in `.env` and:

- `sso_login()` — opens the EVE SSO auth URL, captures the code on localhost
- `sso_status()`, `sso_logout()`
- `my_wallet()`, `my_wallet_journal()`, `my_assets()`, `my_open_orders()`, `my_skills()`, `my_industry_jobs()`

Refresh tokens are stored at `$XDG_DATA_HOME/eve-esi-mcp/sso_token.json` with mode `0600`.

## Example model prompts

> *"Is PLEX currently a profitable Jita→Amarr haul? Assume 150k m3 on 5B collateral at 1000 isk/m3 and standard broker/tax."*

The model should: `resolve_ids(["PLEX"])` → `compare_hubs(type_id)` →
`freight_cost(...)` → `profit_after_fees(...)` and summarise.

> *"Find me manufacturing candidates: systems within 8 jumps of Jita with the lowest manufacturing cost index."*

→ `resolve_ids(["Jita"])` → `cheapest_system("manufacturing", 25, nearest_to=<Jita id>)`.

> *"I'm about to fly through low-sec — which pipe systems have the most ship kills in the last hour?"*

→ `hottest_systems("ship_kills", 20)` → `resolve_names([...])`.

## Development

```bash
.venv/bin/pytest
```

Tests stub httpx with `respx` — no live ESI calls in the test suite.

## License

MIT.
