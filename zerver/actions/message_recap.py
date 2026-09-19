"""Message Recap: summarize a user's unread messages with clickable references.

Reuses the existing topic-summarization LLM wiring (OpenAI-compatible client
pointing at Groq in dev, see zproject/dev_settings.py) so no new secrets or
dependencies are needed.
"""

import re
from typing import Any
from urllib.parse import quote

import orjson
from django.conf import settings
from django.utils.timezone import now as timezone_now
from openai import OpenAI

from analytics.lib.counts import COUNT_STATS, do_increment_logging_stat
from zerver.actions.message_summary import ai_stats_finish, ai_stats_start, make_message
from zerver.lib.markdown import markdown_convert
from zerver.lib.message import messages_for_ids
from zerver.models import UserMessage, UserProfile
from zerver.models.realms import MessageEditHistoryVisibilityPolicyEnum

# Cap unread messages per recap to bound latency, cost, and prompt size.
MAX_RECAP_MESSAGES = 50

# Matches single model citations like "[msg 123]" so we can link them.
# (Grouped forms like "[msg 123, 124]" are left as plain text.)
CITATION_RE = re.compile(r"\[msg\s+(\d+)\]")


def get_unread_recap_messages(
    user_profile: UserProfile, limit: int
) -> list[dict[str, Any]]:
    """Return up to `limit` most-recent unread message dicts for the user."""
    limit = max(1, min(limit, MAX_RECAP_MESSAGES))
    unread_ids = list(
        UserMessage.objects.filter(user_profile=user_profile)
        .extra(where=[UserMessage.where_unread()])  # noqa: S610
        .order_by("-message_id")
        .values_list("message_id", flat=True)[:limit]
    )
    if not unread_ids:
        return []
    # messages_for_ids does access checks; pass empty flags (ignored for content).
    message_list = messages_for_ids(
        message_ids=unread_ids,
        user_message_flags={mid: [] for mid in unread_ids},
        search_fields={},
        apply_markdown=False,
        client_gravatar=True,
        allow_empty_topic_name=False,
        message_edit_history_visibility_policy=MessageEditHistoryVisibilityPolicyEnum.none.value,
        user_profile=user_profile,
        realm=user_profile.realm,
    )
    # messages_for_ids may return in arbitrary order; show oldest first.
    message_list.sort(key=lambda m: m["id"])
    return message_list


def format_messages_for_recap(message_list: list[dict[str, Any]]) -> str:
    items = []
    for m in message_list:
        stream = m.get("stream_name") or m.get("display_recipient", "")
        if isinstance(stream, list):  # DMs collapse to recipient list
            stream = "DM"
        items.append(
            {
                "id": m["id"],
                "stream": stream,
                "topic": m.get("subject", ""),
                "sender": m.get("sender_full_name", ""),
                "content": m.get("content", "")[:2000],
            }
        )
    return orjson.dumps(items).decode()


def build_reference(message: dict[str, Any]) -> dict[str, Any]:
    """Build a clickable reference for one message.

    The authoritative narrow URL is built on the frontend with
    hash_util.by_conversation_and_time_url (which handles slug encoding).
    The backend provides a best-effort `near_url` fallback plus the IDs the
    frontend needs, so links keep working even if the model rewords text.
    """
    message_id = message["id"]
    stream_id = message.get("stream_id")
    topic = message.get("subject", "")
    # Quote path segments: raw spaces/& break Markdown link parsing and the
    # narrow URL itself. (The frontend rewrites these to authoritative,
    # hash_util-encoded URLs; this keeps direct API consumers working too.)
    if stream_id is not None and topic:
        near_url = (
            f"#narrow/channel/{stream_id}/topic/{quote(str(topic), safe='')}/near/{message_id}"
        )
    elif stream_id is not None:
        near_url = f"#narrow/channel/{stream_id}/near/{message_id}"
    else:
        near_url = f"#narrow/near/{message_id}"
    return {
        "message_id": message_id,
        "stream_id": stream_id,
        "stream_name": message.get("stream_name") or message.get("display_recipient", ""),
        "topic": topic,
        "sender": message.get("sender_full_name", ""),
        "near_url": near_url,
    }


def linkify_citations(
    summary_markdown: str, references: list[dict[str, Any]]
) -> str:
    """Rewrite validated `[msg <id>]` citations as markdown links.

    Only IDs present in `references` are linked; unknown IDs are left as-is
    so the model cannot inject arbitrary links.
    """

    by_id = {r["message_id"]: r for r in references}

    def _repl(match: re.Match[str]) -> str:
        mid = int(match.group(1))
        ref = by_id.get(mid)
        if ref is None:
            return match.group(0)
        return f"[msg {mid}]({ref['near_url']})"

    return CITATION_RE.sub(_repl, summary_markdown)


def do_generate_recap(
    user_profile: UserProfile, limit: int = 30
) -> dict[str, Any] | None:
    """Generate an LLM recap of unread messages. Returns None when no unreads."""
    model = settings.TOPIC_SUMMARIZATION_MODEL
    if model is None:  # nocoverage
        return None

    message_list = get_unread_recap_messages(user_profile, limit)
    if not message_list:
        return None

    references = [build_reference(m) for m in message_list]
    formatted = format_messages_for_recap(message_list)
    max_sentences = min(8, 4 + max(0, (len(message_list) - 10) // 10))
    messages = [
        make_message(
            "The following is a list of unread Zulip messages as JSON "
            "(fields: id, stream, topic, sender, content).",
            "system",
        ),
        make_message(formatted),
        make_message(
            f"Write a concise recap in up to {max_sentences} sentences for someone "
            "catching up. Group by stream/topic with one short paragraph per "
            "group, separated by blank lines; start each paragraph with the "
            "stream and topic in bold. Mention key conclusions and "
            "action items, and refer to people by name. After each key point, "
            "cite the message(s) it came from with the exact token `[msg <id>]` "
            "using the provided numeric ids. Only cite ids from the list. "
            "You can use Zulip CommonMark formatting.",
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

    summary = response.choices[0].message.content
    assert summary is not None
    summary = linkify_citations(summary, references)
    rendered = markdown_convert(
        summary, message_realm=user_profile.realm
    ).rendered_content
    return {
        "recap": rendered,
        "recap_markdown": summary,
        "message_ids": [r["message_id"] for r in references],
        "references": references,
    }
