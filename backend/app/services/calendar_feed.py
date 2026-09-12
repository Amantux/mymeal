"""Build a household's meal plan as a subscribable iCalendar feed.

Consumed by `api/calendar.py`, which owns the HTTP surface and the token
lookup; everything here takes an explicit ``gid`` and never touches request
context, matching the rest of services/.
"""
from __future__ import annotations

import secrets
from datetime import date, timedelta

from sqlalchemy.orm import joinedload

from ..extensions import db
from ..models import MealPlanEntry
from .ics import all_day_event, calendar

# How far back the feed reaches. This is a FEED, not an archive: a calendar
# client re-downloads the whole document on every poll, so every event we keep
# is paid for on every sync, forever. Four weeks is enough to answer "what did
# we eat recently?" and to cover a client that has been offline for a while,
# and it keeps a household's document small indefinitely. Past entries are not
# deleted from myMeal — they are simply not published.
PAST_DAYS = 28

# Hard ceiling on events in one document. Nothing forward-bounds the plan (a
# user may plan months ahead, and cutting that off would be the wrong default),
# so this is the backstop that keeps an unauthenticated, timer-polled endpoint
# from ever serving an unbounded response.
MAX_EVENTS = 2000

# UID domain. Deliberately a FIXED literal rather than the request host: the
# entire point of a stable UID is that a re-sync UPDATES an event instead of
# duplicating it, and the same add-on is routinely reached at several hosts
# (LAN IP, .local name, reverse proxy). Deriving the UID from the host would
# hand the same meal a different identity per route and duplicate the whole
# calendar the first time a user changed how they reach the app. Entry ids are
# uuid4, so they are already globally unique; the domain is just RFC decoration.
UID_DOMAIN = "mymeal"


def new_token() -> str:
    """Mint a feed token. 32 bytes = 256 bits, same as Recipe.share_token —
    this is a bearer capability sitting in a URL, so entropy is the only thing
    standing between a stranger and a household's meal plan."""
    return secrets.token_urlsafe(32)


def feed_start(today: date | None = None) -> date:
    return (today or date.today()) - timedelta(days=PAST_DAYS)


def entries_for_feed(gid: str, today: date | None = None) -> list[MealPlanEntry]:
    """The published slice of one group's plan.

    Tenant-scoped on ``group_id`` here and nowhere else — this is the only
    query behind an unauthenticated endpoint, so the filter is not optional and
    is covered by a cross-group test.
    """
    return (
        db.session.query(MealPlanEntry)
        .filter(MealPlanEntry.group_id == gid)
        .filter(MealPlanEntry.date >= feed_start(today))
        # joinedload, not selectinload: recipe is to-one and we touch only
        # name/slug, so one join beats a second round trip per page of entries.
        .options(joinedload(MealPlanEntry.recipe))
        # Deterministic: id breaks the tie so two meals on one day keep a
        # stable order between polls rather than shuffling in the client.
        .order_by(MealPlanEntry.date.asc(), MealPlanEntry.id.asc())
        .limit(MAX_EVENTS)
        .all()
    )


def _meal_label(entry: MealPlanEntry) -> str:
    # meal_type is a free-form String(32), so title-case whatever is there
    # rather than mapping a fixed set and dropping anything unrecognised.
    return (entry.meal_type or "").strip().replace("_", " ").title()


def summary_for(entry: MealPlanEntry) -> str:
    """SUMMARY: what shows in the one line a calendar month view gives you.

    Meal slot first because that is what a human scans for when a day holds
    three entries; without it, breakfast and dinner are indistinguishable.
    """
    name = (entry.recipe.name if entry.recipe else "") or entry.title or "Meal"
    label = _meal_label(entry)
    return f"{label}: {name}" if label else name


def description_for(entry: MealPlanEntry) -> str:
    """DESCRIPTION: the detail that did not fit in SUMMARY.

    No link to the recipe, deliberately. myMeal has no configured external base
    URL — behind HA ingress the app is served from a random, session-scoped
    `/api/hassio_ingress/<token>/` path that the BACKEND genuinely cannot know,
    and the direct host:port a subscriber used to fetch this feed is not
    necessarily reachable from wherever they open the calendar entry. Emitting
    a guessed URL would put a dead link in every event, which is worse than no
    link. The recipe's slug is included instead: it is stable, human-readable,
    and enough to find the recipe in the app. If a real base-URL setting is
    ever added, this is the single place that changes.
    """
    parts: list[str] = []
    if entry.servings:
        parts.append(f"Serves {entry.servings}")
    if entry.notes:
        parts.append(entry.notes.strip())
    if entry.recipe and entry.recipe.slug:
        parts.append(f"Recipe: {entry.recipe.slug}")
    return "\n".join(parts)


def build_feed(gid: str, name: str = "Meal plan",
               today: date | None = None) -> str:
    """The whole ICS document for one group. Empty plan -> a valid empty
    VCALENDAR, never a 404: subscribers poll on a timer and an error would show
    the user a broken calendar rather than an empty one."""
    events = [
        all_day_event(
            uid=f"{entry.id}@{UID_DOMAIN}",
            day=entry.date,
            summary=summary_for(entry),
            description=description_for(entry),
        )
        for entry in entries_for_feed(gid, today)
    ]
    return calendar(events, name=name)
