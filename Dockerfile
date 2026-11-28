FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HEADLESS_BROWSER=false \
    SOUND_ALERT=false \
    WINDOWS_TOAST=false \
    OPEN_BROWSER_ON_OPEN=false \
    CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver

# Install Chromium, ChromeDriver, and Xvfb (virtual display for non-headless stealth Chrome)
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    xvfb \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 10000

CMD ["sh", "-c", "rm -f /tmp/.X99-lock && Xvfb :99 -screen 0 1280x720x24 -nolisten tcp -ac >/dev/null 2>&1 & export DISPLAY=:99 && exec python run.py --no-browser"]
