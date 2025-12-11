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
- `delete_google_calendar_event`: Remove an event by ID from the configured calendar.

## Chat HTTP server (Flask + Uvicorn)

The `POST /api/chat` endpoint (plus `GET /health`) now run behind Uvicorn so the iOS client can talk to the Mac from the local network or via a tunnel:

1. Install the ASGI server once in your virtualenv:
   ```bash
   pip install "uvicorn[standard]"
   ```
2. During development you can keep using the script directly—it now boots Uvicorn automatically:
   ```bash
   python scripts/flask_server.py
   ```
3. For a long-running process (or when deploying), start it explicitly so you can choose host/port:
   ```bash
   uvicorn scripts.flask_server:asgi_app --host 0.0.0.0 --port 5050
   ```
   Replace `0.0.0.0` with the machine’s LAN IP (or the hostname provided by ngrok/cloudflared) before pointing the Swift app at it.

### Server environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `CHAT_SERVER_HOST` | `0.0.0.0` | Address Uvicorn binds to. Use `127.0.0.1` if you want to keep it local-only. |
| `CHAT_SERVER_PORT` | `5050` | TCP port for the HTTP API. |
| `CHAT_SERVER_LOG_LEVEL` | `info` | Uvicorn log verbosity (`info`, `debug`, etc.). |
| `CHAT_SERVER_URL` | `http://localhost:5050` | Base URL consumed by helper scripts (`voice_ui.py`, `transcribe_and_chat.py`). Set it to `http://<mac-ip>:5050` or `https://…` when exposing the service. |
| `CHAT_SERVER_CERT_FILE` | ⛔️ (unset) | Absolute path to a PEM certificate. When set together with `CHAT_SERVER_KEY_FILE`, HTTPS is enabled automatically. |
| `CHAT_SERVER_KEY_FILE` | ⛔️ (unset) | Private key that matches the certificate above. |
| `CHAT_SERVER_KEY_PASSWORD` | ⛔️ | Optional password for the private key. |

When targeting an iOS simulator/device on the same Wi‑Fi network, bind to `0.0.0.0`, find your Mac’s LAN IP via `ipconfig getifaddr en0`, and point the Swift client at `http://<that-ip>:5050/api/chat`. Use a tunneling service if you need it reachable over the public internet.

### Enabling HTTPS (macOS + iOS dev workflow)

1. **Generate a certificate.** The easiest local-dev setup is [`mkcert`](https://github.com/FiloSottile/mkcert):
   ```bash
   brew install mkcert nss # installs mkcert + CA toolchain
   mkcert -install         # adds the local CA to macOS trust store
   mkdir -p certs
   mkcert -cert-file certs/chat.pem -key-file certs/chat-key.pem \
          localhost $(hostname) 127.0.0.1 ::1
   ```
   If you need to reach the Mac from an iPhone/iPad, install the same mkcert root CA on the device (AirDrop the file printed by `mkcert -CAROOT` and trust it under Settings ▸ General ▸ About ▸ Certificate Trust Settings).
2. **Export the env vars before launching the server:**
   ```bash
   export CHAT_SERVER_CERT_FILE="$PWD/certs/chat.pem"
   export CHAT_SERVER_KEY_FILE="$PWD/certs/chat-key.pem"
   export CHAT_SERVER_URL="https://<mac-ip>:5050"
   export REQUESTS_CA_BUNDLE="$(mkcert -CAROOT)/rootCA.pem"  # so local Python clients trust it
   ```
3. **Start the server** (now serving HTTPS):
   ```bash
   python scripts/flask_server.py
   # or
   uvicorn scripts.flask_server:asgi_app --host 0.0.0.0 --port 5050 \
       --ssl-certfile "$CHAT_SERVER_CERT_FILE" --ssl-keyfile "$CHAT_SERVER_KEY_FILE"
   ```
4. **Update clients:**
   - Swift app: point to `https://<mac-ip>:5050/api/chat`.
   - Python helpers (`voice_ui.py`, `transcribe_and_chat.py`): set `CHAT_SERVER_URL` to the same HTTPS URL. If you used mkcert, keep `REQUESTS_CA_BUNDLE` exported so `requests` validates the cert. (Avoid disabling TLS verification; iOS ATS requires trusted certificates.)

For production-grade deployments, swap the mkcert pair with a publicly trusted certificate (e.g., LetsEncrypt) and leave the env vars pointing at those PEM files.

