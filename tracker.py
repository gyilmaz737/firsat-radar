from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import pandas as pd
from datetime import datetime
import time
import os
import re

PRODUCTS_FILE = "products.csv"
HISTORY_FILE = "price_history.csv"
REPORT_FILE = "daily_report.csv"

def clean_price(price_text):
    if not price_text or price_text == "Fiyat bulunamadı":
        return None

    price_text = str(price_text)
    price_text = price_text.replace("TL", "").replace("₺", "").strip()
    price_text = price_text.replace(".", "").replace(",", ".")

    match = re.search(r"\d+(\.\d+)?", price_text)
    if match:
        return float(match.group())

    return None

def get_price(driver, url):
    driver.get(url)
    time.sleep(3)

    try:
        title = driver.find_element(By.ID, "productTitle").text.strip()
    except:
        title = "Başlık bulunamadı"

    price = "Fiyat bulunamadı"

    price_selectors = [
        "#corePrice_feature_div .a-offscreen",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "#priceblock_saleprice",
        ".a-price .a-offscreen",
        "span.a-price-whole"
    ]

    for selector in price_selectors:
        try:
            element = driver.find_element(By.CSS_SELECTOR, selector)
            text = element.get_attribute("textContent").strip()
            if text:
                price = text
                break
        except:
            pass

    return title, price

def main():
    now = datetime.now()
    today_text = now.strftime("%Y-%m-%d %H:%M:%S")
    today_date = now.date()

    products = pd.read_csv(PRODUCTS_FILE)

    options = webdriver.ChromeOptions()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.binary_location = "/usr/bin/chromium"
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    
    driver = webdriver.Chrome(
        service=Service("/usr/bin/chromedriver"),
        options=options
    )

    new_rows = []
    report_rows = []

    for _, row in products.iterrows():
        name = row["name"]
        url = row["url"]
        start_date = datetime.strptime(str(row["start_date"]), "%Y-%m-%d").date()
        end_date = datetime.strptime(str(row["end_date"]), "%Y-%m-%d").date()
        target_price = float(row["target_price"])
        is_active = int(row["is_active"])

        if is_active != 1:
            print(f"Pasif ürün atlandı: {name}")
            continue

        if today_date < start_date or today_date > end_date:
            print(f"Tarih aralığı dışında: {name}")
            continue

        print(f"Kontrol ediliyor: {name}")

        title, price_text = get_price(driver, url)
        price_number = clean_price(price_text)

        print(f"Fiyat: {price_text}")

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

        print("\nHEDEF FİYATA DÜŞEN ÜRÜNLER:")
        for _, row in report_df.iterrows():
            print(
                f"{row['name']} → {row['current_price']} TL "
                f"| Hedef: {row['target_price']} TL"
            )

        print(f"\nRapor oluşturuldu: {REPORT_FILE}")
    else:
        print("\nHedef fiyata düşen ürün yok.")

    print(f"Geçmiş fiyatlar kaydedildi: {HISTORY_FILE}")

import time

if __name__ == "__main__":
    while True:
        print("Kontrol çalışıyor...")
        main()
        print("30 dakika bekleniyor...")
        time.sleep(1800)  # 30 dakika
