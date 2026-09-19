# Implementation notes (HW1)

Demo video (both features): https://www.loom.com/share/04fc74ba13314a52bbad285deaa7fbca

## Feature 1: Message Recap

One click summarizes all unread messages on the page, with clickable
references that jump to the original messages.

**Backend.** `GET /json/messages/recap` (`zerver/views/message_recap.py`,
wired in `zproject/urls.py` next to `messages/summary`). I reused the
existing topic-summarization LLM setup instead of adding new plumbing: same
`can_summarize_topics()` permission check, same monthly AI-credit budget
check, same OpenAI-compatible client pointed at Groq
(`zproject/dev_settings.py`, secret `topic_summarization_api_key` from the
gitignored `zproject/dev-secrets.conf`). `do_generate_recap()` in
`zerver/actions/message_recap.py` fetches up to `MAX_RECAP_MESSAGES=50` of
the most recent unread IDs through `UserMessage.where_unread()`, hydrates
them with `messages_for_ids()` oldest-first, prompts the model with JSON
rows of `{id, stream, topic, sender, content}`, and tells it to cite each
key point as `[msg <id>]`. It logs AI time and credits with the shared
`ai_stats_*` and `ai_credit_usage` helpers, then renders CommonMark with
`markdown_convert`.

**How links are created.** The prompt requires machine-checkable
`[msg <id>]` citations rather than free-form quotes.
`linkify_citations()` drops any ID outside the fetched set and rewrites the
rest to `[msg <id>](<near_url>)`, so the model cannot inject its own links.
Each reference carries `message_id, stream_id, topic, sender` plus a
best-effort backend `near_url` shaped like
`#narrow/channel/<id>/topic/<topic>/near/<msg_id>` (the same shape as
Zulip's own `message_url`, which I confirmed in API responses). The backend
percent-encodes the topic segment. On render, the frontend swaps each
citation href for the authoritative URL from
`hash_util.by_conversation_and_time_url()`, which gets slug encoding right
for topics with spaces. The modal also lists every reference
(`web/templates/message_recap.hbs`), so each cited message is one click
away even if the model words something oddly.

**Frontend.** `web/src/message_recap.ts:show_unread_recap()` opens a
`dialog_widget` modal, GETs the endpoint, and renders the recap plus the
reference list. Entry point is the **Recap unread** button in the inbox
header (`web/templates/inbox_view/inbox_view.hbs`, handler in
`web/src/inbox_ui.ts`).

**Tests.** `zerver/tests/test_message_recap.py` (mocked
`Completions.create`): an unread recap returns
`recap/message_ids/references` with working `/near/<id>` links, empty
unreads return a clean error, unknown citation IDs stay unlinked, and
topics with spaces get quoted URLs.

## Feature 2: Topic Title Improver

This detects when a topic drifts (sustained off-title discussion or a new
sub-thread) and suggests a better title while the user still has context.

**Backend.** `POST /json/topics/suggest-title`
(`zerver/views/topic_title_suggestion.py`, wired in `zproject/urls.py`).
`do_suggest_topic_title()` in
`zerver/actions/topic_title_suggestion.py` pulls the last
`MAX_TOPIC_MESSAGES=20` messages with the standard narrow fetch
(`channel` plus `topic` narrow through `fetch_messages` and
`messages_for_ids`) and asks the model for strict JSON of the form
`{drifted, suggested_title, reason, confidence}`. Parsing tolerates code
fences and extra text, and falls back to "not drifted" when the response is
unusable. The response includes `sample_message_id` so Apply can rename
without another lookup.

**Latency, cost, scalability.** Latency: the endpoint never sits on the send
path. The frontend calls it after a send finishes, so posting speed is
unchanged and the modal arrives async. Cost: topics under
`MIN_TOPIC_MESSAGES=5` return `drifted=false` with no LLM call at all, and
those are the majority of topics. Prompts are capped at 20 messages of 500
chars, and spend runs through the existing per-user monthly AI-credit
budget, same as summaries. Scale: the endpoint is stateless with no new
tables or migrations. The frontend dedupes checks to one per topic per
session plus a 5 minute debounce, so a burst of sends costs one call. For
real production scale I would move this to an async queue worker with an
embedding pre-filter and a cached summary invalidated on edit, move, or
send (there is already a TODO to that effect in `message_summary.py`).

**Frontend.** `web/src/topic_title_suggestion.ts`, triggered two ways: a
manual **Improve title** item in the topic popover (same AI-gated section
as Summarize; `left_sidebar_topic_actions_popover.hbs`, handler in
`topic_popover.ts`), and automatically in `compose.send_message_success()`
(`web/src/compose.ts`), which stays silent unless drift is found. The modal
shows the suggestion plus the reason, with Apply (renames through
`PATCH /json/messages/<id>` with `change_all`, copied from the resolve-topic
flow in `message_edit.ts`) and Dismiss.

**Tests.** `zerver/tests/test_topic_title_suggestion.py`: a drifted topic
returns the mocked suggestion; a short topic never calls the LLM
(`assert_not_called`).

**Risks and reflection (both features).** Hallucinations: citations are
limited to real fetched IDs and both modals carry an "AI may be wrong"
footer. Rename races: Apply renames through one representative message with
`change_all`, so two competing suggestions resolve to last-writer-wins
through the event system. Permissions and credit limits are inherited from
the summaries feature rather than reinvented. Prompt injection through
message content is partly contained by instructing the model to use only
the provided data and by allowlisting links to fetched IDs.
