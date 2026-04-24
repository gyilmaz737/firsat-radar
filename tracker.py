import os
import re
import time
from datetime import datetime

import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By


PRODUCTS_FILE = "products.csv"
HISTORY_FILE = "price_history.csv"
REPORT_FILE = "daily_report.csv"


def clean_price(price_text):
    if not price_text or price_text == "Fiyat bulunamadı":
        return None

    text = str(price_text)
    text = text.replace("TL", "")
    text = text.replace("₺", "")
    text = text.replace(".", "")
    text = text.replace(",", ".")
    text = text.strip()

    match = re.search(r"\d+(\.\d+)?", text)
    if match:
        return float(match.group())

    return None


def create_driver():
    options = webdriver.ChromeOptions()
    options.binary_location = "/usr/bin/chromium"
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--lang=tr-TR")

    service = Service("/usr/bin/chromedriver")

    return webdriver.Chrome(service=service, options=options)


def get_price(driver, url):
    driver.get(url)
    time.sleep(4)

    try:
        title = driver.find_element(By.ID, "productTitle").text.strip()
    except Exception:
        title = "Başlık bulunamadı"

    price = "Fiyat bulunamadı"

    selectors = [
        "#corePrice_feature_div .a-offscreen",
        ".a-price .a-offscreen",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "#priceblock_saleprice",
        "span.a-price-whole",
    ]

    for selector in selectors:
        try:
            element = driver.find_element(By.CSS_SELECTOR, selector)
            text = element.get_attribute("textContent").strip()
            if text:
                price = text
                break
        except Exception:
            pass

    return title, price


def run_once():
    print("BOT BAŞLADI 🚀", flush=True)
    print("Ürünler okunuyor...", flush=True)

    now = datetime.now()
    today_text = now.strftime("%Y-%m-%d %H:%M:%S")
    today_date = now.date()

    products = pd.read_csv(PRODUCTS_FILE)

    driver = create_driver()

    new_rows = []
    report_rows = []

    try:
        for _, row in products.iterrows():
            name = str(row["name"])
            url = str(row["url"])
            start_date = datetime.strptime(str(row["start_date"]), "%Y-%m-%d").date()
            end_date = datetime.strptime(str(row["end_date"]), "%Y-%m-%d").date()
            target_price = float(row["target_price"])
            is_active = int(row["is_active"])

            if is_active != 1:
                print(f"Pasif ürün atlandı: {name}", flush=True)
                continue

            if today_date < start_date or today_date > end_date:
                print(f"Tarih aralığı dışında: {name}", flush=True)
                continue

            print(f"Kontrol ediliyor: {name}", flush=True)

            title, price_text = get_price(driver, url)
            price_number = clean_price(price_text)

            print(f"Fiyat: {price_text}", flush=True)

            new_rows.append({
                "date": today_text,
                "name": name,
                "title": title,
                "price_text": price_text,
                "price": price_number,
                "target_price": target_price,
                "url": url
            })

            if price_number is not None and price_number <= target_price:
                report_rows.append({
                    "date": today_text,
                    "name": name,
                    "current_price": price_number,
                    "target_price": target_price,
                    "difference": round(target_price - price_number, 2),
                    "url": url
                })

    finally:
        driver.quit()

    new_df = pd.DataFrame(new_rows)

    if os.path.exists(HISTORY_FILE):
        old_history = pd.read_csv(HISTORY_FILE)
        full_history = pd.concat([old_history, new_df], ignore_index=True)
    else:
        full_history = new_df

    full_history.to_csv(HISTORY_FILE, index=False)

    report_df = pd.DataFrame(report_rows)

    if not report_df.empty:
        report_df = report_df.sort_values("difference", ascending=False)
        report_df.to_csv(REPORT_FILE, index=False)

        print("HEDEF FİYATA DÜŞEN ÜRÜNLER:", flush=True)

        for _, row in report_df.iterrows():
            print(
                f"{row['name']} → {row['current_price']} TL | Hedef: {row['target_price']} TL",
                flush=True
            )
    else:
        print("Hedef fiyata düşen ürün yok.", flush=True)

    print(f"Geçmiş fiyatlar kaydedildi: {HISTORY_FILE}", flush=True)


if __name__ == "__main__":
    while True:
        try:
            print("Kontrol çalışıyor...", flush=True)
            run_once()
            print("30 dakika bekleniyor...", flush=True)
        except Exception as e:
            print(f"HATA: {e}", flush=True)

        time.sleep(1800)
