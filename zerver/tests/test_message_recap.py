from unittest import mock

from openai.resources.chat.completions import Completions
from openai.types.chat import ChatCompletion

from zerver.lib.test_classes import ZulipTestCase


def make_fake_completion(content: str) -> ChatCompletion:
    return ChatCompletion.model_validate(
        {
            "id": "chatcmpl-test-recap",
            "created": 1738495155,
            "model": "groq/llama-3.3-70b-versatile",
            "object": "chat.completion",
            "choices": [
                {
                    "finish_reason": "stop",
                    "index": 0,
                    "message": {"content": content, "role": "assistant"},
                }
            ],
            "usage": {
                "completion_tokens": 20,
                "prompt_tokens": 100,
                "total_tokens": 120,
            },
        }
    )


class MessagesRecapTestCase(ZulipTestCase):
    def test_recap_unread_messages(self) -> None:
        sender = self.example_user("iago")
        user = self.example_user("hamlet")
        channel_name = "Zulip features"
        topic_name = "New feature launch"
        self.subscribe(sender, channel_name)
        self.subscribe(user, channel_name)
        self.send_stream_message(
            sender, channel_name, content="Deploy is at 3pm today.", topic_name=topic_name
        )
        self.send_stream_message(
            sender,
            channel_name,
            content="Please review the release notes.",
            topic_name=topic_name,
        )
        self.login_user(user)

        fake_response = make_fake_completion("Deploy at 3pm. Review notes.")
        with (
            self.settings(
                TOPIC_SUMMARIZATION_MODEL="llama-3.3-70b-versatile",
                TOPIC_SUMMARIZATION_API_KEY="test",
            ),
            mock.patch.object(Completions, "create", return_value=fake_response),
        ):
            result = self.client_get("/json/messages/recap", dict(limit=10))
        response = self.assert_json_success(result)
        self.assertIn("recap", response)
        self.assertIn("message_ids", response)
        self.assertIn("references", response)
        self.assertEqual(len(response["message_ids"]), 2)
        self.assertEqual(len(response["references"]), 2)
        for ref in response["references"]:
            self.assertIn("message_id", ref)
            self.assertIn("near_url", ref)
            self.assertIn(f"/near/{ref['message_id']}", ref["near_url"])

    def test_recap_no_unread_messages(self) -> None:
        user = self.example_user("hamlet")
        self.login_user(user)
        with (
            self.settings(
                TOPIC_SUMMARIZATION_MODEL="llama-3.3-70b-versatile",
                TOPIC_SUMMARIZATION_API_KEY="test",
            ),
        ):
            result = self.client_get("/json/messages/recap")
        self.assert_json_error(result, "No unread messages to recap")

    def test_recap_citations_linkified(self) -> None:
        from zerver.actions.message_recap import linkify_citations

        refs = [
            {
                "message_id": 42,
                "stream_id": 7,
                "stream_name": "general",
                "topic": "standup",
                "sender": "Hamlet",
                "near_url": "#narrow/channel/7/topic/standup/near/42",
            }
        ]
        linked = linkify_citations("Standup moved [msg 42] today.", refs)
        self.assertIn("[msg 42](#narrow/channel/7/topic/standup/near/42)", linked)
        # Unknown IDs are left untouched so the model can't inject links.
        untouched = linkify_citations("See [msg 999].", refs)
        self.assertIn("[msg 999]", untouched)

    def test_reference_url_quotes_special_chars(self) -> None:
        from zerver.actions.message_recap import build_reference

        ref = build_reference(
            {
                "id": 7,
                "stream_id": 3,
                "subject": "Outdoor Activity & Gaming Plans",
                "sender_full_name": "Iago",
            }
        )
        self.assertNotIn(" ", ref["near_url"])
        self.assertNotIn("&", ref["near_url"])
        self.assertIn("/near/7", ref["near_url"])
