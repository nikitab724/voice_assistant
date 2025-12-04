# Configuration

Most configuration now lives in `config.json` (at the repo root). Environment variables remain available as overrides.

## config.json

Example:

```json
{
  "openai": {
    "model": "gpt-5.1",
    "api_key_name": "OPENAI_API_KEY"
  },
  "google": {
    "service_account_file": "service-account.json",
    "calendar_id": "primary",
    "delegate": "user@example.com"
  }
}
```

- `openai.api_key_name`: name of the env var that stores your OpenAI key.
- `openai.model`: default model for the chatbot bridge.
- `google.service_account_json` or `google.service_account_file`: where to load Google credentials from (relative paths are resolved from the repo root).
- `google.calendar_id` / `google.delegate`: defaults for the Calendar MCP tool.

## Environment overrides

Set the following environment variables (e.g., in your shell profile or `.env`) if you prefer not to edit `config.json` or need to override settings per deployment.

## OpenAI

| Variable | Required | Description |
| --- | --- | --- |
| `OPENAI_API_KEY` | ✅ | API key used by the chatbot bridge to call OpenAI models. |
| `OPENAI_MODEL` | ⛔️ (default `gpt-4o-mini`) | Optional override for the default chat model. |

## Google Calendar

Provide **either** `GOOGLE_SERVICE_ACCOUNT_JSON` **or** `GOOGLE_SERVICE_ACCOUNT_FILE` so the MCP tool can authenticate.

| Variable | Required | Description |
| --- | --- | --- |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | ✅\* | Raw JSON for the Google service account (preferred in development). |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | ✅\* | Absolute path to the service-account JSON file (use if you can't inline JSON). |
| `GOOGLE_CALENDAR_DELEGATE` | ⛔️ | Email to impersonate when using domain-wide delegation. |
| `GOOGLE_CALENDAR_ID` | ⛔️ (default `primary`) | Calendar ID to target when none is provided in the tool call. |

\*Provide one of the two credential options.

