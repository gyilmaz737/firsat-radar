import os
import re
import json
import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By


PRODUCTS_FILE = "products.csv"
HISTORY_FILE = "price_history.csv"
STATE_FILE = "telegram_state.json"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CHECK_INTERVAL_SECONDS = 1800
TELEGRAM_POLL_SECONDS = 3

last_update_id = 0
last_price_check_time = 0


def ensure_files():
    if not os.path.exists(PRODUCTS_FILE):
        df = pd.DataFrame(columns=[
            "name", "url", "asin", "start_date", "end_date",
            "target_price", "is_active"
        ])
        df.to_csv(PRODUCTS_FILE, index=False)

    if not os.path.exists(STATE_FILE):
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f, ensure_ascii=False)


def tg_send(text, keyboard=None):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram bilgileri eksik.", flush=True)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }

    if keyboard:
        data["reply_markup"] = json.dumps(keyboard, ensure_ascii=False)

    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"Telegram gönderim hatası: {e}", flush=True)


def tg_answer_callback(callback_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
    try:
        requests.post(url, data={"callback_query_id": callback_id}, timeout=10)
    except Exception:
        pass


def main_menu():
    return {
        "inline_keyboard": [
            [
                {"text": "➕ Ürün Ekle", "callback_data": "add_product"},
                {"text": "📋 Takip Listem", "callback_data": "list_products"}
            ],
            [
                {"text": "❌ Ürün Sil", "callback_data": "delete_menu"},
                {"text": "🔄 Şimdi Kontrol Et", "callback_data": "check_now"}
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


def add_product_to_csv(name, url, asin, target_price, days):
    ensure_files()

    df = pd.read_csv(PRODUCTS_FILE)

    start_date = datetime.now().date()
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


def list_products_message():
    ensure_files()
    df = pd.read_csv(PRODUCTS_FILE)

    active_df = df[df["is_active"] == 1]

    if active_df.empty:
        return "📭 Takip listende aktif ürün yok."

    msg = "📋 <b>Takip Listem</b>\n\n"

    for i, row in active_df.reset_index().iterrows():
        msg += (
            f"{i + 1}. <b>{row['name']}</b>\n"
            f"🎯 Hedef: {row['target_price']} TL\n"
            f"📅 Bitiş: {row['end_date']}\n"
            f"🔗 {row['url']}\n\n"
        )

    return msg


def delete_keyboard():
    ensure_files()
    df = pd.read_csv(PRODUCTS_FILE)
    active_df = df[df["is_active"] == 1].reset_index()

    buttons = []

    for i, row in active_df.iterrows():
        buttons.append([
            {
                "text": f"❌ {i + 1}. {row['name']}",
                "callback_data": f"delete_{row['index']}"
            }
        ])

    buttons.append([{"text": "⬅️ Ana Menü", "callback_data": "menu"}])

    return {"inline_keyboard": buttons}


def delete_product(index):
    df = pd.read_csv(PRODUCTS_FILE)
    df.loc[int(index), "is_active"] = 0
    df.to_csv(PRODUCTS_FILE, index=False)


def run_price_check():
    print("Kontrol çalışıyor...", flush=True)
    ensure_files()

    df = pd.read_csv(PRODUCTS_FILE)

    if df.empty:
        print("Ürün listesi boş.", flush=True)
        return

    now = datetime.now()
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

    print("30 dakika bekleniyor...", flush=True)


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
            f"✅ Ürün linki alındı.\n\nASIN: <b>{asin}</b>\n\nŞimdi hedef fiyatı yaz.\nÖrnek: 75"
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

        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "3 gün", "callback_data": "days_3"},
                    {"text": "7 gün", "callback_data": "days_7"}
                ],
                [
                    {"text": "15 gün", "callback_data": "days_15"},
                    {"text": "30 gün", "callback_data": "days_30"}
                ],
                [
                    {"text": "60 gün", "callback_data": "days_60"}
                ]
            ]
        }

        tg_send("⏱ Kaç gün takip edeyim?", keyboard)
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
        tg_send("Silmek istediğin ürüne bas:", delete_keyboard())

    elif data.startswith("delete_"):
        index = data.replace("delete_", "")
        delete_product(index)
        tg_send("✅ Ürün takip listesinden çıkarıldı.", main_menu())

    elif data.startswith("days_"):
        days = int(data.replace("days_", ""))
        user_state = get_state()
        product_data = user_state.get("data", {})

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
            f"✅ Ürün eklendi.\n\n"
            f"ASIN: <b>{asin}</b>\n"
            f"Hedef: {target_price} TL\n"
            f"Süre: {days} gün\n"
            f"Link: {url}",
            main_menu()
        )

    elif data == "check_now":
        tg_send("🔄 Kontrol başlatıldı. Birazdan sonucu bildiririm.")
        run_price_check()
        tg_send("✅ Kontrol tamamlandı.", main_menu())

    elif data == "help":
        tg_send(
            "ℹ️ <b>Fırsat Radar Yardım</b>\n\n"
            "➕ Ürün Ekle: Amazon linki ekler\n"
            "📋 Takip Listem: Aktif ürünleri gösterir\n"
            "❌ Ürün Sil: Takibi kapatır\n"
            "🔄 Şimdi Kontrol Et: Anında fiyat kontrolü yapar",
            main_menu()
        )


def poll_telegram():
    global last_update_id

    if not TELEGRAM_TOKEN:
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

        now = time.time()
        if now - last_price_check_time >= CHECK_INTERVAL_SECONDS:
            try:
                run_price_check()
            except Exception as e:
                print(f"Fiyat kontrol hatası: {e}", flush=True)
                tg_send(f"⚠️ Fiyat kontrol hatası:\n{e}")

            last_price_check_time = now

        time.sleep(TELEGRAM_POLL_SECONDS)
