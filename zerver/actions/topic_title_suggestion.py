"""Topic Title Improver: detect drift and suggest a better title.

Design notes (latency / cost / scalability):
- The endpoint is on-demand, never on the synchronous send path. The frontend
  calls it after a send completes (debounced per topic) or when the user
  opens the topic popover, so posting latency is unaffected.
- A cheap pre-filter (fewer than MIN_TOPIC_MESSAGES messages -> not drifted)
  avoids LLM calls for short topics, which are the majority of traffic.
- Prompts are truncated (last MAX_TOPIC_MESSAGES messages x 500 chars) to
  bound tokens; we reuse the shared TOPIC_SUMMARIZATION_MODEL wiring and
  per-user monthly AI-credit budget so cost is capped and metered.
"""

import orjson
from django.conf import settings
from django.utils.timezone import now as timezone_now
from openai import OpenAI

from analytics.lib.counts import COUNT_STATS, do_increment_logging_stat
from zerver.actions.message_summary import ai_stats_finish, ai_stats_start, make_message
from zerver.lib.message import messages_for_ids
from zerver.lib.narrow import (
    LARGER_THAN_MAX_MESSAGE_ID,
    AnchorInfo,
    NarrowParameter,
    clean_narrow_for_message_fetch,
    fetch_messages,
)
from zerver.models import UserProfile
from zerver.models.realms import MessageEditHistoryVisibilityPolicyEnum

# Last N messages in the topic are examined.
MAX_TOPIC_MESSAGES = 20
# Fewer messages than this -> sustained drift can't be judged; skip the LLM.
MIN_TOPIC_MESSAGES = 5
MAX_CHARS_PER_MESSAGE = 500


def get_topic_messages(
    user_profile: UserProfile, channel_id: int, topic_name: str, limit: int = MAX_TOPIC_MESSAGES
) -> list[dict[str, object]]:
    narrow = [
        NarrowParameter(operator="channel", operand=channel_id),
        NarrowParameter(operator="topic", operand=topic_name),
    ]
    narrow = clean_narrow_for_message_fetch(narrow, user_profile.realm, user_profile)
    query_info = fetch_messages(
        narrow=narrow,
        user_profile=user_profile,
        realm=user_profile.realm,
        is_web_public_query=False,
        anchor_info=AnchorInfo(type="message_id", value=LARGER_THAN_MAX_MESSAGE_ID),
        include_anchor=True,
        num_before=limit,
        num_after=0,
    )
    message_ids = [row[0] for row in query_info.rows]
    if not message_ids:
        return []
    message_list = messages_for_ids(
        message_ids=message_ids,
        user_message_flags={mid: [] for mid in message_ids},
        search_fields={},
        apply_markdown=False,
        client_gravatar=True,
        allow_empty_topic_name=False,
        message_edit_history_visibility_policy=MessageEditHistoryVisibilityPolicyEnum.none.value,
        user_profile=user_profile,
        realm=user_profile.realm,
    )
    message_list.sort(key=lambda m: m["id"])
    return message_list


def format_topic_for_model(
    message_list: list[dict[str, object]], current_title: str
) -> str:
    items = [
        {
            "sender": m.get("sender_full_name", ""),
            "content": str(m.get("content", ""))[:MAX_CHARS_PER_MESSAGE],
        }
        for m in message_list
    ]
    return orjson.dumps({"current_title": current_title, "messages": items}).decode()


def parse_suggestion_response(raw: str, current_title: str) -> dict[str, object]:
    """Parse the model's JSON; be liberal (code fences, extra text)."""
    text = raw.strip()
    if text.startswith("```"):
        # Strip ```json ... ``` fences.
        text = text.strip("`").strip()
        if text.startswith("json"):
            text = text[4:].strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    try:
        data = orjson.loads(text)
    except Exception:
        return {
            "drifted": False,
            "suggested_title": current_title,
            "reason": "Could not parse model response.",
            "confidence": 0.0,
        }
    suggested = str(data.get("suggested_title", "") or "").strip()[:60]
    drifted = bool(data.get("drifted", False))
    if not suggested or suggested.lower() == current_title.lower():
        drifted = False
        suggested = current_title
    return {
        "drifted": drifted,
        "suggested_title": suggested,
        "reason": str(data.get("reason", ""))[:500],
        "confidence": float(data.get("confidence", 0.0) or 0.0),
    }


def do_suggest_topic_title(
    user_profile: UserProfile, channel_id: int, topic_name: str
) -> dict[str, object] | None:
    model = settings.TOPIC_SUMMARIZATION_MODEL
    if model is None:  # nocoverage
        return None

    message_list = get_topic_messages(user_profile, channel_id, topic_name)
    if len(message_list) < MIN_TOPIC_MESSAGES:
        # Cheap pre-filter: not enough discussion to judge drift; no LLM cost.
        return {
            "drifted": False,
            "suggested_title": topic_name,
            "reason": "Not enough messages in the topic to detect drift.",
            "confidence": 0.0,
            "sample_message_id": message_list[-1]["id"] if message_list else None,
        }

    formatted = format_topic_for_model(message_list, topic_name)
    messages = [
        make_message(
            "You help keep Zulip topic titles accurate as conversations evolve.",
            "system",
        ),
        make_message(formatted),
        make_message(
            "The JSON above has the current topic title and the most recent "
            "messages in that topic (oldest first). Detect topic drift: sustained "
            "off-title discussion or a new sub-thread emerging. Reply with STRICT "
            "JSON only, no other text: "
            '{"drifted": true/false, "suggested_title": "<concise title under 60 chars>", '
            '"reason": "<one sentence>", "confidence": 0.0-1.0}. '
            "Set drifted=false and suggested_title to the current title if the "
            "title still fits.",
        ),
    ]

    ai_stats_start()
    client = OpenAI(
        api_key=settings.TOPIC_SUMMARIZATION_API_KEY,
        base_url=settings.TOPIC_SUMMARIZATION_API_BASE,
    )
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        **settings.TOPIC_SUMMARIZATION_PARAMETERS,
    )
    assert response.usage is not None
    credits_used = (
        response.usage.completion_tokens * settings.OUTPUT_COST_PER_GIGATOKEN
    ) + (response.usage.prompt_tokens * settings.INPUT_COST_PER_GIGATOKEN)
    ai_stats_finish()
    do_increment_logging_stat(
        user_profile, COUNT_STATS["ai_credit_usage::day"], None, timezone_now(), credits_used
    )

    raw = response.choices[0].message.content
    assert raw is not None
    result = parse_suggestion_response(raw, topic_name)
    result["sample_message_id"] = message_list[-1]["id"]
    return result
