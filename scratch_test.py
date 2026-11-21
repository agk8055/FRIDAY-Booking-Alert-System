from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
import time

options = Options()
options.add_argument('--headless=new')
options.add_argument('--disable-gpu')
options.add_argument('--no-sandbox')
options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

driver = webdriver.Chrome(options=options)
try:
    url = 'https://in.bookmyshow.com/movies/kochi/avengers-doomsday-ms-infinity-vsn-3d/ET00526306'
    driver.get(url)
    time.sleep(2)
    btn = driver.find_element('xpath', '//button[contains(., "Book tickets")]')
    driver.execute_script("arguments[0].click();", btn)
    time.sleep(3)
    print("Current URL after click:", driver.current_url)
    soup = BeautifulSoup(driver.page_source, "html.parser")
    
    # Check all date elements or date links
    print("=== DATES ON SHOWTIMES ===")
    for d in soup.select('[class*="date"], [data-date], a[href*="2026"]'):
        txt = d.text.strip().replace("\n", " ")
        if txt and len(txt) < 30:
            print("Date element:", repr(txt), "href:", d.get("href"))
            
    # Check if PVMF or Forum is in the page
    if "PVMF" in driver.page_source or "Forum" in driver.page_source:
        print("PVMF or Forum found in default date!")
    else:
        print("PVMF NOT in default date (Nov 19)!")
        
    # Now check what happens if we visit the cinema URL directly
    cinema_url = 'https://in.bookmyshow.com/cinemas/kochi/pvr-forum-mall-kochi/buytickets/PVMF/20261217'
    driver.get(cinema_url)
    time.sleep(3)
    print("Cinema URL title:", driver.title)
    if 'ET00526306' in driver.page_source:
        print("MATCH! ET00526306 found at cinema URL!")
    if 'Avengers' in driver.page_source:
        print("MATCH! Avengers found at cinema URL!")
finally:
    driver.quit()
