"""Public iCalendar feed for the household meal plan, and its token management.

The feed route is UNAUTHENTICATED by design, for the same reason
``api/public.py`` is: calendar clients — Home Assistant's Remote Calendar, iOS,
Google, Thunderbird — cannot send an Authorization header. They GET a URL on a
timer. So the capability lives in the URL, exactly as public recipe sharing
already does, and the token IS the credential.

Auth in this app is opt-IN (there is no global auth before_request; views apply
``@login_required`` themselves), so the feed route simply omits the decorator.
That is the whole mechanism — nothing is being bypassed.
"""
from flask import Blueprint, Response, abort, jsonify

from ..auth import current_group, login_required
from ..extensions import db, limiter
from ..models import Group
from ..services.calendar_feed import build_feed, new_token

bp = Blueprint("calendar", __name__)


@bp.get("/calendar/<token>.ics")
# The only unauthenticated data-serving route with a write-free but scrapable
# surface. Token entropy (256 bits) makes guessing infeasible, so this is not a
# brute-force bound — it caps abuse and accidental hammering by a misconfigured
# client. Generous on purpose: a household may have several devices polling,
# and a subscriber that starts getting 429s silently stops updating, which
# looks like "the calendar is broken" rather than "you are rate limited".
# Behind HA ingress every request shares the 172.30.32.2 peer, but this feed is
# meant to be fetched directly rather than through ingress, so the key is
# usually the real client.
@limiter.limit("120 per hour")
def calendar_feed(token):
    # Guard the empty token explicitly, like public.py: a group that has never
    # published has calendar_token NULL, and "" must never resolve to it.
    if not token:
        abort(404)
    group = db.session.query(Group).filter_by(calendar_token=token).first()
    if not group:
        # 404, never 401/403. A distinct status would confirm that the token
        # namespace exists and turn this into an oracle; an unknown token and
        # an unpublished feed must be indistinguishable.
        abort(404)

    body = build_feed(group.id, name=f"{group.name} meal plan")
    resp = Response(body, mimetype="text/calendar")
    resp.headers["Content-Type"] = "text/calendar; charset=utf-8"
    # `inline` (not attachment): subscribing clients fetch this, they do not
    # download it. The filename is only a hint for a human who opens it.
    resp.headers["Content-Disposition"] = 'inline; filename="mealplan.ics"'
    # The plan changes whenever anyone edits it; a cached feed shows stale
    # meals, which is the one thing that makes a subscription useless.
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


# --- token management (authenticated) --------------------------------------
# Any household member may publish or rotate. That matches recipe sharing, and
# every member can already read the whole plan, so gating this on an owner role
# would protect nothing while blocking the person who actually wants the feed.


def _out(group: Group):
    return jsonify({"token": group.calendar_token})


@bp.get("/calendar/subscription")
@login_required
def get_subscription():
    return _out(current_group())


@bp.post("/calendar/subscription")
@login_required
def create_subscription():
    """Publish the feed. Idempotent — returns the existing token if there is
    one, mirroring POST /recipes/<id>/share. Rotation is a separate call, so
    that re-opening the dialog can never silently break live subscriptions."""
    group = current_group()
    if not group.calendar_token:
        group.calendar_token = new_token()
        db.session.commit()
    return _out(group)


@bp.post("/calendar/subscription/rotate")
@login_required
def rotate_subscription():
    """Issue a fresh token and invalidate the old one in a single step.

    A separate endpoint rather than DELETE-then-POST: the two-call version
    leaves the household with no feed if the second call fails, and this is the
    action a user reaches for precisely when they believe the old URL has
    leaked.
    """
    group = current_group()
    group.calendar_token = new_token()
    db.session.commit()
    return _out(group)


@bp.delete("/calendar/subscription")
@login_required
def delete_subscription():
    """Unpublish entirely — the old URL stops resolving immediately."""
    group = current_group()
    group.calendar_token = None
    db.session.commit()
    return "", 204
