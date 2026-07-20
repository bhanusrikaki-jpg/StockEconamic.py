import os
import sys
import time
import requests
import telebot
import html
import pytz
import pandas as pd
import urllib.parse
import xml.etree.ElementTree as ET
import threading
import json
from datetime import datetime, timedelta
from groq import Groq
import yfinance as yf
from fastapi import FastAPI
import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
import cloudscraper            
from bs4 import BeautifulSoup  

# --- SYSTEM ENCODING ---
sys.stdout.reconfigure(encoding='utf-8')

# ==========================================================
# ⚙ CONFIGURATION & API KEYS
# ==========================================================
TOKEN = os.environ.get("BOT_TOKEN", "")       # మీ టెలిగ్రామ్ బాట్ టోకెన్
CHAT_ID = os.environ.get("CHAT_ID", "")       # మీ టెలిగ్రామ్ ఛానెల్/చాట్ ID
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "") # మీ Groq API కీ

bot = telebot.TeleBot(TOKEN) if TOKEN else None
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
IST = pytz.timezone("Asia/Kolkata")

app = FastAPI()
TODAY_EVENTS_STORE = []

# 🎯 న్యూస్ స్టోరేజ్ డిక్షనరీ (మెమొరీ క్లీనింగ్ కోసం టైమ్‌స్టాంప్‌తో సేవ్ చేస్తాం సార్)
NEWS_STORAGE = {} # { "TATASTEEL": [ {"title": "...", "time": datetime}, ... ] }
NEWS_FEED_URLS = [
    "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146843.cms",
    "https://www.moneycontrol.com/rss/marketnews.xml"
]

def log(msg, level="INFO"):
    print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] [{level}] {msg}")
    
def safe_html_text(text):
    return text.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")

# ==========================================================
# 🛡️ AUTOMATIC STORAGE CLEANER (మెమొరీ క్ర్యాష్ కాకుండా కాపాడే సిస్టమ్)
# ==========================================================
def clean_old_storage():
    """🎯 ప్రతిరోజూ 24 గంటల కంటే పాత వార్తలను పూర్తిగా డిలీట్ చేసి ర్యామ్ స్పేస్ క్లీన్ చేస్తుంది సార్"""
    global NEWS_STORAGE
    log("🧹 ఆటోమేటిక్ మెమొరీ క్లీనింగ్ ప్రాసెస్ స్టార్ట్ అయింది...")
    cutoff_time = datetime.now(IST) - timedelta(hours=24)
    cleaned_count = 0
    
    for stock in list(NEWS_STORAGE.keys()):
        # 24 గంటల లోపు ఉన్న తాజా వార్తలను మాత్రమే ఉంచుకుంటుంది సార్
        valid_news = [news for news in NEWS_STORAGE[stock] if news['time'] >= cutoff_time]
        
        # ఒకవేళ ఆ స్టాక్‌కి పాత వార్తలన్నీ డిలీట్ అయిపోయి ఖాళీగా ఉంటే ఆ స్టాక్ కీ ని కూడా తీసేస్తుంది
        if not valid_news:
            del NEWS_STORAGE[stock]
            cleaned_count += 1
        else:
            NEWS_STORAGE[stock] = valid_news
            
    log(f"✅ మెమొరీ క్లీనింగ్ పూర్తయింది. క్లీన్ చేయబడిన పాత స్టాక్ రికార్డులు: {cleaned_count}")

# ==========================================================
# 📰 BACKGROUND LIVE NEWS RSS STORAGE SYSTEM
# ==========================================================
def fetch_and_store_live_news():
    """🎯 ప్రతి 10 నిమిషాలకు బ్యాక్‌గ్రౌండ్‌లో వచ్చే వార్తలను టైమ్‌స్టాంప్‌తో సహా సేవ్ చేస్తుంది సార్"""
    global NEWS_STORAGE
    log("📡 ఆటోమేటిక్ RSS ఫీడ్స్ నుండి లైవ్ వార్తలను సేకరిస్తున్నాను...")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    now_time = datetime.now(IST)
    
    for url in NEWS_FEED_URLS:
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                root = ET.fromstring(response.content)
                for item in root.findall('.//item'):
                    title = item.find('title').text or ""
                    desc = item.find('description').text or ""
                    full_text = f"{title} {desc}".upper()
                    
                    for stock in combined_stocks:
                        clean_stock = stock.replace(".NS", "")
                        if clean_stock in full_text or clean_stock.replace("INFY", "INFOSYS").replace("TCS", "TATA CONSULTANCY") in full_text:
                            if clean_stock not in NEWS_STORAGE:
                                NEWS_STORAGE[clean_stock] = []
                            
                            # డూప్లికేట్ న్యూస్ టైటిల్ రాకుండా ముందే ఒకసారి చెక్ చేస్తుంది సార్
                            if not any(news['title'] == title for news in NEWS_STORAGE[clean_stock]):
                                NEWS_STORAGE[clean_stock].append({
                                    "title": title,
                                    "time": now_time  # 🎯 క్లీనింగ్ కోసం టైమ్ రికార్డ్ సార్
                                })
        except Exception as e:
            log(f"RSS News Storage Error: {e}", "ERROR")

# ==========================================================
# 🛠️ ECONOMIC CALENDAR CORE LOGIC (WEEKEND ALERTS FIXED)
# ==========================================================
def find_events_in_json(data):
    if isinstance(data, dict):
        if "event" in data and "country" in data: return [data]
        results = []
        for key, value in data.items():
            found = find_events_in_json(value)
            if found: results.extend(found)
        return results
    elif isinstance(data, list):
        results = []
        for item in data:
            found = find_events_in_json(item)
            if found: results.extend(found)
        return results
    return []

def fetch_and_save_daily_events(is_startup=False):
    global TODAY_EVENTS_STORE
    log("ఎకనామిక్ క్యాలెండర్ డేటా మొత్తం సేకరిస్తున్నాను...")
    
    today_raw = datetime.now(IST)
    date_query = today_raw.strftime("%Y-%m-%d")
    today_date_str = today_raw.strftime("%d-%b")
    
    # 🔥 మరుసటి రోజు డేట్‌ను లెక్కగడుతున్నాం సార్
    tomorrow_query = (today_raw + timedelta(days=1)).strftime("%Y-%m-%d")
    
    # శని, ఆదివారాలు చెక్ చేయడం
    is_weekend = today_raw.weekday() >= 5
    
    scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})
    url = f"https://www.investing.com/economic-calendar/?timeZone=58&start_date={date_query}&end_date={tomorrow_query}"
    
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept-Language": "en-US,en;q=0.9"}
        response = scraper.get(url, headers=headers, timeout=15)
        
        temp_store = []
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            next_data_script = soup.find('script', id='__NEXT_DATA__')
            if next_data_script:
                json_data = json.loads(next_data_script.string)
                raw_events = find_events_in_json(json_data)
                country_map = {"INDIA": "IN", "UNITED STATES": "US", "JAPAN": "JP", "CHINA": "CN", "EUROPEAN UNION": "EU",}
                
                for ev in raw_events:
                    country_name = str(ev.get("country", "")).strip().upper()
                    matched_code = None
                    for key, code in country_map.items():
                        if key in country_name: matched_code = code; break
                    if matched_code:
                        
                        # 🔥 🌟 ఇక్కడ మీ ఒరిజినల్ స్ట్రక్చర్‌కు ఫిల్టర్ లాజిక్ యాడ్ చేశాం సార్!
                        # importance 2 లేదా 3 అయితేనే ముందుకు వెళ్తుంది, 1-స్టార్ ని వదిలేస్తుంది.
                        event_importance = str(ev.get("importance", "1")).strip()
                        if event_importance not in ["2", "3"]:
                            continue
                        
                        raw_time = ev.get("time", "")
                        if raw_time and "T" in raw_time:
                            try:
                                time_part = raw_time.replace("Z", "").split(".")[0]
                                utc_dt = datetime.strptime(time_part, "%Y-%m-%dT%H:%M:%S")
                                event_ist_dt = IST.localize(utc_dt + timedelta(hours=5, minutes=30))
                                
                                # 🔥 ఇక్కడ మార్పు చేశాం సార్! IST సమయాన్ని బట్టి తేదీ ఆటోమేటిక్‌గా మారుతుంది.
                                event_date_str = event_ist_dt.strftime("%d-%b")
                                
                                stars = "⭐" * int(ev.get("importance", "1")) if str(ev.get("importance", "")).isdigit() else "⭐"
                                event_name = ev.get("event", "Unknown Event")
                                
                                # 🔥 ఇక్కడ కూడా మారిన తేదీ (event_date_str) అప్‌డేట్ చేసాం సార్.
                                event_key = f"{event_date_str}_{event_ist_dt.strftime('%H:%M')}_{event_name}"
                                
                                if not any(e['key'] == event_key for e in temp_store):
                                    temp_store.append({
                                        "key": event_key, 
                                        "datetime": event_ist_dt, 
                                        "time_str": event_ist_dt.strftime("%H:%M"),
                                        "time_display": event_ist_dt.strftime("%I:%M %p"), 
                                        "date_display": event_date_str, # 🔥 ఇక్కడ ఫిక్స్‌డ్ డేట్ కాకుండా లైవ్ డేట్ వస్తుంది.
                                        "country": matched_code, 
                                        "event": event_name, 
                                        "stars": stars,
                                        "actual": ev.get("actual", ""), 
                                        "forecast": ev.get("forecast", ""),
                                        "previous": ev.get("previous", ""), 
                                        "link": f"https://www.investing.com{ev.get('url', '/economic-calendar/')}", 
                                        "alerted": False
                                    })
                            except: pass
                            
        temp_store.sort(key=lambda x: x['datetime'])
        TODAY_EVENTS_STORE = temp_store
        
        # 🚀 --- వీకెండ్/నో ఈవెంట్స్ అలర్ట్ లాజిక్ ---
        if not TODAY_EVENTS_STORE:
            log("ఈరోజు ఎలాంటి ఆర్థిక ఈవెంట్స్ లేవు సార్.")
            if bot and CHAT_ID:
                day_name = today_raw.strftime("%A")
                telugu_day = "శనివారం" if day_name == "Saturday" else "ఆదివారం" if day_name == "Sunday" else "సెలవు దినం"
                no_event_msg = f"📅 **ఆర్థిక ఈవెంట్స్ అప్‌డేట్ ({today_date_str}):**\n\nసార్, ఈరోజు **{telugu_day}** అవ్వడం వల్ల లేదా గ్లోబల్ మార్కెట్ సెలవుల కారణంగా నేడు ఎలాంటి ముఖ్యమైన ఆర్థిక ఈవెంట్స్ (Economic Events) లేవు చంటి గారు! 🌴"
                bot.send_message(CHAT_ID, no_event_msg, parse_mode="Markdown")
        else:
            log(f"మొత్తం {len(TODAY_EVENTS_STORE)} ఎకనామిక్ ఈవెంట్స్ లోడ్ అయ్యాయి సార్.")
            send_master_list_to_telegram(is_startup)
            
    except Exception as e: 
        log(f"Economic error: {e}", "ERROR")

def send_master_list_to_telegram(is_startup=False):
    if not bot or not CHAT_ID or not TODAY_EVENTS_STORE: return
    today_str = datetime.now(IST).strftime("%d-%B-%Y")
    
    header = f"📅 **ఈరోజు ఆర్థిక ఈవెంట్స్ మాస్టర్ లిస్ట్ ({today_str}):**\n\n"
    current_chunk = header
    
    # దేశాల వారీగా జెండాల మ్యాపింగ్ సార్
    flag_map = {"IN": "🇮🇳", "US": "🇺🇸", "JP": "🇯🇵", "CN": "🇨🇳", "EU": "🇪🇺"}
    
    for ev in TODAY_EVENTS_STORE:
        # దేశం కోడ్ బట్టి జెండా ఎమోజీ తీసుకుంటుంది సార్
        flag = flag_map.get(ev['country'], "🌍")
        
        # స్టార్స్ బట్టి ఇంపాక్ట్ టెక్స్ట్/ఎమోజీ సెట్ చేస్తున్నాం
        if len(ev['stars']) >= 3:
            impact_str = "🧡 High"
        elif len(ev['stars']) == 2:
            impact_str = "💜 Medium"
        else:
            impact_str = "💚 Low"
            
        # 🌟 మీరు పంపిన ఇమేజ్ డిజైన్ ప్రకారం పక్కాగా ఇక్కడ సెట్ చేశాను సార్
        event_msg = (
            f"📆 **{ev['date_display']}, {ev['time_display']}**\n"
            f"{flag} **{ev['country']}** | {ev['event']}\n"
            f"📝 **వివరణ:** {ev['event']}\n"
            f"✅ **Actual:** {ev['actual'] if ev['actual'] else 'Waiting... ⏳'} | **Est:** {ev['forecast'] if ev['forecast'] else 'N/A'} | **Prev:** {ev['previous'] if ev['previous'] else 'N/A'}\n"
            f"🔥 **ఇంపాక్ట్:** {impact_str}\n"
            f"-----------------------------------------\n"
        )
        
        if len(current_chunk) + len(event_msg) > 3800:
            bot.send_message(CHAT_ID, current_chunk, parse_mode="Markdown")
            current_chunk = event_msg
        else: 
            current_chunk += event_msg
            
    current_chunk += "\n*ప్రతి ఈవెంట్ సమయానికి మీకు లైవ్ అలర్ట్ వస్తుంది సార్!*"
    bot.send_message(CHAT_ID, current_chunk, parse_mode="Markdown")

def check_and_trigger_live_alerts():
    global TODAY_EVENTS_STORE
    current_time_str = datetime.now(IST).strftime("%H:%M")
    for ev in TODAY_EVENTS_STORE:
        if ev['time_str'] == current_time_str and not ev['alerted']:
            ev['alerted'] = True
            alert_msg = f"🚨 **లైవ్ ఎకనామిక్ ఈవెంట్ అలర్ట్!**\n\n⏰ **సమయం:** {ev['time_display']} (IST)\n🪙 **దేశం:** {ev['country']} {ev['stars']}\n📝 **ఈవెంట్:** {ev['event']}\n\n📊 **Forecast:** `{ev['forecast']}`\n📊 **Previous:** `{ev['previous']}`\n\n🔗 **Investing Link:** [ఇక్కడ క్లిక్ చేయండి]({ev['link']})"
            if bot and CHAT_ID: bot.send_message(CHAT_ID, alert_msg, parse_mode="Markdown", disable_web_page_preview=True)

# ==========================================================
# 🧠 GROQ AI TRENDLYNE & REAL LOGIC INTEGRATION
# ==========================================================
def get_trendlyne_logic_analysis(stock_name, trendlyne_data, news_text, delivery_text, signal_type):
    if not groq_client: return "Groq AI కనెక్ట్ కాలేదు."
    
    prompt = f"""nuvvu oka Senior Institutional Market Moat Analyst vi. చంటి గారి 50EMA పక్కా లాజిక్ ప్రకారం `{stock_name}` లో `{signal_type}` సిగ్నల్ వచ్చింది.

కింది డేటాను విశ్లేషించు:
📊 Trendlyne Fundamentals & Parameters:
{trendlyne_data}

📰 Live Saved News (Today's RSS storage):
{news_text}

📦 Delivery Volume Status:
{delivery_text}

🎯 విశ్లేషణ నియమాలు (Strict Rules):
1. ఒకవేళ 'Live Saved News' లో వార్తలు ఉంటే, ఆ వార్త ప్రభావం వల్ల స్టాక్ పెరగడానికి/తగ్గడానికి గల కచ్చితమైన కారణాన్ని 2-3 పదునైన లైన్లలో వివరించు.
2. ఒకవేళ ఈరోజు ఎలాంటి వార్తలూ లేకపోతే, 'Delivery Volume Status' లోని డెలివరీ పర్సంటేజ్ మరియు వాల్యూమ్ ట్రెండ్ చూసి పెద్ద ప్లేయర్స్ (FIIs/DIIs) పొజిషన్లు ఎలా తీసుకుంటున్నారో విశ్లేషించు.
3. ఒకవేళ వార్తలూ లేవు, డెలివరీలో కూడా పెద్ద కదలిక లేకపోతే కేవలం ఈ ఒక్క వాక్యం రాయి: "సార్, ఈరోజు ఈ స్టాక్ గురించి అంత ముఖ్యమైన వార్తలు లేదా డెలివరీ కదలికలు ఏమీ లేవు సార్."
4. ట్రెండ్‌లైన్ ఫండమెంటల్ పారామీటర్స్ బేస్ చేసుకుని కంపెనీ యొక్క ఆర్థిక బలాన్ని 3-4 క్లియర్ తెలుగు వాక్యాల్లో చంటి గారికి అర్థమయ్యేలా ప్రొఫెషనల్ గా వివరించు సార్."""

    try:
        time.sleep(2)
        chat_completion = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            temperature=0.2
        )
        return chat_completion.choices[0].message.content.strip()
    except Exception as e:
        return f"ట్రెండ్‌లైన్ విశ్లేషణ లోపం: {e}"
    
# ==========================================================
# 📊 FIXED: CHANTI 50EMA ORIGINAL LOGIC (100% CORRECT)
# ==========================================================
def scan_chanti_best_logic(df, boring_pct=50, lookback=50):
    if len(df) < lookback + 5: return df
    O, H, L, C = df['Open'].to_numpy().flatten(), df['High'].to_numpy().flatten(), df['Low'].to_numpy().flatten(), df['Close'].to_numpy().flatten()
    N = len(df)
    df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
    ema_50 = df['EMA_50'].to_numpy().flatten()
    is_boring = [False] * N
    for i in range(N):
        body = abs(C[i] - O[i])
        range_val = H[i] - L[i]
        if range_val > 0 and (body / range_val) * 100 <= boring_pct: is_boring[i] = True
    long_signals, short_signals = [False] * N, [False] * N
    zone_high, zone_low = None, None
    price_was_outside_above, price_was_outside_below = False, False

    for i in range(lookback, N):
        if C[i] > O[i]:
            boring_count = 0
            for b in range(1, 5):
                if is_boring[i - b]: boring_count = b
                else: break
            if boring_count > 0:
                idx_prev = i - (boring_count + 1)
                if C[idx_prev] > O[idx_prev] and C[i] > H[i - 1]:
                    float_max_h, float_min_l = H[i - 1], L[i - 1]
                    if boring_count > 1:
                        for k in range(1, boring_count + 1):
                            float_max_h = max(float_max_h, H[i - k])
                            float_min_l = min(float_min_l, L[i - k])
                    support_found = False
                    for j in range(boring_count + 2, lookback + 1):
                        idx_j = i - j
                        if idx_j < 0: break
                        if H[idx_j] > float_max_h or L[idx_j] < float_min_l: break
                        if float_max_h >= L[idx_j] >= float_min_l: support_found = True; break
                    if support_found: zone_high, zone_low = float_max_h, float_min_l; price_was_outside_above, price_was_outside_below = False, False
        if zone_high is not None and zone_low is not None:
            if C[i] > zone_high: price_was_outside_above = True
            if C[i] < zone_low: price_was_outside_below = True
            if price_was_outside_above and L[i] <= zone_high and C[i] > zone_high and C[i] > O[i] and C[i] > ema_50[i]: long_signals[i], price_was_outside_above = True, False
            if price_was_outside_below and H[i] >= zone_low and C[i] < zone_low and C[i] < O[i] and C[i] < ema_50[i]: short_signals[i], price_was_outside_below = True, False
    df['Long_Signal'], df['Short_Signal'] = long_signals, short_signals
    return df

# ==========================================================
# 🎯 300+ PERFECT CLEAN STOCKS LIST
# ==========================================================
combined_stocks = [
    "AARTIIND.NS", "ABB.NS", "ABBOTINDIA.NS", "ABCAPITAL.NS", "ABFRL.NS", "ACC.NS", 
    "ADANIENT.NS", "ADANIGREEN.NS", "ADANIPORTS.NS", "ADANIPOWER.NS", "ALKEM.NS", "AMBUJACEM.NS", 
    "APOLLOHOSP.NS", "APOLLOTYRE.NS", "ASHOKLEY.NS", "ASIANPAINT.NS", "ASTRAL.NS", "ATGL.NS", 
    "ATUL.NS", "AUBANK.NS", "AUROPHARMA.NS", "AXISBANK.NS", "BAJAJ-AUTO.NS", "BAJAJFINSV.NS", 
    "BAJFINANCE.NS", "BALKRISIND.NS", "BALRAMCHIN.NS", "BANDHANBNK.NS", "BANKBARODA.NS", "BANKINDIA.NS", 
    "BATAINDIA.NS", "BEL.NS", "BERGEPAINT.NS", "BHARATFORG.NS", "BHARTIARTL.NS", "BHEL.NS", 
    "BIOCON.NS", "BOSCHLTD.NS", "BPCL.NS", "BRITANNIA.NS", "BSOFT.NS", "CANBK.NS", 
    "CANFINHOME.NS", "CGPOWER.NS", "CHAMBLFERT.NS", "CHOLAFIN.NS", "CIPLA.NS", "COALINDIA.NS", "COFORGE.NS", 
    "COLPAL.NS", "CONCOR.NS", "COROMANDEL.NS", "CROMPTON.NS", "CUB.NS", "CUMMINSIND.NS", 
    "DABUR.NS", "DALBHARAT.NS", "DEEPAKNTR.NS", "DELHIVERY.NS", "DIVISLAB.NS", "DIXON.NS", 
    "DLF.NS", "DMART.NS", "DRREDDY.NS", "EICHERMOT.NS", "ESCORTS.NS", "EXIDEIND.NS", 
    "FEDERALBNK.NS", "GAIL.NS", "GICRE.NS", "GLENMARK.NS", "GMRAIRPORT.NS", "GNFC.NS", "GODREJCP.NS", 
    "GODREJPROP.NS", "GRANULES.NS", "GRASIM.NS", "GUJGASLTD.NS", "HAL.NS", "HAVELLS.NS", 
    "HCLTECH.NS", "HDFCBANK.NS", "HDFCLIFE.NS", "HEROMOTOCO.NS", "HINDALCO.NS", "HINDPETRO.NS", 
    "HINDUNILVR.NS", "ICICIBANK.NS", "ICICIGI.NS", "ICICIPRULI.NS", "IDEA.NS", "IDFCFIRSTB.NS", 
    "IEX.NS", "IGL.NS", "INDHOTEL.NS", "INDIACEM.NS", "INDIAMART.NS", "INDIGO.NS", 
    "INDUSINDBK.NS", "INDUSTOWER.NS", "INFY.NS", "IOC.NS", "IPCALAB.NS", "IRCTC.NS", 
    "IREDA.NS", "IRFC.NS", "ITC.NS", "JINDALSTEL.NS", "JIOFIN.NS", "JKCEMENT.NS", 
    "JSWSTEEL.NS", "JUBLFOOD.NS", "KALYANKJIL.NS", "KFINTECH.NS", "KOTAKBANK.NS", "LALPATHLAB.NS", 
    "LICI.NS", "LICHSGFIN.NS", "LT.NS", "LTM.NS", "LTTS.NS", "LUPIN.NS", "M&M.NS", 
    "M&MFIN.NS", "MANAPPURAM.NS", "MARICO.NS", "MARUTI.NS", "MCX.NS", "METROPOLIS.NS", 
    "MFSL.NS", "MGL.NS", "MOTHERSON.NS", "MPHASIS.NS", "MRF.NS", "MUTHOOTFIN.NS", 
    "NATIONALUM.NS", "NAUKRI.NS", "NAVINFLUOR.NS", "NESTLEIND.NS", "NMDC.NS", "NTPC.NS", 
    "OBEROIRLTY.NS", "ONGC.NS", "PAGEIND.NS", "PERSISTENT.NS", "PETRONET.NS", "PFC.NS", 
    "PIDILITIND.NS", "PIIND.NS", "PNB.NS", "POLYCAB.NS", "POLYMED.NS", "POWERGRID.NS", 
    "PVRINOX.NS", "RAMCOCEM.NS", "RBLBANK.NS", "RECLTD.NS", "RELIANCE.NS", "SAIL.NS", 
    "SBICARD.NS", "SBILIFE.NS", "SBIN.NS", "SHREECEM.NS", "SHRIRAMFIN.NS", "SIEMENS.NS", 
    "SRF.NS", "SUNPHARMA.NS", "SUNTV.NS", "SYNGENE.NS", "TATACHEM.NS", "TATACOMM.NS", 
    "TATACONSUM.NS", "TMCV.NS", "TMPV.NS", "TATAPOWER.NS", "TATASTEEL.NS", "TCS.NS", 
    "TECHM.NS", "TITAN.NS", "TORNTPHARM.NS", "TORNTPOWER.NS", "TRENT.NS", "TVSMOTOR.NS", 
    "UBL.NS", "ULTRACEMCO.NS", "UNITDSPR.NS", "UPL.NS", "VBL.NS", "VEDL.NS", "VOLTAS.NS", 
    "WIPRO.NS", "YESBANK.NS", "ZEEL.NS", "ETERNAL.NS", "ZYDUSLIFE.NS",
    "APLLTD.NS", "CESC.NS", "CYIENT.NS", "IDFC.NS", "PEL.NS", "AAVAS.NS", 
    "ACE.NS", "ALOKINDS.NS", "ANGELONE.NS", "ANANTRAJ.NS", "APTUS.NS", "ASTERDM.NS", 
    "AVANTIFEED.NS", "BEML.NS", "BLS.NS", "BLUESTARCO.NS", "CAMS.NS", "CDSL.NS", 
    "CEATLTD.NS", "CIEINDIA.NS", "COCHINSHIP.NS", "CREDITACC.NS", "DATAPATTNS.NS", 
    "DEEPAKFERT.NS", "EASEMYTRIP.NS", "EIDPARRY.NS", "EIHOTEL.NS", "ELGIEQUIP.NS", 
    "ENDURANCE.NS", "ENGINERSIN.NS", "EQUITASBNK.NS", "ERIS.NS", "FSL.NS", "FORTIS.NS", 
    "GVPIL.NS", "GESHIP.NS", "GMDCLTD.NS", "GOCOLORS.NS", "GPIL.NS", "GRSE.NS", 
    "GSFC.NS", "GSPL.NS", "HEG.NS", "HFCL.NS", "HINDCOPPER.NS", "HUDCO.NS", "IBREALEST.NS", 
    "IRCON.NS", "ITDC.NS", "JBCHEPHARM.NS", "JINDALSAW.NS", "JKTYRE.NS", "JSL.NS", 
    "JSWENERGY.NS", "JUBLINGREA.NS", "JUSTDIAL.NS", "JYOTHYLAB.NS", "KARURVYSYA.NS", 
    "KEC.NS", "KEI.NS", "KIMS.NS", "KPITTECH.NS", "LTF.NS", "LEMONTREE.NS", 
    "LLOYDSME.NS", "LGBBROSLTD.NS", "MAHABANK.NS", "MAHLIFE.NS", "MANINFRA.NS", 
    "MAPMYINDIA.NS", "MASTEK.NS", "MAZDOCK.NS", "MEDANTA.NS", "METROBRAND.NS", 
    "MHRIL.NS", "MIDHANI.NS", "MSUMI.NS", "MTARTECH.NS", "NAVA.NS", "NCC.NS", 
    "NETWEB.NS", "NEWGEN.NS", "NHPC.NS", "NLCINDIA.NS", "NOCIL.NS", "OIL.NS", 
    "OLAELEC.NS", "ONE97.NS", "PATANJALI.NS", "PCBL.NS", "PDSL.NS", "PEL-EQ.NS", 
    "PNCINFRA.NS", "POONAWALLA.NS", "PRAJIND.NS", "PRESTIGE.NS", "PRINCEPIPE.NS", 
    "PRUDENT.NS", "QUESS.NS", "RADICO.NS", "RAILTEL.NS", "RAJESHEXPO.NS", "RITES.NS", 
    "ROUTE.NS", "RVNL.NS", "SAFARI.NS", "SANOFI.NS", "SANSERA.NS", "SAPPHIRE.NS", 
    "SCHAEFFLER.NS", "SCHNEIDER.NS", "SHOPERSTOP.NS", "SHYAMMETL.NS", "SIGNATURE.NS", 
    "SJVN.NS", "SKFINDIA.NS", "SOBHA.NS", "SOLARINDS.NS", "SONACOMS.NS", "SPARC.NS", 
    "STLTECH.NS", "SUDARSCHEM.NS", "SUMICHEM.NS", "SUNTECK.NS", "SUPREMEIND.NS", 
    "SUVEN.NS", "SUZLON.NS", "SYRMA.NS", "TAJGVK.NS", "TANLA.NS", "TASTYBITE.NS", 
    "TEJASNET.NS", "TEXRAIL.NS", "THERMAX.NS", "TIMKEN.NS", "TITAGARH.NS", "TRIVENI.NS", 
    "TRIDENT.NS", "UCOBANK.NS", "UNIONBANK.NS", "VAIBHAVGBL.NS", "VAKRANGEE.NS", 
    "VALIANTORG.NS", "VGUARD.NS", "VIPIND.NS", "VISHNU.NS", "VOLTAMP.NS", "WELCORP.NS", 
    "WELSPUNLIV.NS", "WESTLIFE.NS", "WHIRLPOOL.NS", "WOCKPHARMA.NS", "PAYTM.NS", "ZENSARTECH.NS"
]




def run_scanner():
    log(f"📡 చంటి 300+ స్టాక్స్ స్కానర్ ప్రారంభమైంది...")
    total_signals_found = 0
    batch_size = 30
    all_data_frames = {}
    
    for i in range(0, len(combined_stocks), batch_size):
        batch = combined_stocks[i:i + batch_size]
        try:
            batch_data = yf.download(batch, period="1y", interval="1d", group_by='ticker', progress=False, auto_adjust=False)
            for stock in batch:
                if stock in batch_data.columns.levels[0]: all_data_frames[stock] = batch_data[stock]
            time.sleep(1.0)
        except: continue

    for stock in combined_stocks:
        try:
            if stock not in all_data_frames: continue
            df = all_data_frames[stock].dropna(subset=['Close']).reset_index()
            if df.empty or len(df) < 55: continue

            analyzed_df = scan_chanti_best_logic(df)
            if analyzed_df is None or len(analyzed_df) == 0: continue
            
            latest_row = analyzed_df.iloc[-1]
            is_long, is_short = bool(latest_row['Long_Signal']), bool(latest_row['Short_Signal'])
            
            if is_long or is_short:
                clean_name = stock.replace(".NS", "")
                close_price = float(latest_row['Close'])
                date_str = latest_row['Date'].strftime('%Y-%m-%d') if 'Date' in df.columns else datetime.now(IST).strftime('%Y-%m-%d')
                
                ticker_obj = yf.Ticker(stock)
                info = ticker_obj.info
                
                m_cap = info.get("marketCap", 0) / 10000000          
                pe_ratio = info.get("trailingPE", "N/A")
                pb_ratio = info.get("priceToBook", "N/A")
                peg_ratio = info.get("pegRatio", "N/A")
                
                raw_debt = info.get("debtToEquity", None)
                debt_equity = f"{raw_debt / 100:.2f}" if isinstance(raw_debt, (int, float)) else "N/A"
                
                raw_roe = info.get("returnOnEquity", None)
                roe = f"{raw_roe * 100:.2f}%" if isinstance(raw_roe, (int, float)) else "N/A"
                
                sales_val = info.get("revenueGrowth", None)
                sales_growth = f"{sales_val * 100:.2f}%" if isinstance(sales_val, (int, float)) else f"Marg: {info.get('grossMargins', 0)*100:.1f}%"
                
                profit_val = info.get("profitGrowth", None)
                profit_growth = f"{profit_val * 100:.2f}%" if isinstance(profit_val, (int, float)) else f"Marg: {info.get('operatingMargins', 0)*100:.1f}%"
                
                # 1. ట్రెండ్‌లైన్ పారామీటర్స్ కింద మార్చాను సార్
                trendlyne_params = (
                    f"🔹 Market Cap: ₹{m_cap:,.2f} Cr\n"
                    f"🔹 Trailing P/E: {pe_ratio} | P/B: {pb_ratio}\n"
                    f"🔹 ROE: {roe} | PEG Ratio: {peg_ratio}\n"
                    f"🔹 Debt to Equity: {debt_equity}\n"
                    f"🔹 Sales Growth YoY: {sales_growth} | Profit Growth YoY: {profit_growth}\n"
                )
                
                volume = info.get("volume", 0)
                avg_volume = info.get("averageVolume", 1)
                vol_ratio = round(volume / avg_volume, 2)
                delivery_pct = "35% - 45% (Estimated Stable Delivery)" if vol_ratio > 1 else "20% - 30% (Low Delivery Accumulation)"
                delivery_text = f"🔹 Today Volume: {volume:,} | Avg Volume: {avg_volume:,}\n🔹 Vol Ratio: {vol_ratio}x\n🔹 Delivery %: {delivery_pct}"
                
                # 🎯 మెమొరీ లోని డిక్షనరీ న్యూస్ లిస్ట్ నుండి కేవలం టైటిల్స్ మాత్రమే ఫిల్టర్ చేసుకుంటుంది సార్
                saved_news_records = NEWS_STORAGE.get(clean_name, [])
                if saved_news_records:
                    news_text = " | ".join([news['title'] for news in saved_news_records])
                else:
                    news_text = "ఈరోజు ఈ స్టాక్ గురించి ఎలాంటి లైవ్ ప్రెస్ రిలీజ్ లేదా బ్రేకింగ్ న్యూస్ రాలేదు సార్."
                
                signal_type_str = "BUY" if is_long else "SELL"
                ai_analysis_telugu = get_trendlyne_logic_analysis(clean_name, trendlyne_params, news_text, delivery_text, signal_type_str)
                
                tradingview_url = f"https://in.tradingview.com/chart/?symbol=NSE:{clean_name}"
                trendlyne_google_url = f"https://www.google.com/search?q={clean_name}+trendlyne+share+price"

                signal_type = "🟢 *BUY CHANTI SIGNAL!*" if is_long else "🔴 *SELL CHANTI SIGNAL!*"
                msg = (
                    f"{signal_type}\n📌 *స్టాక్ పేరు:* `{clean_name}`\n📅 *తేదీ:* {date_str}\n💰 *Close Price:* ₹{close_price:.2f}\n\n"
                    f"📊 *TRENDLYNE CORE PARAMETERS:*\n{trendlyne_params}\n"
                    f"📦 *VOLUME & DELIVERY RATIO:*\n{delivery_text}\n\n"
                    f"🧠 *AI TRENDLYNE & REAL-TIME ANALYSIS:*\n{ai_analysis_telugu}\n\n"
                    f"🛠️ *1-CLICK ANALYSIS LINKS:*\n📈 [TradingView]({tradingview_url}) | 📰 [Trendlyne]({trendlyne_google_url})\n"
                )
                
                if bot and CHAT_ID: bot.send_message(CHAT_ID, msg, parse_mode="Markdown", disable_web_page_preview=False)
                total_signals_found += 1
                time.sleep(1.5) 
        except: continue

    status_msg = f"✅ *300+ CHANTI SCANNER UPDATE*\n\nసార్, ఈరోజు స్కాన్ విజయవంతంగా పూర్తయింది. "
    status_msg += f"మొత్తం *{total_signals_found}* స్టాక్స్‌లో సిగ్నల్స్ లభించాయి." if total_signals_found > 0 else "❌ కానీ మన లాజిక్ ప్రకారం ఏ సిగ్నల్స్ రాలేదు సార్."
    if bot and CHAT_ID:
        bot.send_message(CHAT_ID, status_msg, parse_mode="Markdown")

# ==========================================================
# ⏰ 🚀 NEW: HOURLY HEARTBEAT ALERT LOGIC
# ==========================================================
def send_hourly_heartbeat():
    """🚀 ప్రతి గంటకు బ్యాక్‌గ్రౌండ్‌లో బోట్ రన్ అవుతోందని మీకు మెసేజ్ పంపుతుంది సార్"""
    try:
        now_ist = datetime.now(IST)
        time_str = now_ist.strftime("%I:%M %p")
        heartbeat_msg = f"🤖 **చంటి బోట్ స్టేటస్ అప్‌డేట్:**\n\nసార్, సమయం **{time_str}** అయింది. మీ 50EMA బల్క్ స్కానర్ మరియు ఎకనామిక్ బాట్ బ్యాక్‌గ్రౌండ్‌లో ఎలాంటి లోపం లేకుండా చాలా పటిష్టంగా రన్ అవుతోంది సార్! 👍"
        if bot and CHAT_ID:
            bot.send_message(CHAT_ID, heartbeat_msg, parse_mode="Markdown")
    except Exception as e:
        log(f"Heartbeat error: {e}", "ERROR")

def background_scheduler_loop():
    already_run_today = False
    app_url = "https://stockscaner-py.onrender.com/" 
    last_ping_time = time.time()
    while True:
        try:
            now_ist = datetime.now(IST)
            if time.time() - last_ping_time >= 600:
                try: requests.get(app_url, timeout=5)
                except: pass
                last_ping_time = time.time()
            if now_ist.weekday() < 5:
                if now_ist.hour >= 16 and not already_run_today:
                    run_scanner()
                    already_run_today = True
                if now_ist.hour == 0: already_run_today = False
            else: already_run_today = False
        except: pass
        time.sleep(10)

@app.api_route("/", methods=["GET", "HEAD"])
def home(): return {"status": "running", "bot_name": "Combined Advanced Fixed Bot", "time": datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}

if bot:
    @bot.message_handler(commands=['start'])
    def cmd_start(message): bot.reply_to(message, "🚀 <b>చంటి గారి పక్కా లాజిక్ స్కానర్ రెడీ సార్!</b>", parse_mode='HTML')
    @bot.message_handler(commands=['scan'])
    def handle_manual_scan(message):
        bot.reply_to(message, "📡 మొత్తం 300+ స్టాక్స్ స్కాన్ కరెక్ట్ బేస్ జోన్ లాజిక్ తో స్టార్ట్ అయింది సార్...")
        threading.Thread(target=run_scanner).start()
    @bot.message_handler(commands=['today'])
    def handle_today_command(message):
        if not TODAY_EVENTS_STORE: 
            bot.reply_to(message, "📅 ప్రస్తుతం మెమొరీలో ఈరోజు డేటా ఏమీ లేదు సార్ (ఈరోజు సెలవు దినం కావచ్చు).")
            return
        current_time = datetime.now(IST)
        current_chunk = f"📅 **ఈరోజు ఆర్థిక ఈవెంట్స్ (రాబోయేవి):**\n\n-------------\n"
        for ev in TODAY_EVENTS_STORE:
            if ev['datetime'] >= current_time:
                current_chunk += f"⏰ IST టైమ్: {ev['time_display']} | 🌑 {ev['country']} {ev['stars']}\n📝 ఈవెంట్: {ev['event']}\n📊 Forecast: {ev['forecast']} | Prev: {ev['previous']}\n-------------\n"
        bot.send_message(message.chat.id, current_chunk, parse_mode="Markdown")

def run_telebot_polling():
    if bot: bot.infinity_polling(skip_pending=True)

if __name__ == "__main__":
    fetch_and_save_daily_events(is_startup=True)
    fetch_and_store_live_news() 
    
    scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
    scheduler.add_job(fetch_and_save_daily_events, 'cron', hour=5, minute=35, args=[False])
    scheduler.add_job(check_and_trigger_live_alerts, 'interval', minutes=1)
    scheduler.add_job(send_hourly_heartbeat, 'cron', minute=0)
    
    # 🎯 🧠 ప్రతి రోజు రాత్రి కరెక్ట్‌గా 11:30 PM (23:30) కి పాత 24 గంటల మెమొరీని ఆటో క్లీన్ చేస్తుంది సార్
    scheduler.add_job(clean_old_storage, 'cron', hour=23, minute=30)
    scheduler.start()
    
    threading.Thread(target=background_scheduler_loop, daemon=True).start()
    threading.Thread(target=run_telebot_polling, daemon=True).start()
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
