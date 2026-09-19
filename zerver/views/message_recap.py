from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext as _
from pydantic import Json

from analytics.lib.counts import COUNT_STATS
from zerver.actions.message_recap import MAX_RECAP_MESSAGES, do_generate_recap
from zerver.lib.exceptions import JsonableError
from zerver.lib.response import json_success
from zerver.lib.typed_endpoint import typed_endpoint
from zerver.models import UserProfile

from django.conf import settings


@typed_endpoint
def get_messages_recap(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    limit: Json[int] = 30,
) -> HttpResponse:
    if settings.TOPIC_SUMMARIZATION_MODEL is None:  # nocoverage
        raise JsonableError(_("AI features are not enabled on this server."))

    if not user_profile.can_summarize_topics():
        raise JsonableError(_("Insufficient permission"))

    limit = max(1, min(limit, MAX_RECAP_MESSAGES))

    if settings.MAX_PER_USER_MONTHLY_AI_COST is not None:
        used_credits = COUNT_STATS["ai_credit_usage::day"].current_month_accumulated_count_for_user(
            user_profile
        )
        if used_credits >= settings.MAX_PER_USER_MONTHLY_AI_COST * 1000000000:
            raise JsonableError(_("Reached monthly limit for AI credits."))

    result = do_generate_recap(user_profile, limit)
    if result is None:  # nocoverage
        raise JsonableError(_("No unread messages to recap"))

    return json_success(request, data=result)
