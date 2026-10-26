# Sarvam API setup and usage

This project is configured for the official Sarvam Python SDK. Use this guide to add Sarvam API calls to the new project's backend.

## Current project files

| File | Purpose |
| --- | --- |
| [`.env`](./.env) | Stores `SARVAM_API_KEY` locally. Never commit or share this file. |
| [`.gitignore`](./.gitignore) | Excludes `.env` from Git. |

The installed package is `sarvamai==0.1.39`, running with Python 3.13.1.

## Install the SDK

On a new machine or Python environment:

```bash
python -m pip install sarvamai
```

Sarvam officially supports Python 3.9 and newer.

## Configure authentication

Create `.env` in the project root:

```dotenv
SARVAM_API_KEY=your_key_here
```

Keep `.env` listed in `.gitignore`:

```gitignore
.env
```

Load this file with your application's configuration system, then pass `os.environ["SARVAM_API_KEY"]` to the SDK. Never hardcode or print the key.

For production, inject `SARVAM_API_KEY` through the hosting platform's secret manager instead of deploying a `.env` file. Keep API calls on the server; never place the key in browser or mobile client code.

## Minimal integration example

Add the following to the appropriate backend module after your application has loaded its environment variables:

```python
import os

from sarvamai import SarvamAI


client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])

response = client.chat.completions(
    model="sarvam-105b-conversations",
    messages=[
        {"role": "user", "content": "Say hello in one short sentence."}
    ],
)

print(response.choices[0].message.content)
```

`client.chat.completions(...)` uses Sarvam's V1 `POST /v1/chat/completions` endpoint. The response text is available at `response.choices[0].message.content`.

## Adapt it for a project

Replace the user message with input from your application:

```python
def ask_sarvam(prompt: str) -> str:
    response = client.chat.completions(
        model="sarvam-105b-conversations",
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""
```

Use `sarvam-105b-conversations` for dialogue and voice-agent workloads. Use `sarvam-105b` when the project needs more complex reasoning, coding, or agentic work.

For multi-turn chat, send the conversation history on each request:

```python
messages = [
    {"role": "user", "content": "Plan a weekend in Jaipur."},
    {"role": "assistant", "content": first_reply},
    {"role": "user", "content": "Make it suitable for children."},
]
```

Long histories consume the model's context window and increase cost. Trim or summarize old turns before resending them.

## Choose the API for the product

Sarvam provides APIs for:

| Product need | API family |
| --- | --- |
| Chatbot, assistant, reasoning | Chat Completion |
| Audio transcription or captions | Speech-to-Text |
| Generate spoken audio | Text-to-Speech |
| Translate or transliterate text | Text Processing |
| OCR or structured extraction from files | Document AI |
| Translate complete documents while preserving layout | Document Translation |
| Localize audio or video | Dubbing |
| Generate speech using a reference voice | Instant Voice Cloning |

Start with the [Sarvam Model APIs overview](https://docs.sarvam.ai/api/getting-started/welcome), then use the relevant API guide and reference for the product's input, output, latency, and streaming requirements.

## Production checklist

- Keep `SARVAM_API_KEY` in environment secrets and out of logs, source code, and client-side applications.
- Pin the SDK version in production and review the changelog before upgrades.
- Configure timeouts and retries for transient connection errors, HTTP `429`, and `5xx` responses.
- Treat HTTP `403` as a possible missing or invalid key; Sarvam authentication failures use `403`, not `401`.
- Monitor credits and rate limits in the Sarvam dashboard.
- Avoid logging full prompts or replies if they may contain personal or sensitive information.

## Official documentation

- [Developer quickstart](https://docs.sarvam.ai/api/getting-started/quickstart)
- [Python SDK guide](https://docs.sarvam.ai/api/getting-started/sdks)
- [Chat Completion guide](https://docs.sarvam.ai/api/api-guides-tutorials/chat-completion/overview)
- [Authentication](https://docs.sarvam.ai/api-reference/authentication)
- [API reference](https://docs.sarvam.ai/api-reference)
