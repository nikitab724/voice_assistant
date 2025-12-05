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
| `GOOGLE_OAUTH_CLIENT_SECRET_FILE` | ⛔️ | Path to the OAuth client secret JSON (if using OAuth instead of a service account). |
| `GOOGLE_OAUTH_TOKEN_FILE` | ⛔️ | Path to store the OAuth token (defaults to `token.json`). |

### Running the OAuth flow

If you prefer OAuth credentials (so events appear as you, without sharing calendars with a service account):

1. Put your `client_secret_*.json` somewhere accessible and reference it via `google.oauth_client_secret_file` or `GOOGLE_OAUTH_CLIENT_SECRET_FILE`.
2. Set `google.oauth_token_file` (or `GOOGLE_OAUTH_TOKEN_FILE`) to where you want the refresh token saved.
3. Run:
   ```bash
   python google_apis.py oauth
   ```
   This launches the browser consent screen and writes the resulting token to the configured path.
4. Start the MCP server (`python src/workflow_server.py`). The tool will now use your OAuth credentials.

\*Provide one of the two credential options.

## Available MCP tools

Once the server is running (`python src/workflow_server.py`), the following tools are exposed:

- `create_google_calendar_event`: Book an event on the configured calendar.
- `list_google_calendar_events`: Read upcoming events within a time window (defaults to the next seven days).

