import {$} from "jquery";
import * as z from "zod/mini";

import render_message_recap from "../templates/message_recap.hbs";

import * as channel from "./channel.ts";
import * as dialog_widget from "./dialog_widget.ts";
import * as hash_util from "./hash_util.ts";
import {$t} from "./i18n.ts";
import * as message_store from "./message_store.ts";
import * as rendered_markdown from "./rendered_markdown.ts";

const recap_response_schema = z.object({
    recap: z.string(),
    recap_markdown: z.string(),
    message_ids: z.array(z.number()),
    references: z.array(
        z.object({
            message_id: z.number(),
            stream_id: z.optional(z.nullable(z.number())),
            stream_name: z.string(),
            topic: z.string(),
            sender: z.string(),
            near_url: z.string(),
        }),
    ),
});

function authoritative_near_url(ref: {message_id: number}): string {
    // Prefer the frontend's hash_util (correct slug encoding) when the
    // message is in the local store; fall back to the backend URL.
    const message = message_store.get(ref.message_id);
    if (message !== undefined) {
        return hash_util.by_conversation_and_time_url(message);
    }
    return `#narrow/near/${ref.message_id}`;
}

function rewrite_citation_hrefs(
    recap_html: string,
    refs: {message_id: number; near_url: string}[],
): string {
    // Same rewrite for backend-rendered HTML: swap hrefs pointing at
    // .../near/<id> for known messages to authoritative frontend URLs.
    const by_id = new Map(refs.map((ref) => [ref.message_id, ref.near_url]));
    return recap_html.replace(/href="([^"]*\/near\/(\d+)[^"]*)"/g, (match, _url, id_text) => {
        const url = by_id.get(Number.parseInt(id_text, 10));
        if (url === undefined) {
            return match;
        }
        return `href="${url}"`;
    });
}

export function show_unread_recap(limit = 30): void {
    dialog_widget.launch({
        modal_title_text: $t({defaultMessage: "Unread recap"}),
        modal_content_html: `<div class="recap-loading">${$t({defaultMessage: "Generating recap…"})}</div>`,
        close_on_submit: true,
        id: "message-recap-modal",
        footer_minor_text: $t({defaultMessage: "AI recaps may have errors."}),
        modal_submit_button_text: $t({defaultMessage: "Close"}),
        single_footer_button: true,
        on_click() {
            // Just close the modal, there is nothing else to do.
        },
        post_render() {
            const close_on_success = false;
            dialog_widget.submit_api_request(
                channel.get,
                "/json/messages/recap",
                {limit},
                {
                    success_continuation(response_data) {
                        const data = recap_response_schema.parse(response_data);
                        const refs = data.references.map((ref) => ({
                            ...ref,
                            near_url: authoritative_near_url(ref),
                        }));
                        const recap_html = render_message_recap({
                            recap: rewrite_citation_hrefs(data.recap, refs),
                            has_references: refs.length > 0,
                            references: refs,
                        });
                        $("#message-recap-modal .modal__content")
                            .addClass("rendered_markdown")
                            .html(recap_html);
                        rendered_markdown.update_elements(
                            $("#message-recap-modal .modal__content"),
                        );
                    },
                },
                close_on_success,
            );
        },
    });
}
