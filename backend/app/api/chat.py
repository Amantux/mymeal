"""Conversational cooking-assistant endpoints."""
import json

from flask import Blueprint, request, jsonify, abort

from ..extensions import db
from ..models import ChatSession, ChatMessage
from ..auth import login_required, current_group
from ..schemas.serializers import (
    chat_session_out,
    chat_session_summary,
    chat_message_out,
)
from ..services.ai.base import ProviderError
from ..services.ai.registry import get_provider
from ..services.ai.agent import run_chat, actions_from_trace

bp = Blueprint("chat", __name__)


def _get_session(session_id) -> ChatSession:
    s = db.session.get(ChatSession, session_id)
    if not s or s.group_id != current_group().id:
        abort(404)
    return s


@bp.get("/ai/chat/sessions")
@login_required
def list_sessions():
    sessions = (
        db.session.query(ChatSession)
        .filter_by(group_id=current_group().id)
        .order_by(ChatSession.updated_at.desc())
        .all()
    )
    return jsonify({"items": [chat_session_summary(s) for s in sessions]})


@bp.get("/ai/chat/sessions/<session_id>")
@login_required
def get_session(session_id):
    return jsonify(chat_session_out(_get_session(session_id)))


@bp.delete("/ai/chat/sessions/<session_id>")
@login_required
def delete_session(session_id):
    db.session.delete(_get_session(session_id))
    db.session.commit()
    return "", 204


def _undo_edibl_stock(obj_id):
    """Reverse an `edibl_add_stock` chat action by deleting the lot in Edibl.
    A 404 means the lot is already gone, which we treat as successfully undone."""
    from ..services.edibl import EdiblClient
    res = EdiblClient.from_settings().delete_stock(str(obj_id))
    if res.get("ok") or res.get("status") == 404:
        return True, None
    return False, "Couldn't reach Edibl to undo — check the Edibl connection."


# Undo kinds the SERVER must reverse because the browser can't reach the target
# (a sibling app on the internal network). Client-reversible kinds like
# `shopping_item` are handled in the frontend and never hit this endpoint.
# Whitelisted — the client names a kind + id, never a method/path.
_SERVER_UNDO = {"edibl_stock": _undo_edibl_stock}


@bp.post("/ai/chat/undo")
@login_required
def undo_action():
    """Reverse a cross-app chat action the browser cannot reverse itself.
    Body: {kind, id}. Only whitelisted kinds are accepted."""
    data = request.get_json(force=True) or {}
    handler = _SERVER_UNDO.get(data.get("kind"))
    if not handler:
        return jsonify({"error": f"unknown undo kind {data.get('kind')}"}), 400
    ok, err = handler(data.get("id"))
    if ok:
        return jsonify({"undone": True})
    return jsonify({"undone": False, "error": err}), 502


def _next_position(session) -> int:
    return (max((m.position for m in session.messages), default=-1)) + 1


@bp.post("/ai/chat")
@login_required
def chat():
    """Send a message to the assistant. Creates a session if none is given."""
    data = request.get_json(force=True) or {}
    message = str(data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 422

    try:
        provider = get_provider()
    except ProviderError as exc:
        return jsonify({"error": str(exc)}), 503

    gid = current_group().id
    session_id = data.get("sessionId")
    if session_id:
        session = _get_session(session_id)
    else:
        session = ChatSession(title=message[:60] or "New chat", group_id=gid)
        db.session.add(session)
        db.session.flush()

    history = [{"role": m.role, "content": m.content} for m in session.messages]

    try:
        result = run_chat(gid, provider, history, message)
    except ProviderError as exc:
        # Discard the flushed-but-uncommitted session and any tool writes so a
        # failed turn leaves no phantom session or partial shopping-list item.
        db.session.rollback()
        return jsonify({"error": str(exc)}), 502

    pos = _next_position(session)
    user_msg = ChatMessage(
        role="user", content=message, position=pos, session_id=session.id
    )
    assistant_msg = ChatMessage(
        role="assistant",
        content=result["reply"],
        tool_trace=json.dumps(result["trace"]),
        position=pos + 1,
        session_id=session.id,
    )
    db.session.add_all([user_msg, assistant_msg])
    db.session.commit()

    return jsonify(
        {
            "sessionId": session.id,
            "reply": result["reply"],
            "trace": result["trace"],
            "actions": actions_from_trace(result["trace"]),
            "message": chat_message_out(assistant_msg),
        }
    )
