from unittest import mock

import orjson

from openai.resources.chat.completions import Completions

from zerver.lib.test_classes import ZulipTestCase
from zerver.tests.test_message_recap import make_fake_completion


class TopicTitleSuggestionTestCase(ZulipTestCase):
    def test_suggest_title_detects_drift(self) -> None:
        sender = self.example_user("iago")
        user = self.example_user("hamlet")
        channel_name = "Zulip features"
        topic_name = "Standup meeting"
        self.subscribe(sender, channel_name)
        self.subscribe(user, channel_name)
        for i in range(6):
            self.send_stream_message(
                sender,
                channel_name,
                content=f"We should switch to deploy discussion part {i}.",
                topic_name=topic_name,
            )
        self.login_user(user)
        stream_id = self.get_stream_id(channel_name)

        payload = {
            "drifted": True,
            "suggested_title": "Deploy discussion",
            "reason": "Conversation moved to deploys.",
            "confidence": 0.9,
        }
        fake_response = make_fake_completion(orjson.dumps(payload).decode())
        with (
            self.settings(
                TOPIC_SUMMARIZATION_MODEL="llama-3.3-70b-versatile",
                TOPIC_SUMMARIZATION_API_KEY="test",
            ),
            mock.patch.object(Completions, "create", return_value=fake_response),
        ):
            result = self.client_post(
                "/json/topics/suggest-title",
                dict(channel_id=stream_id, topic_name=topic_name),
            )
        response = self.assert_json_success(result)
        self.assertTrue(response["drifted"])
        self.assertEqual(response["suggested_title"], "Deploy discussion")
        self.assertIn("sample_message_id", response)

    def test_short_topic_skips_llm(self) -> None:
        sender = self.example_user("iago")
        user = self.example_user("hamlet")
        channel_name = "Zulip features"
        topic_name = "Quick question"
        self.subscribe(sender, channel_name)
        self.subscribe(user, channel_name)
        self.send_stream_message(
            sender, channel_name, content="One question.", topic_name=topic_name
        )
        self.login_user(user)
        stream_id = self.get_stream_id(channel_name)

        with (
            self.settings(
                TOPIC_SUMMARIZATION_MODEL="llama-3.3-70b-versatile",
                TOPIC_SUMMARIZATION_API_KEY="test",
            ),
            mock.patch.object(Completions, "create") as mock_create,
        ):
            result = self.client_post(
                "/json/topics/suggest-title",
                dict(channel_id=stream_id, topic_name=topic_name),
            )
        response = self.assert_json_success(result)
        self.assertFalse(response["drifted"])
        mock_create.assert_not_called()
