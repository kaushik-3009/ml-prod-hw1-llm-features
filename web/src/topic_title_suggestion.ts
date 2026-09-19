import {$} from "jquery";
import * as z from "zod/mini";

import * as channel from "./channel.ts";
import * as dialog_widget from "./dialog_widget.ts";
import {$t} from "./i18n.ts";
import * as message_edit from "./message_edit.ts";

const suggestion_schema = z.object({
    drifted: z.boolean(),
    suggested_title: z.string(),
    reason: z.string(),
    confidence: z.number(),
    sample_message_id: z.optional(z.nullable(z.number())),
});

// Debounce auto-checks so a burst of sends in one topic costs one LLM call.
const last_auto_check = new Map<string, number>();
const AUTO_CHECK_DEBOUNCE_MS = 5 * 60 * 1000;
const checked_this_session = new Set<string>();

function topic_key(channel_id: number, topic_name: string): string {
    return `${channel_id}:${topic_name.toLowerCase()}`;
}

function apply_suggested_title(
    channel_id: number,
    current_topic: string,
    suggested_title: string,
    sample_message_id?: number | null,
): void {
    const do_rename = (message_id: number): void => {
        void channel.patch({
            url: "/json/messages/" + message_id,
            data: {
                topic: suggested_title,
                propagate_mode: "change_all",
                send_notification_to_old_thread: false,
                send_notification_to_new_thread: true,
            },
        });
    };
    if (sample_message_id !== undefined && sample_message_id !== null) {
        do_rename(sample_message_id);
        return;
    }
    // Fall back to resolving any message in the topic for the rename.
    message_edit.with_first_message_id(channel_id, current_topic, (message_id) => {
        if (message_id !== undefined) {
            do_rename(message_id);
        }
    });
}

function show_suggestion_modal(
    channel_id: number,
    current_topic: string,
    data: z.infer<typeof suggestion_schema>,
): void {
    const title = data.drifted
        ? $t({defaultMessage: "Topic may have drifted"})
        : $t({defaultMessage: "Title looks good"});
    const body = data.drifted
        ? `<p>${$t({defaultMessage: "Suggested title:"})} <strong></strong></p><p class="suggestion-reason"></p>`
        : `<p>${$t({defaultMessage: "No drift detected. The current title still fits."})}</p>`;
    dialog_widget.launch({
        modal_title_text: title,
        modal_content_html: body,
        close_on_submit: true,
        id: "topic-title-suggestion-modal",
        footer_minor_text: $t({defaultMessage: "AI suggestions may be wrong."}),
        modal_submit_button_text: data.drifted
            ? $t({defaultMessage: "Apply suggestion"})
            : $t({defaultMessage: "Close"}),
        modal_exit_button_text: data.drifted ? $t({defaultMessage: "Dismiss"}) : undefined,
        single_footer_button: !data.drifted,
        on_click() {
            if (data.drifted) {
                apply_suggested_title(
                    channel_id,
                    current_topic,
                    data.suggested_title,
                    data.sample_message_id,
                );
            }
        },
        post_render() {
            $("#topic-title-suggestion-modal strong").text(data.suggested_title);
            $("#topic-title-suggestion-modal .suggestion-reason").text(data.reason);
        },
    });
}

export function suggest_title_for_topic(
    channel_id: number,
    topic_name: string,
    options: {auto?: boolean} = {},
): void {
    void channel.post({
        url: "/json/topics/suggest-title",
        data: {channel_id, topic_name},
        success(raw_data) {
            const data = suggestion_schema.parse(raw_data);
            if (options.auto && !data.drifted) {
                return; // Stay silent when nothing drifted after a send.
            }
            show_suggestion_modal(channel_id, topic_name, data);
        },
    });
}

export function maybe_check_drift_after_send(channel_id: number, topic_name: string): void {
    // Called immediately after the user posts, while they still have context.
    const key = topic_key(channel_id, topic_name);
    if (checked_this_session.has(key)) {
        return;
    }
    const now = Date.now();
    const last = last_auto_check.get(key) ?? 0;
    if (now - last < AUTO_CHECK_DEBOUNCE_MS) {
        return;
    }
    last_auto_check.set(key, now);
    checked_this_session.add(key);
    suggest_title_for_topic(channel_id, topic_name, {auto: true});
}

export function clear_drift_check_cache_for_tests(): void {
    last_auto_check.clear();
    checked_this_session.clear();
}
