# 🎬 FRIDAY — BookMyShow Booking Alert System

**FRIDAY** is an intelligent, automated real-time alert system designed to monitor **BookMyShow** for movie ticket bookings and trigger instant multi-channel alerts the second ticket bookings open (audio alarm, Windows desktop notifications, Telegram, Discord, Email, custom webhooks, and auto-opening the booking page in your browser).

**100% URL-Driven:** Simply paste the BookMyShow URL. FRIDAY automatically detects the **Movie**, **City**, and **Event Code** directly from the URL — completely eliminating spelling mistakes or naming mismatch confusion!

---

## 📋 Adding Targets ([targets.txt](file:///c:/Users/alang/OneDrive/Desktop/FRIDAY-Booking%20Alert%20System/targets.txt))

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

## 🔔 Notification Channels ([.env](file:///c:/Users/alang/OneDrive/Desktop/FRIDAY-Booking%20Alert%20System/.env))

Configure your preferences in `.env` (refer to `.env.example`):
- 🔊 **Sound Alarm**: Loud looping audio alert when tickets drop.
- 🪟 **Desktop Toast**: Native Windows notification banner.
- 🌐 **Auto-Browser Open**: Automatically launches your browser to the booking page.
- 📱 **Telegram Bot**: Push notifications directly to your phone.
- 💬 **Discord Webhook**: Rich embedded notifications in your Discord server.
- 📧 **Email Alerts**: Instant SMTP email notification.
- 🔗 **Custom Webhook**: JSON payloads dispatched to your custom web endpoints.

---

## ⚡ PC Performance & Background Optimization

FRIDAY has been specifically engineered to run in the background on your personal PC for extended periods without slowing down gaming, browsing, or other tasks:

1. **Persistent Browser Session:** Instead of repeatedly launching and killing Chrome every 60s, FRIDAY maintains an active session across cycles, recycling cleanly every 50 cycles to prevent memory accumulation.
2. **Ultra-Lean Resource Blocking:** Blocks all images, video streams, audio, web fonts, background sync, and tracker networks. Reduces page weight by **85%+** and DOM load times to under ~1 second.
3. **Memory Unloading (`about:blank`):** While idle between check cycles, the browser automatically unloads the page to `about:blank`, dropping Chrome's idle memory footprint to ~30MB.
4. **Windows Eco Mode (`ECO_PRIORITY=true`):** Runs the Python and browser processes under `BELOW_NORMAL_PRIORITY_CLASS` using native Windows API, ensuring zero micro-stutters for everyday PC use.
5. **Adaptive Night Polling (`ADAPTIVE_POLLING=true`):** Optionally relaxes check intervals automatically during late night hours (00:00 - 07:00).
6. **Zero Zombie Processes:** `atexit` and `SIGINT`/`SIGTERM` handlers guarantee that terminating the monitor instantly kills any child headless Chrome and ChromeDriver processes.

---

## 🚀 Running FRIDAY

### Continuous Monitoring
```powershell
python monitor.py
```
*(Or double-click [start_monitor.bat](file:///c:/Users/alang/OneDrive/Desktop/FRIDAY-Booking%20Alert%20System/start_monitor.bat))*

### One-off Single Check
```powershell
python monitor.py --once
```

### Test Alerts On-Demand
```powershell
python monitor.py --test
```
