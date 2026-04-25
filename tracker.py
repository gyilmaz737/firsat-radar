import os
import re
import json
import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By


PRODUCTS_FILE = "products.csv"
SEARCHES_FILE = "searches.csv"
HISTORY_FILE = "price_history.csv"
SEARCH_HISTORY_FILE = "search_history.csv"
STATE_FILE = "telegram_state.json"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

ASIN_CHECK_INTERVAL_SECONDS = 1800
TELEGRAM_POLL_SECONDS = 3

# Türkiye saatine göre günde 3 kez arama raporu
SEARCH_REPORT_HOURS = [9, 14, 21]

last_update_id = 0
last_asin_check_time = 0
last_search_report_key = ""


def now_tr():
    return datetime.now(ZoneInfo("Europe/Istanbul"))


def ensure_files():
    if not os.path.exists(PRODUCTS_FILE):
        pd.DataFrame(columns=[
            "name", "url", "asin", "start_date", "end_date",
            "target_price", "is_active"
        ]).to_csv(PRODUCTS_FILE, index=False)

    if not os.path.exists(SEARCHES_FILE):
        pd.DataFrame(columns=[
            "keyword", "start_date", "end_date", "is_active"
        ]).to_csv(SEARCHES_FILE, index=False)

    if not os.path.exists(STATE_FILE):
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f, ensure_ascii=False)


def tg_send(text, keyboard=None):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram bilgileri eksik.", flush=True)
        print(f"TELEGRAM_TOKEN var mı: {bool(TELEGRAM_TOKEN)}", flush=True)
        print(f"TELEGRAM_CHAT_ID var mı: {bool(TELEGRAM_CHAT_ID)}", flush=True)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    chunks = [text[i:i + 3500] for i in range(0, len(text), 3500)]

    for chunk in chunks:
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }

        if keyboard and chunk == chunks[-1]:
            data["reply_markup"] = json.dumps(keyboard, ensure_ascii=False)

        try:
            r = requests.post(url, data=data, timeout=15)
            print(f"Telegram cevap: {r.status_code} - {r.text}", flush=True)
        except Exception as e:
            print(f"Telegram gönderim hatası: {e}", flush=True)


def tg_answer_callback(callback_id):
    if not TELEGRAM_TOKEN:
        return

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
        requests.post(url, data={"callback_query_id": callback_id}, timeout=10)
    except Exception:
        pass


def main_menu():
    return {
        "inline_keyboard": [
            [
                {"text": "➕ ASIN Ürün Ekle", "callback_data": "add_product"},
                {"text": "📋 ASIN Listem", "callback_data": "list_products"}
            ],
            [
                {"text": "❌ ASIN Ürün Sil", "callback_data": "delete_menu"},
                {"text": "🔄 ASIN Şimdi Kontrol Et", "callback_data": "check_now"}
            ],
            [
                {"text": "🔎 Arama Takibi Ekle", "callback_data": "add_search"},
                {"text": "📊 Arama Takiplerim", "callback_data": "list_searches"}
            ],
            [
                {"text": "🗑 Arama Takibi Sil", "callback_data": "delete_search_menu"},
                {"text": "🔍 Aramaları Şimdi Kontrol Et", "callback_data": "check_search_now"}
            ],
            [
                {"text": "ℹ️ Yardım", "callback_data": "help"}
            ]
        ]
    }


def load_state():
    ensure_files()
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def set_state(step, data=None):
    state = load_state()
    state[str(TELEGRAM_CHAT_ID)] = {
        "step": step,
        "data": data or {}
    }
    save_state(state)


def get_state():
    state = load_state()
    return state.get(str(TELEGRAM_CHAT_ID), {"step": None, "data": {}})


def clear_state():
    state = load_state()
    state[str(TELEGRAM_CHAT_ID)] = {
        "step": None,
        "data": {}
    }
    save_state(state)


def extract_asin(text):
    patterns = [
        r"/dp/([A-Z0-9]{10})",
        r"/gp/product/([A-Z0-9]{10})",
        r"asin=([A-Z0-9]{10})",
        r"\b([A-Z0-9]{10})\b"
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)

    return None


def make_amazon_link(asin):
    return f"https://www.amazon.com.tr/dp/{asin}"


def clean_price(price_text):
    if not price_text:
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
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )

    service = Service("/usr/bin/chromedriver")
    return webdriver.Chrome(service=service, options=options)


def get_price(driver, url):
    driver.get(url)
    time.sleep(5)

    try:
        title = driver.find_element(By.ID, "productTitle").text.strip()
    except Exception:
        title = "Başlık bulunamadı"

    price = None

    selectors = [
        "#corePrice_feature_div .a-offscreen",
        ".a-price .a-offscreen",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "#priceblock_saleprice"
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


def search_amazon(driver, keyword, limit=10):
    search_url = f"https://www.amazon.com.tr/s?k={quote_plus(keyword)}"
    driver.get(search_url)
    time.sleep(6)

    results = []
    items = driver.find_elements(By.CSS_SELECTOR, "div.s-result-item[data-asin]")

    for item in items:
        if len(results) >= limit:
            break

        try:
            asin = item.get_attribute("data-asin")
            if not asin or len(asin) != 10:
                continue

            title = ""

            for selector in ["h2 span", ".a-size-base-plus.a-color-base.a-text-normal", ".a-size-medium.a-color-base.a-text-normal"]:
                try:
                    title = item.find_element(By.CSS_SELECTOR, selector).text.strip()
                    if title:
                        break
                except Exception:
                    pass

            if not title:
                continue

            price_text = None

            for selector in [".a-price .a-offscreen", "span.a-price-whole"]:
                try:
                    price_text = item.find_element(By.CSS_SELECTOR, selector).get_attribute("textContent").strip()
                    if price_text:
                        break
                except Exception:
                    pass

            price_number = clean_price(price_text)

            if price_number is None:
                continue

            bad_words = [
                "kılıf", "kapak", "stand", "şarj istasyonu", "koruyucu",
                "skin", "case", "thumb", "analog başlık", "sticker",
                "etiket", "taşıma çantası"
            ]

            if any(word in title.lower() for word in bad_words):
                continue

            results.append({
                "keyword": keyword,
                "asin": asin,
                "title": title,
                "price_text": price_text,
                "price": price_number,
                "url": make_amazon_link(asin)
            })

        except Exception:
            continue

    results = sorted(results, key=lambda x: x["price"])
    return results[:limit]


def add_product_to_csv(name, url, asin, target_price, days):
    ensure_files()
    df = pd.read_csv(PRODUCTS_FILE)

    start_date = now_tr().date()
    end_date = start_date + timedelta(days=int(days))

    new_row = {
        "name": name,
        "url": url,
        "asin": asin,
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "target_price": float(target_price),
        "is_active": 1
    }

    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    df.to_csv(PRODUCTS_FILE, index=False)


def add_search_to_csv(keyword, days):
    ensure_files()
    df = pd.read_csv(SEARCHES_FILE)

    start_date = now_tr().date()
    end_date = start_date + timedelta(days=int(days))

    new_row = {
        "keyword": keyword,
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "is_active": 1
    }

    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    df.to_csv(SEARCHES_FILE, index=False)


def list_products_message():
    ensure_files()
    df = pd.read_csv(PRODUCTS_FILE)
    active_df = df[df["is_active"] == 1]

    if active_df.empty:
        return "📭 ASIN takip listende aktif ürün yok."

    msg = "📋 <b>ASIN Takip Listem</b>\n\n"

    for i, row in active_df.reset_index().iterrows():
        msg += (
            f"{i + 1}. <b>{row['name']}</b>\n"
            f"🎯 Hedef: {row['target_price']} TL\n"
            f"📅 Bitiş: {row['end_date']}\n"
            f"🔗 {row['url']}\n\n"
        )

    return msg


def list_searches_message():
    ensure_files()
    df = pd.read_csv(SEARCHES_FILE)
    active_df = df[df["is_active"] == 1]

    if active_df.empty:
        return "📭 Arama takip listende aktif kayıt yok."

    msg = "📊 <b>Arama Takiplerim</b>\n\n"

    for i, row in active_df.reset_index().iterrows():
        msg += (
            f"{i + 1}. 🔎 <b>{row['keyword']}</b>\n"
            f"📅 Bitiş: {row['end_date']}\n\n"
        )

    return msg


def delete_keyboard():
    ensure_files()
    df = pd.read_csv(PRODUCTS_FILE)
    active_df = df[df["is_active"] == 1].reset_index()

    buttons = []

    for i, row in active_df.iterrows():
        buttons.append([{
            "text": f"❌ {i + 1}. {row['name']}",
            "callback_data": f"delete_{row['index']}"
        }])

    buttons.append([{"text": "⬅️ Ana Menü", "callback_data": "menu"}])
    return {"inline_keyboard": buttons}


def delete_search_keyboard():
    ensure_files()
    df = pd.read_csv(SEARCHES_FILE)
    active_df = df[df["is_active"] == 1].reset_index()

    buttons = []

    for i, row in active_df.iterrows():
        buttons.append([{
            "text": f"🗑 {i + 1}. {row['keyword']}",
            "callback_data": f"delete_search_{row['index']}"
        }])

    buttons.append([{"text": "⬅️ Ana Menü", "callback_data": "menu"}])
    return {"inline_keyboard": buttons}


def delete_product(index):
    df = pd.read_csv(PRODUCTS_FILE)
    df.loc[int(index), "is_active"] = 0
    df.to_csv(PRODUCTS_FILE, index=False)


def delete_search(index):
    df = pd.read_csv(SEARCHES_FILE)
    df.loc[int(index), "is_active"] = 0
    df.to_csv(SEARCHES_FILE, index=False)


def send_search_report(keyword, results):
    if not results:
        tg_send(f"🔎 <b>{keyword}</b>\n\nSonuç bulunamadı veya fiyat okunamadı.")
        return

    msg = f"🔎 <b>{keyword}</b>\n🏆 Amazon en uygun 10 sonuç\n\n"

    for i, item in enumerate(results, start=1):
        short_title = item["title"]
        if len(short_title) > 70:
            short_title = short_title[:67] + "..."

        msg += (
            f"{i}. <b>{item['price']} TL</b>\n"
            f"{short_title}\n"
            f"{item['url']}\n\n"
        )

    tg_send(msg)


def run_search_reports(force=False):
    global last_search_report_key

    now = now_tr()
    today = now.date()
    current_hour = now.hour

    if not force:
        if current_hour not in SEARCH_REPORT_HOURS:
            return

        report_key = now.strftime("%Y-%m-%d") + f"-{current_hour}"

        if report_key == last_search_report_key:
            return

        last_search_report_key = report_key

    ensure_files()
    df = pd.read_csv(SEARCHES_FILE)

    if df.empty:
        print("Arama takip listesi boş.", flush=True)
        return

    active_df = df[df["is_active"] == 1]

    if active_df.empty:
        print("Aktif arama takibi yok.", flush=True)
        return

    tg_send("🔍 Arama takipleri kontrol ediliyor...")

    driver = create_driver()
    history_rows = []

    try:
        for _, row in active_df.iterrows():
            keyword = str(row["keyword"])
            start_date = datetime.strptime(str(row["start_date"]), "%Y-%m-%d").date()
            end_date = datetime.strptime(str(row["end_date"]), "%Y-%m-%d").date()

            if today < start_date or today > end_date:
                continue

            print(f"Amazon araması yapılıyor: {keyword}", flush=True)

            results = search_amazon(driver, keyword, limit=10)
            send_search_report(keyword, results)

            for item in results:
                history_rows.append({
                    "date": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "keyword": keyword,
                    "asin": item["asin"],
                    "title": item["title"],
                    "price": item["price"],
                    "url": item["url"]
                })

    finally:
        driver.quit()

    if history_rows:
        new_df = pd.DataFrame(history_rows)

        if os.path.exists(SEARCH_HISTORY_FILE):
            old_df = pd.read_csv(SEARCH_HISTORY_FILE)
            full_df = pd.concat([old_df, new_df], ignore_index=True)
        else:
            full_df = new_df

        full_df.to_csv(SEARCH_HISTORY_FILE, index=False)

    tg_send("✅ Arama takipleri tamamlandı.")


def run_price_check():
    print("ASIN fiyat kontrolü çalışıyor...", flush=True)
    ensure_files()

    df = pd.read_csv(PRODUCTS_FILE)

    if df.empty:
        print("ASIN ürün listesi boş.", flush=True)
        return

    now = now_tr()
    today = now.date()

    driver = create_driver()
    history_rows = []
    alert_messages = []

    try:
        for _, row in df.iterrows():
            if int(row["is_active"]) != 1:
                continue

            start_date = datetime.strptime(str(row["start_date"]), "%Y-%m-%d").date()
            end_date = datetime.strptime(str(row["end_date"]), "%Y-%m-%d").date()

            if today < start_date or today > end_date:
                continue

            name = str(row["name"])
            url = str(row["url"])
            target_price = float(row["target_price"])

            print(f"Kontrol ediliyor: {name}", flush=True)

            title, price_text = get_price(driver, url)
            price_number = clean_price(price_text)

            print(f"Fiyat: {price_text}", flush=True)

            history_rows.append({
                "date": now.strftime("%Y-%m-%d %H:%M:%S"),
                "name": name,
                "title": title,
                "price_text": price_text,
                "price": price_number,
                "target_price": target_price,
                "url": url
            })

            if price_number is not None and price_number <= target_price:
                alert_messages.append(
                    f"🔥 <b>Fırsat yakalandı!</b>\n\n"
                    f"Ürün: <b>{name}</b>\n"
                    f"Fiyat: {price_number} TL\n"
                    f"Hedef: {target_price} TL\n"
                    f"Link: {url}"
                )

    finally:
        driver.quit()

    new_history = pd.DataFrame(history_rows)

    if not new_history.empty:
        if os.path.exists(HISTORY_FILE):
            old_history = pd.read_csv(HISTORY_FILE)
            full_history = pd.concat([old_history, new_history], ignore_index=True)
        else:
            full_history = new_history

        full_history.to_csv(HISTORY_FILE, index=False)

    if alert_messages:
        for msg in alert_messages:
            tg_send(msg)

    print("ASIN kontrol tamamlandı.", flush=True)


def days_keyboard(prefix):
    return {
        "inline_keyboard": [
            [
                {"text": "3 gün", "callback_data": f"{prefix}_3"},
                {"text": "7 gün", "callback_data": f"{prefix}_7"}
            ],
            [
                {"text": "15 gün", "callback_data": f"{prefix}_15"},
                {"text": "30 gün", "callback_data": f"{prefix}_30"}
            ],
            [
                {"text": "60 gün", "callback_data": f"{prefix}_60"}
            ]
        ]
    }


def handle_text(text):
    user_state = get_state()
    step = user_state.get("step")
    data = user_state.get("data", {})

    if text == "/start":
        clear_state()
        tg_send("👋 Fırsat Radar hazır.\n\nNe yapmak istiyorsun?", main_menu())
        return

    if step == "waiting_link":
        asin = extract_asin(text)

        if not asin:
            tg_send("❌ ASIN bulunamadı. Amazon ürün linkini tekrar gönder.")
            return

        url = make_amazon_link(asin)
        data["asin"] = asin
        data["url"] = url

        set_state("waiting_target_price", data)
        tg_send(
            f"✅ Ürün linki alındı.\n\n"
            f"ASIN: <b>{asin}</b>\n\n"
            f"Şimdi hedef fiyatı yaz.\nÖrnek: 75"
        )
        return

    if step == "waiting_target_price":
        try:
            price = float(text.replace(",", "."))
        except Exception:
            tg_send("❌ Hedef fiyatı sayı olarak yaz. Örnek: 75")
            return

        data["target_price"] = price
        set_state("waiting_days", data)
        tg_send("⏱ Kaç gün takip edeyim?", days_keyboard("days"))
        return

    if step == "waiting_search_keyword":
        keyword = text.strip()

        if len(keyword) < 3:
            tg_send("❌ Arama kelimesi çok kısa. Örnek: DualSense 5")
            return

        data["keyword"] = keyword
        set_state("waiting_search_days", data)
        tg_send(f"🔎 Arama kaydı: <b>{keyword}</b>\n\nKaç gün takip edeyim?", days_keyboard("search_days"))
        return

    tg_send("Menüden seçim yapabilirsin 👇", main_menu())


def handle_callback(callback):
    callback_id = callback["id"]
    data = callback["data"]

    tg_answer_callback(callback_id)

    if data == "menu":
        clear_state()
        tg_send("Ana menü:", main_menu())

    elif data == "add_product":
        set_state("waiting_link", {})
        tg_send("🔗 Amazon ürün linkini gönder.\n\nÖrnek:\nhttps://www.amazon.com.tr/dp/B019OKXUCU")

    elif data == "list_products":
        tg_send(list_products_message(), main_menu())

    elif data == "delete_menu":
        tg_send("Silmek istediğin ASIN ürününe bas:", delete_keyboard())

    elif data.startswith("delete_") and not data.startswith("delete_search_"):
        index = data.replace("delete_", "")
        delete_product(index)
        tg_send("✅ ASIN ürün takip listesinden çıkarıldı.", main_menu())

    elif data.startswith("days_"):
        days = int(data.replace("days_", ""))
        product_data = get_state().get("data", {})

        asin = product_data.get("asin")
        url = product_data.get("url")
        target_price = product_data.get("target_price")

        if not asin or not url or not target_price:
            tg_send("❌ Eksik bilgi var. Lütfen yeniden ürün ekle.", main_menu())
            clear_state()
            return

        name = f"Amazon Ürünü {asin}"
        add_product_to_csv(name, url, asin, target_price, days)
        clear_state()

        tg_send(
            f"✅ ASIN ürün eklendi.\n\n"
            f"ASIN: <b>{asin}</b>\n"
            f"Hedef: {target_price} TL\n"
            f"Süre: {days} gün\n"
            f"Link: {url}",
            main_menu()
        )

    elif data == "add_search":
        set_state("waiting_search_keyword", {})
        tg_send(
            "🔎 Takip etmek istediğin ürünü yaz.\n\n"
            "Örnek:\n"
            "DualSense 5\n"
            "AirPods Pro 2\n"
            "LEGO 77256"
        )

    elif data == "list_searches":
        tg_send(list_searches_message(), main_menu())

    elif data == "delete_search_menu":
        tg_send("Silmek istediğin arama takibine bas:", delete_search_keyboard())

    elif data.startswith("delete_search_"):
        index = data.replace("delete_search_", "")
        delete_search(index)
        tg_send("✅ Arama takibi silindi.", main_menu())

    elif data.startswith("search_days_"):
        days = int(data.replace("search_days_", ""))
        search_data = get_state().get("data", {})
        keyword = search_data.get("keyword")

        if not keyword:
            tg_send("❌ Arama kelimesi bulunamadı. Yeniden dene.", main_menu())
            clear_state()
            return

        add_search_to_csv(keyword, days)
        clear_state()

        tg_send(
            f"✅ Arama takibi eklendi.\n\n"
            f"🔎 Kelime: <b>{keyword}</b>\n"
            f"⏱ Süre: {days} gün\n\n"
            f"Her gün 09:00, 14:00 ve 21:00 civarı en ucuz 10 sonucu göndereceğim.",
            main_menu()
        )

    elif data == "check_now":
        tg_send("🔄 ASIN fiyat kontrolü başlatıldı.")
        run_price_check()
        tg_send("✅ ASIN kontrol tamamlandı.", main_menu())

    elif data == "check_search_now":
        tg_send("🔍 Amazon arama kontrolleri başlatıldı.")
        run_search_reports(force=True)

    elif data == "help":
        tg_send(
            "ℹ️ <b>Fırsat Radar Yardım</b>\n\n"
            "➕ ASIN Ürün Ekle: Tek ürün sayfasını takip eder\n"
            "🔎 Arama Takibi Ekle: Amazon’da arama yapar, en ucuz 10 sonucu yollar\n"
            "📋 ASIN Listem: Tekil ürünleri gösterir\n"
            "📊 Arama Takiplerim: Arama kayıtlarını gösterir\n"
            "❌ / 🗑 Sil: Takibi kapatır\n"
            "🔄 Şimdi Kontrol Et: Anında kontrol yapar",
            main_menu()
        )


def poll_telegram():
    global last_update_id

    if not TELEGRAM_TOKEN:
        print("Telegram token yok.", flush=True)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    params = {
        "offset": last_update_id + 1,
        "timeout": 1
    }

    try:
        response = requests.get(url, params=params, timeout=5)
        updates = response.json().get("result", [])
    except Exception as e:
        print(f"Telegram okuma hatası: {e}", flush=True)
        return

    for update in updates:
        last_update_id = update["update_id"]

        if "message" in update:
            text = update["message"].get("text", "")
            handle_text(text)

        if "callback_query" in update:
            handle_callback(update["callback_query"])


if __name__ == "__main__":
    print("BOT BAŞLADI 🚀", flush=True)
    ensure_files()

    tg_send("🚀 Fırsat Radar aktif.\n\nMenüden işlem seçebilirsin:", main_menu())

    while True:
        poll_telegram()

        now_time = time.time()

        if now_time - last_asin_check_time >= ASIN_CHECK_INTERVAL_SECONDS:
            try:
                run_price_check()
            except Exception as e:
                print(f"ASIN fiyat kontrol hatası: {e}", flush=True)
                tg_send(f"⚠️ ASIN fiyat kontrol hatası:\n{e}")

            last_asin_check_time = now_time

        try:
            run_search_reports(force=False)
        except Exception as e:
            print(f"Arama raporu hatası: {e}", flush=True)
            tg_send(f"⚠️ Arama raporu hatası:\n{e}")

        time.sleep(TELEGRAM_POLL_SECONDS)
