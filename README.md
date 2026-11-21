![FRIDAY Banner](assets/banner.png)

# 🎬 FRIDAY — BookMyShow Booking Alert System

**FRIDAY** is an intelligent, automated real-time alert system designed to monitor **BookMyShow** for movie ticket bookings and trigger instant multi-channel alerts the second ticket bookings open (audio alarm, Windows desktop notifications, Telegram, Discord, Email, custom webhooks, and auto-opening the booking page in your browser).

**100% URL-Driven:** Simply paste the BookMyShow URL. FRIDAY automatically detects the **Movie**, **City**, and **Event Code** directly from the URL — completely eliminating spelling mistakes or naming mismatch confusion!

---

## 📋 Adding Targets ([targets.txt](targets.txt))

You can monitor **multiple movies across different cities** simultaneously. Open [targets.txt](file:///c:/Users/alang/OneDrive/Desktop/FRIDAY-Booking%20Alert%20System/targets.txt) and paste any BookMyShow URL (one per line):

```text
# Any Movie in Any City
https://in.bookmyshow.com/movies/coimbatore/dune-part-three/ET00491771
https://in.bookmyshow.com/movies/chennai/dune-part-three/ET00491771
https://in.bookmyshow.com/movies/bengaluru/avengers-doomsday/ET00439706

# Movie + Specific Theatre Focus (add pipe '|' followed by theatre names or cinema URLs)
https://in.bookmyshow.com/movies/coimbatore/dune-part-three/ET00491771 | Broadway Cinemas, Brookefields

# Movie + Direct Cinema URL(s)
https://in.bookmyshow.com/movies/coimbatore/dune-part-three/ET00491771 | https://in.bookmyshow.com/cinemas/coimbatore/broadway-cinemas-coimbatore/BWCB

# Direct Cinema Schedule URL
https://in.bookmyshow.com/cinemas/coimbatore/broadway-cinemas-coimbatore/BWCB
```

---

## ⚡ How FRIDAY Works

1. **Auto-Discovery**: FRIDAY parses the URL to extract the city (`/coimbatore/`, `/chennai/`, `/bengaluru/`), the movie slug, and the BookMyShow event code (`ET00...`).
2. **Batch Checking**: All URLs in `targets.txt` are checked in a single high-speed browser session using eager loading (taking under ~1 second per movie).
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
├── targets.txt                   # User monitoring targets (Movie & Cinema URLs)
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
