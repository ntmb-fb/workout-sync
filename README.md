# workout-sync

Copies your workouts from **Strava** to **Garmin Connect** and **Polar Flow**, automatically.
It runs as one small Docker container, for example on a NAS.

It was made for an Apple Watch running [WorkOutDoors](https://www.workoutdoors.net/), which uploads
to Strava by itself. It works with any recording app or device that uploads to Strava.

```
Watch app ──auto upload──▶ Strava ──▶ workout-sync ──▶ Garmin Connect
                                      (polls 4×/day,  └─▶ Polar Flow
                                       or on demand)
```

> **Unofficial.** For personal use only. Not affiliated with or endorsed by Strava, Garmin or Polar.
> Garmin and Polar have no public upload API for individuals, so this tool uses the same
> unofficial endpoints as their own apps. They can change without notice and stop working.
> Use at your own risk.

## What it does

- Checks Strava for new activities on a schedule (default 06:00, 12:00, 18:00, 23:00), when you
  press **Sync now**, or when something calls the `/sync` URL.
- Takes only activities from the devices you choose (default: `WorkOutDoors`). It ignores manual entries.
- Builds a FIT file from the Strava data: GPS, heart rate, cadence, power, altitude, laps, pauses.
- Uploads it to Garmin Connect as an import. Imports are not shared back to Strava, so you get no loops.
- Uploads it to Polar Flow with GPS, heart rate, speed and altitude.
- Uploads each activity once per service. If a service already has it, the UI shows "already there".
- Retries failures automatically (3 times by default). After that, you can press **Resend**.
- Optionally emails you when something fails.
- Starts from the day you connect Strava. To copy older activities, use **Import older activities**.

What it does **not** do: it doesn't copy edits or deletes made later, and it doesn't copy
anything from Garmin or Polar back to Strava.

## Setup

You need a Docker host on x86-64 (amd64), Docker Compose, and accounts on Strava, Garmin and/or Polar.

### 1. Create a Strava API app

1. Go to <https://www.strava.com/settings/api> and create an app. Name and website can be anything.
2. Set **Authorization Callback Domain** to the host name or IP you will use to open the web UI,
   without `http://` or a port, for example `192.168.1.10`.
3. Write down the **Client ID** and **Client Secret**.

### 2. Start the container

Make a folder, e.g. `workout-sync/`, and put two files in it:

- [`docker-compose.yml`](docker-compose.yml)
- `.env`: copy [`.env.example`](.env.example) and fill it in. Generate the secrets with
  `openssl rand -hex 16` and `openssl rand -hex 32`.

Then run:

```sh
docker compose up -d
```

**Synology (Container Manager):** create the folder in File Station (e.g. `/docker/workout-sync`),
upload `.env` there, then go to **Container Manager → Project → Create**. Select that folder
and paste the contents of `docker-compose.yml`.

The image is `DOCKERHUB_USER/workout-sync:latest` on Docker Hub (linux/amd64).
Data (database, Garmin tokens, generated FIT files) is stored in `./data`.

### 3. Connect your accounts

Open `PUBLIC_URL` (e.g. `http://192.168.1.10:8080`) and log in with any user name and your
`ADMIN_PASSWORD`. Then:

1. **Connect Strava** and allow access to your activities.
2. **Connect Garmin**: enter your email and password. If you use two-step verification,
   enter the code when it asks. Only the login tokens are stored, not your password. They
   stay valid for a long time. If they expire, the UI asks you to log in again.
3. **Connect Polar**: enter your email and password. The Polar endpoint needs a login on
   every sync, so the password is stored encrypted with `SECRET_KEY`.

### 4. Test it

Use **Import older activities** with yesterday's date (or the date of a recent workout).
Check the activity in Garmin Connect and Polar Flow.

### 5. Avoid duplicates

- If Polar Flow or Garmin Connect is set to share to Strava, turn that off. Otherwise your
  own Garmin or Polar recordings show up twice on Strava.
- If another sync tool also copies these workouts, turn its automatic sync off.

## Sync on demand (optional)

`POST http://<host>:8080/sync?token=<SYNC_TOKEN>` starts a sync right away.
The token can also be sent in an `X-Sync-Token` header.

**iOS Shortcut that syncs right after a workout:**

1. Shortcuts app → **Automation** → **New Automation** → **App** → choose your workout app →
   **Is Closed** → **Run Immediately**.
2. Add the action **Get Contents of URL**:
   - URL: `http://<host>:8080/sync?token=<SYNC_TOKEN>`
   - Method: `POST`

Your phone must be able to reach the host, so this only works on your home network or over VPN.
Strava sometimes needs a minute to process an upload. If the sync runs too early, the next
scheduled sync picks the activity up.

## Settings

All settings are environment variables in `.env`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `PUBLIC_URL` | `http://localhost:8080` | URL of the web UI. Strava redirects back to it. |
| `ADMIN_PASSWORD` | – (required) | Web UI password. |
| `SYNC_TOKEN` | – (required) | Secret for the `/sync` URL. |
| `SECRET_KEY` | – (required) | Encrypts stored Strava and Polar credentials. |
| `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET` | – | From your Strava API app. |
| `TZ` | `UTC` | Time zone for the schedule. |
| `SYNC_TIMES` | `06:00,12:00,18:00,23:00` | When to sync. Empty = only on demand. |
| `DEVICE_FILTER` | `WorkOutDoors` | Comma-separated. Only sync activities whose Strava device name contains one of these. Empty = all. |
| `GARMIN_ENABLED`, `POLAR_ENABLED` | `true` | Turn a service on or off. |
| `GARMIN_SPOOF_DEVICE` | `false` | Label the FIT file as a Garmin watch, so Garmin calculates Training Effect, Load and Status from it. |
| `GARMIN_SPOOF_PRODUCT` | `3113` | Garmin product id used when spoofing (3113 = Forerunner 945). |
| `MAX_ATTEMPTS` | `3` | Automatic retries before an activity needs a manual resend. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_TO` | – | Failure emails. For Gmail, use `smtp.gmail.com`, port `587` and an [app password](https://myaccount.google.com/apppasswords). |
| `PORT` | `8080` | Port inside the container. |
| `LOG_LEVEL` | `INFO` | Log detail. |

## Troubleshooting

- **Status and errors** are shown in the web UI. More detail: `docker logs workout-sync`.
- **"Skipped: device … not in DEVICE_FILTER"**: the activity came from another device. Press
  **Send** to sync it anyway, or change `DEVICE_FILTER`.
- **Garmin login fails or is blocked**: Garmin changes its login now and then. Update the
  image (`docker compose pull && docker compose up -d`). Fixes usually arrive through the
  [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) library.
- **Polar upload fails**: the unofficial Polar endpoint may have changed. Open an issue.
- **A FIT file looks wrong**: every activity has a **FIT** link in the UI, so you can download
  the generated file and check it.

## How Polar uploads work

Polar has no public upload API. The retired Polar Beat app uploaded sessions to
`www.polarremote.com/v2` as protobuf messages, and that endpoint still accepts them.
See [docs/polar-protocol.md](docs/polar-protocol.md).

## Development

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest
DATA_DIR=./data ADMIN_PASSWORD=x SYNC_TOKEN=x SECRET_KEY=x python -m workout_sync
```

Code layout (`workout_sync/`):

| File | Role |
| --- | --- |
| `strava.py` | Strava OAuth, activity list, streams → `Workout` |
| `fitfile.py` | `Workout` → FIT file |
| `garmin.py` | Garmin login (with MFA) and upload |
| `polar.py` | `Workout` → Polar protobuf messages and upload |
| `sync.py` | One sync run: find new activities, upload, retry |
| `app.py` | Web UI, `/sync`, schedule |

A GitHub Action runs the tests and publishes the `linux/amd64` image to Docker Hub on every push to `main`.
It needs two repository secrets: `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` (a Docker Hub access token with Read & Write).

## License

[MIT](LICENSE)
