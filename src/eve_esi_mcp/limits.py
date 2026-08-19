"""Row caps for tools that would otherwise return a whole ESI dataset.

Several ESI endpoints return the entire universe in one response — /markets/prices/
is ~1.1 MB and /industry/systems/ ~1.9 MB, each far larger than a model's context
window. Forwarding those verbatim doesn't just waste tokens, it ends the session.

The rule here is that truncation is always *visible*. A silently shortened list is
worse than a large one, because the model treats a partial answer as a complete one
and reasons confidently from it.
"""

from __future__ import annotations

from typing import Any

# Enough rows to answer a real question, small enough to stay affordable.
DEFAULT_ROW_LIMIT = 200

# Callers may raise the limit, but not past this — the point is that no argument
# the model chooses can put the session back in danger.
MAX_ROW_LIMIT = 5000


def tool_limit(limit: int | None) -> int:
    """Coerce a model-supplied limit for a tool that must never be unbounded.

    `None` here means "caller didn't choose", not "give me everything" — the
    unbounded path is reserved for internal helpers that aren't exposed as tools.
    """
    if limit is None:
        return DEFAULT_ROW_LIMIT
    return min(max(limit, 1), MAX_ROW_LIMIT)


def capped(
    rows: list[Any],
    limit: int | None = DEFAULT_ROW_LIMIT,
    *,
    extra: dict[str, Any] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Wrap `rows` in an envelope that states what was withheld.

    `limit=None` disables capping, for callers that genuinely need every row
    (an internal lookup table, not a tool response).
    """
    total = len(rows)
    if limit is None:
        items = rows
    else:
        items = rows[: min(max(limit, 1), MAX_ROW_LIMIT)]

    truncated = len(items) < total
    out: dict[str, Any] = {
        "items": items,
        "returned": len(items),
        "total": total,
        "truncated": truncated,
    }
    if truncated:
        out["note"] = (
            f"Showing {len(items)} of {total} rows. This is a partial dataset — do not "
            f"treat it as complete. Narrow the query (by type, region, or activity) or "
            f"raise `limit` up to {MAX_ROW_LIMIT} to see more."
        )
    if note:
        out["note"] = f"{out['note']} {note}" if "note" in out else note
    if extra:
        out.update(extra)
    return out
