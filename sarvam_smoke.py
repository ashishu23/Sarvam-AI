"""Run once after confirming the local Sarvam key is ready; never log secrets."""

import os
from pathlib import Path

from sarvamai import SarvamAI


def main() -> None:
    env_path = Path(__file__).with_name(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == "SARVAM_API_KEY":
                os.environ.setdefault(name.strip(), value.strip().strip("\"'"))

    api_key = os.environ.get("SARVAM_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("Add SARVAM_API_KEY to .env before running this script.")

    client = SarvamAI(api_subscription_key=api_key, timeout=60.0)
    try:
        result = client.chat.completions(
            model="sarvam-105b-conversations",
            messages=[{
                "role": "user",
                "content": "In one short sentence, confirm that Eagle Eye can reach Sarvam.",
            }],
            max_tokens=100,
            stream=False,
            request_options={"max_retries": 0},
        )
        reply = result.choices[0].message.content if result.choices else None
    except Exception as error:
        status = getattr(error, "status_code", None)
        category = type(error).__name__
        detail = f" HTTP {status}" if isinstance(status, int) else ""
        raise SystemExit(
            f"Sarvam request failed ({category}{detail}). "
            "Check key access, model access, credits and connectivity."
        ) from None

    if not reply or not reply.strip():
        raise SystemExit("Sarvam returned no reply text. Check the SDK response contract.")
    print(reply.replace(api_key, "[REDACTED]"))


if __name__ == "__main__":
    main()
