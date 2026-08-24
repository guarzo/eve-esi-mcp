# eve-esi-mcp

A local [Model Context Protocol](https://modelcontextprotocol.io) server that gives
an LLM client (Claude Code, Claude Desktop, any MCP-compatible app) structured
access to the **EVE Online ESI API** — market data, industry cost indices,
universe lookups, system activity, wormhole/J-space statics, and (optionally)
your own character's wallet/assets/orders via EVE SSO.

The goal is turning questions like:

- *"What's the Jita→Amarr spread on Tritanium right now, and is it worth hauling after fees?"*
- *"Which system has the cheapest manufacturing cost index this week, within 10 jumps of Jita?"*
- *"Which low-sec systems are hottest by ship kills right now — avoid this route."*
- *"What's the static chain for a C5 wolf-rayet wormhole?"*

…into tool calls the model can chain together, rather than copy-paste screenshots.

## ESI compliance

This server follows CCP's [ESI best practices](https://developers.eveonline.com/docs/services/esi/best-practices/):

- **User-Agent** built from `EVE_ESI_MCP_CONTACT` (required — the server refuses to start without it).
- **Compatibility date** — every request sends `X-Compatibility-Date` (default `2026-08-18`).
  ESI versions by date rather than by URL path; without the header you are silently served
  the `2020-01-01` view of the API. Override with `EVE_ESI_MCP_COMPATIBILITY_DATE`, and
  check [`/meta/changelog`](https://esi.evetech.net/meta/changelog) before bumping it —
  response shapes change between dates.
- **HTTP caching** via `hishel` on disk, honouring `Expires` and `ETag` responses; repeated calls within the cache window never hit CCP.
- **Authenticated responses are never cached** — hishel keys on method + URL + body only,
  so a cached `/characters/{id}/wallet/` body would be replayed to any later caller of that
  URL. Token-bearing requests use a separate uncached client, and the cache and data
  directories are created `0700`.
- **Error-limit awareness** — reads `X-ESI-Error-Limit-Remain`/`Reset` after every response and sleeps when low; 420s back off with exponential jitter.
- **Pagination** — `X-Pages` walked with bounded concurrency and jitter.
- **Read-only** — no write endpoints wired up.

## Response size

Several ESI endpoints return the entire universe in one response — `/markets/prices/` is
~15,800 rows and `/industry/systems/` ~5,500, each far larger than a model's context
window. Tools that touch those endpoints return an envelope rather than a bare list:

```json
{ "items": [...], "returned": 200, "total": 15817, "truncated": true, "note": "Showing 200 of 15817 rows..." }
```

Truncation is always visible. A silently shortened list is worse than a large one, because
the model treats a partial answer as a complete one and reasons confidently from it. Raise
`limit` (up to 5000) or narrow the query to see more.

Prefer the narrow tool over the broad one: `best_bid_ask` over raw `market_orders`,
`cheapest_system` over `industry_systems`, `hottest_systems` over `system_kills`. Always
pass `type_id` to `market_orders` — an unfiltered region scan is ~414 pages in The Forge
and returns only the first by default.

## Globally-traded items

PLEX (`type_id` 44992) trades on a single cross-cluster market, region `19000001`, not
per-region. Asking for it in The Forge returns an empty order book and a stale history
stub — which reads as "illiquid here" rather than "wrong region". The market tools detect
these types, query the global market instead, and report the redirect in the response.

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

- `market_orders(region, type_id?, order_type, location_id?, page_limit?, limit?)`
- `market_history(region, type_id)` — 400 days of daily OHLC-ish
- `market_prices(type_ids?, limit?)` — global average + adjusted price; pass `type_ids` to price specific items exactly
- `best_bid_ask(region, type_id)` — top bid/ask, spread, 7d liquidity
- `top_traded(region, type_ids[], days, limit)` — rank a watchlist by ISK turnover

### Arbitrage

- `compare_hubs(type_id, hubs?)` — Jita/Amarr/Dodixie/Rens/Hek side-by-side
- `find_spreads(src_hub, dst_hub, type_ids[], min_margin_pct, min_daily_isk)`
- `freight_cost(m3, collateral, rate_per_m3, collateral_pct, minimum_fee)`
- `profit_after_fees(buy, sell, qty, sales_tax_pct, broker_fee_pct_buy, broker_fee_pct_sell, freight_isk)`

### Industry

- `industry_systems(activity?, limit?)` — cost indices per system
- `cheapest_system(activity, limit, nearest_to?)` — rank + optional jump distance
- `build_cost_estimate(product_type_id, materials[], system_id, runs, me_pct, job_tax_pct, facility_bonus_pct)` — EIV-style

### Universe

- `resolve_ids(names[])` / `resolve_names(ids[])`
- `region_info`, `constellation_info`, `system_info`, `type_info`, `list_regions`
- `route(origin, destination, flag, avoid?)` / `jumps_between(origin, destination)`

### Activity

- `system_kills(limit)` / `system_jumps(limit)` — last-hour heat map
- `hottest_systems(kind, limit)`
- `sovereignty_systems(limit)` / `sovereignty_campaigns()`

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
- `sso_status()` — every logged-in character, with scopes and token expiry
- `list_characters()` — ids and names accepted by the `character` argument below
- `sso_logout(character_id?)` — one character, or all of them when omitted
- `my_wallet(character?)`, `my_wallet_journal(character?, limit?, complete?)`,
  `my_wallet_transactions(character?, limit?, complete?)`,
  `my_assets(character?, limit?, complete?)`, `my_open_orders(character?, limit?, complete?)`,
  `my_skills(character?)`, `my_industry_jobs(character?, include_completed?, limit?, complete?)`
- `my_blueprints(character?, limit?, complete?)` — blueprints the character
  owns. Needs `esi-characters.read_blueprints.v1`. Answers what `my_assets`
  cannot: there a blueprint is only a type_id and a quantity, so ten rows of
  one formula could be ten originals or a single ten-run copy stack. Here
  `runs = -1` is an **original** and `runs > 0` is a **copy** with that many
  runs left, which is the difference between "I can install this again" and
  "I cannot". Carries `material_efficiency` / `time_efficiency` too, for
  costing the blueprint that will actually be installed. A blueprint appears
  in both this and `my_assets`, so callers summing stock must not add them.
- `my_corp_assets(character?, limit?, complete?)` — assets owned by the
  character's **corporation**. Needs `esi-assets.read_corporation_assets.v1`
  *and* the in-game **Director** role. The envelope carries `corporation_id`
  alongside `character_id`. Disjoint from `my_assets` — corp-owned items never
  appear in a character's own asset list, so the two can be summed without
  double-counting.

  The two requirements fail differently and the tool separates them: a missing
  **scope** returns a structured `missing_scope` error before any request, so
  it is never confused with a missing **role**, which arrives from ESI as a
  403 and cannot be predicted (roles are not exposed anywhere).

> **Existing logins need to re-authorize.** A stored token does not gain new
> scopes when it refreshes — the saved scope string is preserved — so any
> character logged in before `esi-assets.read_corporation_assets.v1` was added
> to the default set will never acquire it on its own. Run `sso_login` (or
> `sso_login_start` / `sso_login_finish`) again per character. `sso_status()`
> lists each character's granted scopes if you want to check first.

### Multiple characters

Logging in a second character *adds* it rather than replacing the first. Pass
`character=` (id or name, case-insensitive) to choose. With exactly one character
logged in it can be omitted; with several, omitting it returns a structured
`ambiguous_character` error listing the choices rather than silently picking one:

```json
{ "error": "ambiguous_character", "available_characters": [
    {"character_id": 1001, "character_name": "Alice"},
    {"character_id": 1002, "character_name": "Bob"}] }
```

Refresh tokens live in `$XDG_DATA_HOME/eve-esi-mcp/sso_tokens.json`, mode `0600`, in a
`0700` directory. An older single-character `sso_token.json` is migrated automatically on
first use.

### `complete=` and ESI's retention limits

The row caps above are tuned for a model reading an answer. A program *ingesting* these
tools (a P&L tracker, say) needs completeness instead — 200 of 4,000 transactions silently
becomes a wrong average cost. Pass `complete=True` for a guarantee of `truncated: false`
or a hard error; it never returns a quiet partial.

Completeness only reaches as far as ESI retains, which is less than people expect:

| Endpoint | Retention |
|---|---|
| `my_wallet_journal` | **30 days** |
| `my_industry_jobs(include_completed=True)` | **90 days** |
| `my_wallet_transactions` | cursor-walked back as far as ESI serves |

Anything longer has to be polled on a schedule and stored locally — it cannot be
backfilled later. `my_wallet_transactions` also paginates on a `from_id` cursor rather
than `X-Pages`, so a naive client silently reads the first page forever.

## Example model prompts

> *"Is Tritanium currently a profitable Jita→Amarr haul? Assume 150k m3 on 5B collateral at 1000 isk/m3 and standard broker/tax."*

The model should: `resolve_ids(["Tritanium"])` → `compare_hubs(type_id)` →
`freight_cost(...)` → `profit_after_fees(...)` and summarise.

(Asking the same question about PLEX returns a single `global` row and an explicit
note: PLEX has one cluster-wide order book, so there is no inter-hub spread to haul.)

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
