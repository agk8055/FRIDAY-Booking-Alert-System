![FRIDAY Banner](assets/banner.png)

# 🎬 FRIDAY — BookMyShow Booking Alert System

**FRIDAY** is an intelligent, automated real-time alert system designed to monitor **BookMyShow** for movie ticket bookings and trigger instant multi-channel alerts the second ticket bookings open (audio alarm, Windows desktop notifications, Telegram, Discord, Email, custom webhooks, and auto-opening the booking page in your browser).

**100% URL-Driven:** Simply paste the BookMyShow URL. FRIDAY automatically detects the **Movie**, **City**, and **Event Code** directly from the URL — completely eliminating spelling mistakes or naming mismatch confusion!

---

## 📋 Adding Targets ([targets.json](targets.json))

You can monitor **multiple movies across different cities** simultaneously. Use the Web Dashboard or edit [targets.json](file:///c:/Users/alang/OneDrive/Desktop/FRIDAY-Booking%20Alert%20System/targets.json):

```json
{
  "targets": [
    {
      "enabled": true,
      "url": "https://in.bookmyshow.com/movies/kochi/avengers-doomsday/ET00439706",
      "theatres": [
        "https://in.bookmyshow.com/cinemas/kochi/vanitha-cineplex-rgb-laser-4k-3d-atmos-edappally/buytickets/VMHE"
      ],
      "target_date": "20261217"
    }
  ]
}
```

---

## ⚡ How FRIDAY Works

1. **Auto-Discovery**: FRIDAY parses the URL to extract the city (`/coimbatore/`, `/chennai/`, `/bengaluru/`), the movie slug, and the BookMyShow event code (`ET00...`).
2. **Batch Checking**: All enabled targets in `targets.json` are checked in a single high-speed browser session using eager loading (taking under ~1 second per movie).
3. **Targeted Theatre Filtering**: If theatre names or cinema URLs are provided after `|`, FRIDAY waits until that exact theatre has shows available. If omitted, it alerts for any theatre in that city.
4. **Instant Multi-Channel Alerts**: When a movie opens, FRIDAY fires alerts immediately with the exact movie, city, and direct booking link, while continuous monitoring proceeds for your other targets.

---

## 🔔 Notification Channels ([.env.example](.env.example))

Configure your preferences in `.env` (refer to `.env.example`):
- 🔊 **Sound Alarm**: Loud looping audio alert when tickets drop.
- 🪟 **Desktop Toast**: Native Windows notification banner.
- 🌐 **Auto-Browser Open**: Automatically launches your browser to the booking page.
- 📱 **Telegram Bot**: Push notifications directly to your phone.
- 💬 **Discord Webhook**: Rich embedded notifications in your Discord server.
- 📧 **Email Alerts**: Instant SMTP email notification.
- 🔗 **Custom Webhook**: JSON payloads dispatched to your custom web endpoints.

---

## ⚡ High-Speed Hybrid Engine (`curl_cffi` + Chrome Fallback)

FRIDAY is built with a dual-engine architecture for maximum speed and efficiency on personal PCs:

1. **⚡ Primary Engine (`curl_cffi`):**
   - Directly negotiates TLS fingerprint impersonation (`impersonate="chrome124"`), bypassing Cloudflare and Akamai bot defenses at the socket level.
   - Extracts BookMyShow's server-rendered `window.__INITIAL_STATE__` JSON directly in **~200ms**.
   - **Zero browser processes:** Chrome is never launched, keeping RAM usage at an ultra-light **~25MB** with **0% CPU** consumption.

2. **🛡️ On-Demand Fallback (Headless Chrome):**
   - If BookMyShow ever presents a complex client-side JavaScript challenge or CAPTCHA, FRIDAY automatically engages headless Chrome on-demand.
   - Chrome runs with image and video blocking enabled, and cleanly unloads to `about:blank` when idle.

3. **🌱 Windows Eco Priority (`ECO_PRIORITY=true`):**
   - Sets process priority to `BELOW_NORMAL_PRIORITY_CLASS` so you never experience stutters during PC work or gaming.

4. **🌙 Adaptive Polling (`ADAPTIVE_POLLING=true`):**
   - Automatically relaxes check frequency during off-peak night hours (00:00 - 07:00).

---

## 📁 Project Structure

```text
FRIDAY-Booking Alert System/
│
├── src/                          # Core application logic
│   ├── checker.py                # Dual-engine checker (curl_cffi + Selenium fallback)
│   ├── config.py                 # Configuration loader & TargetItem models
│   ├── monitor.py                # Main monitoring loop & PC eco background worker
│   ├── notifier.py               # Multi-channel alert dispatchers
│   └── __init__.py
│
├── tests/                        # Tests & diagnostic tools
│   ├── test_alerts.py            # Notification dispatcher test script
│   └── scratch_test.py           # DOM inspector scratch utility
│
├── logs/                         # Output logs & opened booking history
│   ├── monitor.log               # Background execution log
│   └── booking_opened.txt        # History of detected open bookings
│
├── run.py                        # Primary root entrypoint
├── monitor.py                    # Backward-compatible CLI entrypoint
├── start_monitor.bat             # 1-click batch launcher for Windows
├── targets.json                  # User monitoring targets (Movie & Cinema URLs)
├── .env                          # User configuration & notification credentials
├── .env.example                  # Environment settings template
├── requirements.txt              # Project dependencies
└── README.md                     # Documentation
```

---

## 🚀 Running FRIDAY

### Continuous Monitoring
```powershell
python monitor.py
```
*(Or double-click [start_monitor.bat](start_monitor.bat))*

### One-off Single Check
```powershell
python monitor.py --once
```

### Test Alerts On-Demand
```powershell
python monitor.py --test
```

---

## ☁️ Hosting on Render

FRIDAY ships with a ready-to-use [Dockerfile](Dockerfile) and Render Blueprint ([render.yaml](render.yaml)) that run both the background monitor and the web dashboard (`http://0.0.0.0:$PORT`, defaulting to `8765`).

### Option 1: Deploy via Render Blueprint (Recommended)
1. Push this repository to GitHub/GitLab.
2. In the [Render Dashboard](https://dashboard.render.com/), click **New + → Blueprint** and connect your repository.
3. Render automatically detects [render.yaml](render.yaml) (`runtime: docker`, `healthCheckPath: /health`).
4. Fill in the prompted secret environment variables (`sync: false`):
   - `DASHBOARD_PASSWORD` — **strongly recommended** to protect your public dashboard with HTTP Basic Auth (`DASHBOARD_USERNAME` defaults to `admin`).
   - `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` (if `TELEGRAM_ENABLED=true`).
   - `SMTP_USER` / `SMTP_PASSWORD` / `EMAIL_RECIPIENT` (if `EMAIL_ENABLED=true`).
   - `DISCORD_WEBHOOK_URL` / `WEBHOOK_URL` (if enabled).
5. Click **Apply** to build and deploy.

### Important Notes for Render
- **Local PC alerts are disabled in cloud mode:** `SOUND_ALERT`, `WINDOWS_TOAST`, and `OPEN_BROWSER_ON_OPEN` are set to `false` in [render.yaml](render.yaml) and [Dockerfile](Dockerfile). Configure **Telegram**, **Discord**, **Email**, or a **Custom Webhook** to receive alerts.
- **Health Check:** The `/health` endpoint is unauthenticated so Render can probe service health even when `DASHBOARD_PASSWORD` is enabled.
- **Free Plan Spin-Down & Ephemeral Storage:**
  - Render's `free` web service spins down after 15 minutes without inbound HTTP traffic; upgrade to a paid instance for uninterrupted 24/7 monitoring.
  - Free instances use ephemeral storage, so changes made to [targets.json](targets.json) via the web dashboard will reset on redeploy/restart. Either commit your targets to [targets.json](targets.json) before deploying, or attach a Render Persistent Disk (paid plans) mounted at `/data` and set `DATA_DIR=/data`.

---

## 🖥️ Self-Hosting

You can self-host FRIDAY on any Linux VPS, home server, Raspberry Pi, or local Docker host.

### Option 1: Docker (Recommended)

Build the image (includes Python 3.13, `curl_cffi`, and Chromium + ChromeDriver for Selenium fallback):

```bash
docker build -t friday-booking-alert .
```

Run the container with persistent storage for `targets.json` and `logs/`:

```bash
docker run -d \
  --name friday \
  --restart unless-stopped \
  -p 8765:8765 \
  --env-file .env \
  -e DATA_DIR=/data \
  -v "$(pwd)/data:/data" \
  friday-booking-alert
```

- Access the dashboard at `http://localhost:8765` (or `http://<server-ip>:8765`).
- If you expose the dashboard publicly or via a reverse proxy (Nginx, Caddy, Cloudflare Tunnel), set `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD` in `.env` to enable Basic Auth.
- Mounting `-v "$(pwd)/data:/data"` with `-e DATA_DIR=/data` persists `targets.json` and `logs/` across container restarts and updates.

### Option 2: Bare-Metal / Linux VPS (Python)

1. **Install Python 3.10+ and dependencies:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
   *(Optional)* Install `chromium` and `chromium-driver` if you want the headless Selenium fallback available on the server:
   ```bash
   sudo apt-get update && sudo apt-get install -y chromium chromium-driver
   ```

2. **Configure environment:**
   ```bash
   cp .env.example .env
   ```
   Edit `.env` to set `HEADLESS_BROWSER=true`, disable desktop alerts (`SOUND_ALERT=false`, `WINDOWS_TOAST=false`, `OPEN_BROWSER_ON_OPEN=false`), configure Telegram/Discord/Email/Webhook alerts, and optionally set `DASHBOARD_HOST=0.0.0.0`, `DASHBOARD_PORT=8765`, `DASHBOARD_USERNAME`, and `DASHBOARD_PASSWORD`.

3. **Run FRIDAY:**
   ```bash
   python run.py --no-browser
   ```
   You can also override the bind host/port via CLI flags:
   ```bash
   python run.py --no-browser --dashboard-host 0.0.0.0 --dashboard-port 8765
   ```

4. **Keep it running with `systemd` (Linux):**
   Create `/etc/systemd/system/friday.service`:
   ```ini
   [Unit]
   Description=FRIDAY BookMyShow Booking Alert System
   After=network-online.target
   Wants=network-online.target

   [Service]
   Type=simple
   User=ubuntu
   WorkingDirectory=/path/to/FRIDAY-Booking Alert System
   ExecStart=/path/to/FRIDAY-Booking Alert System/.venv/bin/python run.py --no-browser
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=multi-user.target
   ```
   Then enable and start the service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now friday
   sudo systemctl status friday
   ```
