# HW1: LLM-enabled Zulip features (Message Recap + Topic Title Improver)

17-445/17-645/17-745 Machine Learning in Production, Individual Assignment 1.
This repo is Zulip plus two LLM features (Groq backend). Unless told otherwise,
start the dev server with Vagrant as below.

## 1. Prerequisites

- Vagrant (latest) + Docker Desktop (latest) on macOS, or Docker on Linux.
  2 GB RAM, broadband (first provision downloads several hundred MB).
- A Groq API key: https://console.groq.com → API Keys → copy your key.
  (free tier is enough; <$2 of usage for this assignment).
- No other credentials needed. **Never commit keys** — see §4.

## 2. Install and run (Vagrant path)

```bash
git clone <this-repo-url> zulip && cd zulip
vagrant up --provider=docker   # first time: 30-60 min
vagrant ssh
./tools/run-dev                # serves http://localhost:9991/
```

Open http://localhost:9991/devlogin and log in as e.g. `hamlet@zulip.com`.
No new system dependencies were added: both features reuse the existing
`openai` client (OpenAI-compatible Groq endpoint) already in Zulip's
requirements, so `./tools/provision` installs everything automatically.

macOS note: if provisioning fails with `ERR_PNPM_LINKING_FAILED`, switch
Docker Desktop file sharing to `osxfs (legacy)` for the provision, then back
to VirtioFS for development.

Alternative (used by the author): Ubuntu 22.04 machine (e.g. OrbStack),
clone inside the Linux filesystem, then `./tools/provision` +
`./tools/run-dev` directly, no Vagrant.

## 3. Provide the LLM key

Copy your key into the gitignored dev secrets file (never committed):

```ini
# zproject/dev-secrets.conf
[secrets]
topic_summarization_api_key = PASTE_YOUR_KEY_HERE
```

The backend reads it via `TOPIC_SUMMARIZATION_API_KEY` (`zproject/computed_settings.py`);
the dev default model is `openai/gpt-oss-20b` at
`https://api.groq.com/openai/v1` (`zproject/dev_settings.py`).
Restart `./tools/run-dev` after editing secrets. Staff grading should
substitute their own key in the same file.

## 4. Secrets hygiene

- `zproject/dev-secrets.conf` is in `.gitignore` (verified with
  `git check-ignore`). No `api.key` or `*.key` files are committed.
- Verify before submitting: search `git log --all -p` for any pasted key
  material. Only the `PASTE_YOUR_KEY_HERE` placeholder above may appear.

## 5. Try the features

- **Message Recap:** Inbox view → **Recap unread** button → modal with an LLM
  recap of your unread messages; `[msg <id>]` citations and the reference list
  link straight to the originals (`#narrow/.../near/<id>`).
  API: `GET /api/v1/messages/recap?limit=30`
  (get a dev API key: `curl -X POST http://localhost:9991/api/v1/dev_fetch_api_key
  --data-urlencode username=hamlet@zulip.com`).
- **Topic Title Improver:** topic popover → **Improve title**, or just send a
  message — a debounced drift check runs automatically and suggests a better
  title with **Apply** (renames via `change_all`) / **Dismiss**.
  API: `POST /api/v1/topics/suggest-title` with `channel_id`, `topic_name`.

## 6. Tests

```bash
./tools/test-backend zerver.tests.test_message_recap zerver.tests.test_topic_title_suggestion
```

Details and demo video links: see `implementation.md`.
