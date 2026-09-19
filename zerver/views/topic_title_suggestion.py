from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext as _
from pydantic import Json

from django.conf import settings

from analytics.lib.counts import COUNT_STATS
from zerver.actions.topic_title_suggestion import do_suggest_topic_title
from zerver.lib.exceptions import JsonableError
from zerver.lib.response import json_success
from zerver.lib.typed_endpoint import typed_endpoint
from zerver.models import UserProfile


@typed_endpoint
def suggest_topic_title(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    channel_id: Json[int],
    topic_name: str,
) -> HttpResponse:
    if settings.TOPIC_SUMMARIZATION_MODEL is None:  # nocoverage
        raise JsonableError(_("AI features are not enabled on this server."))

    if not user_profile.can_summarize_topics():
        raise JsonableError(_("Insufficient permission"))

    if settings.MAX_PER_USER_MONTHLY_AI_COST is not None:
        used_credits = COUNT_STATS["ai_credit_usage::day"].current_month_accumulated_count_for_user(
            user_profile
        )
        if used_credits >= settings.MAX_PER_USER_MONTHLY_AI_COST * 1000000000:
            raise JsonableError(_("Reached monthly limit for AI credits."))

    result = do_suggest_topic_title(user_profile, channel_id, topic_name)
    if result is None:  # nocoverage
        raise JsonableError(_("Could not analyze the topic"))

    return json_success(request, data=result)
