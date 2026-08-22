# alwaysdata Free deployment

alwaysdata Public Cloud can run the bridge as a headless foreground service.
The free plan is suitable for a personal low-traffic installation, but its
resources are shared and must be used fairly.

## Security gate

Before uploading anything, rotate every MAX session and Telegram bot token that
has appeared in chat, logs, screenshots, or clipboard history. Never add the
replacement values to Git or to the alwaysdata service command.

## 1. Create the free account

Create an alwaysdata profile and a Free Public Cloud account. Replace
`ACCOUNT` below with the chosen account name. The default SSH user has the same
name. Enable password login temporarily under `Remote access -> SSH/SFTP`, or
install an SSH public key and disable password login again.

## 2. Install from GitHub

Connect to the account and install only runtime dependencies:

```bash
ssh ACCOUNT@ssh-ACCOUNT.alwaysdata.net
git clone https://github.com/RAMMLS/MaxToTelegramMessages.git max-to-telegram
cd max-to-telegram
sh deploy/alwaysdata/bootstrap.sh
```

The bootstrap is idempotent, disables the pip download cache, creates a private
`data/` directory, and creates `.env` from a credential-free template only when
the file does not already exist.

## 3. Transfer private configuration

Keep `.env` and the MAX session file outside Git. From the local project, after
rotating both credentials:

```bash
scp .env ACCOUNT@ssh-ACCOUNT.alwaysdata.net:max-to-telegram/.env
scp .max-session.json \
  ACCOUNT@ssh-ACCOUNT.alwaysdata.net:max-to-telegram/data/max-session.json
ssh ACCOUNT@ssh-ACCOUNT.alwaysdata.net \
  "cd max-to-telegram && \
   sed -i 's|^MAX_SESSION_FILE=.*|MAX_SESSION_FILE=data/max-session.json|' .env && \
   sed -i 's|^BRIDGE_STATE_DB=.*|BRIDGE_STATE_DB=data/state.sqlite3|' .env && \
   chmod 600 .env data/max-session.json"
```

The alwaysdata template expects these relative persistent paths:

```dotenv
MAX_SESSION_FILE=data/max-session.json
BRIDGE_STATE_DB=data/state.sqlite3
```

The two `sed` replacements change only path settings after transfer and never
print the file or its secret values. If your local `.env` uses direct
`MAX_AUTH_TOKEN` instead of `MAX_SESSION_FILE`, migrate it to the protected
session file first so refreshed MAX credentials survive service restarts.

Do not paste tokens into the service command, service name, monitoring command,
or logs. Files in the account home directory persist across service restarts;
the bridge atomically writes refreshed MAX credentials back to the session file.

## 4. Validate before enabling delivery

Run the checks over SSH from the project working directory:

```bash
cd ~/max-to-telegram
.venv/bin/max-to-telegram --check-config
.venv/bin/max-to-telegram --check-telegram
.venv/bin/max-to-telegram --check-state
```

These checks do not send a Telegram message. Keep `MAX_CHAT_IDS` restricted to
the intended source chat and leave `MAX_DISCOVERY_MODE=false` for delivery.

To enable the content-free liveness report in the destination chat, set this in
the private `.env`:

```dotenv
DAILY_REPORT_ENABLED=true
```

The first report is sent about 10 seconds after the reporter starts, allowing
the MAX login to complete; later reports are spaced 24 hours from the last
successful report. Its counters and schedule survive normal service restarts in
`BRIDGE_STATE_DB`. Restart the custom service after changing the flag.

## 5. Register the 24/7 service

In `Advanced -> Services`, create one service with:

- command: `.venv/bin/max-to-telegram`;
- working directory: `max-to-telegram`;
- environment: empty;
- monitoring command: empty.

The administration form displays `/home/ACCOUNT/` before the working-directory
field and prepends it automatically. Enter only `max-to-telegram`; entering the
absolute path would resolve to the invalid nested path
`/home/ACCOUNT/home/ACCOUNT/max-to-telegram`. The virtual environment already
pins the Python interpreter chosen by the bootstrap, so the service does not
need a separate `PYTHON_VERSION` value.

The command stays in the foreground as required by alwaysdata. The platform
restarts it after an unexpected exit. No incoming port or public site is needed.
Service logs are available in the administration panel and under
`~/admin/logs/services/`.

## 6. Verify the running service

Confirm that the service remains enabled and inspect only redacted operational
logs. With daily reports enabled, confirm the first heartbeat arrives and shows
the current MAX connection and zero failed outbox items. Then wait for a new post
from the selected MAX channel and verify one
delivery to the configured Telegram conversation. Also send a message in an
unselected MAX chat and confirm that no Telegram request is made.

If the process is repeatedly disabled, inspect the exit code and logs first.
Exit code `2` indicates configuration, credentials, protocol, or local-state
failure and must not be hidden by a restart loop. A temporary network/API
failure uses exit code `75`; the durable outbox resumes after restart.
