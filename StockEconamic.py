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

def log(msg, level="INFO"):
    print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] [{level}] {msg}")

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
    
    # శని, ఆదివారాలు చెక్ చేయడం
    is_weekend = today_raw.weekday() >= 5
    
    scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})
    url = f"https://www.investing.com/economic-calendar/?timeZone=58&start_date={date_query}&end_date={date_query}"
    
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
                country_map = {"INDIA": "IN", "UNITED STATES": "US", "JAPAN": "JP", "CHINA": "CH/CN", "EURO ZONE": "EU"}
                
                for ev in raw_events:
                    country_name = str(ev.get("country", "")).strip().upper()
                    matched_code = None
                    for key, code in country_map.items():
                        if key in country_name: matched_code = code; break
                    if matched_code:
                        raw_time = ev.get("time", "")
                        if raw_time and "T" in raw_time:
                            try:
                                time_part = raw_time.replace("Z", "").split(".")[0]
                                utc_dt = datetime.strptime(time_part, "%Y-%m-%dT%H:%M:%S")
                                event_ist_dt = IST.localize(utc_dt + timedelta(hours=5, minutes=30))
                                
                                stars = "⭐" * int(ev.get("importance", "1")) if str(ev.get("importance", "")).isdigit() else "⭐"
                                event_name = ev.get("event", "Unknown Event")
                                event_key = f"{today_date_str}_{event_ist_dt.strftime('%H:%M')}_{event_name}"
                                
                                if not any(e['key'] == event_key for e in temp_store):
                                    temp_store.append({
                                        "key": event_key, "datetime": event_ist_dt, "time_str": event_ist_dt.strftime("%H:%M"),
                                        "time_display": event_ist_dt.strftime("%I:%M %p"), "date_display": today_date_str,
                                        "country": matched_code, "event": event_name, "stars": stars,
                                        "actual": ev.get("actual", ""), "forecast": ev.get("forecast", ""),
                                        "previous": ev.get("previous", ""), "link": f"https://www.investing.com{ev.get('url', '/economic-calendar/')}", "alerted": False
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
    current_chunk = header + "-----------------------------\n"
    for ev in TODAY_EVENTS_STORE:
        event_msg = f"📅 తేదీ: {ev['date_display']} | ⏰ IST టైమ్: {ev['time_display']} | 🌑 {ev['country']} {ev['stars']}\n📝 ఈవెంట్: {ev['event']}\n📊 Actual: {ev['actual']} | Forecast: {ev['forecast']} | Prev: {ev['previous']}\n-----------------------------\n"
        if len(current_chunk) + len(event_msg) > 3800:
            bot.send_message(CHAT_ID, current_chunk, parse_mode="Markdown")
            current_chunk = "-----------------------------\n" + event_msg
        else: current_chunk += event_msg
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
# 🤖 🚀 30-DAYS BROKER RESEARCH & TARGETS LOGIC
# ==========================================================
def fetch_broker_research_reports(stock_name):
    try:
        search_query = f"{stock_name} share analyst views brokerage upgrades downgrades target price target"
        encoded_query = urllib.parse.quote(search_query)
        rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en&tbs=qdr:m"
        
        response = requests.get(rss_url, timeout=10)
        headlines = []
        if response.status_code == 200:
            root = ET.fromstring(response.content)
            for item in root.findall('.//item')[:6]:  
                headlines.append(item.find('title').text)
        return " | ".join(headlines) if headlines else "గత 30 రోజుల్లో ఈ స్టాక్‌పై ఎలాంటి ప్రత్యేక బ్రోకర్ నివేదికలు లభించలేదు చంటి గారు."
    except:
        return "అనలిస్ట్ డేటా సేకరించడంలో లోపం జరిగింది."

def get_groq_ai_research_analysis(stock_name, fund_data, research_text):
    if not groq_client: return "Groq AI కనెక్ట్ కాలేదు."
    prompt = (
        f"మీరు ఒక లీడింగ్ మార్కెట్ ఇన్స్టిట్యూషనల్ రీసెర్చ్ హెడ్. కింద ఇవ్వబడిన డేటాను జాగ్రత్తగా చదవండి:\n\n"
        f"📌 స్టాక్ పేరు: {stock_name}\n"
        f"📊 కంపెనీ ఫండమెంటల్స్:\n{fund_data}\n\n"
        f"📰 గత 30 రోజులలోని బ్రోకరేజ్ అప్‌డేట్స్ & Αναలిస్ట్ వ్యూస్:\n{research_text}\n\n"
        f"విశ్లేషణ నిబంధనలు:\n"
        f"1. అందించిన గత 30 రోజులలోని హెడ్‌లైన్స్ ఆధారంగా బ్రోకరేజ్ సంస్థలు (ICICI Direct, Motilal Oswal, Jefferies, Morgan Stanley మొదలైనవి) ఈ స్టాక్‌పై ఎలాంటి రేటింగ్స్ లేదా టార్గెట్ ప్రైస్ (Target Price) ఇచ్చాయో స్పష్టంగా చెప్పండి.\n"
        f"2. బ్రోకర్లు అప్‌గ్రేడ్ (Upgrade) చేశారా లేక డౌన్‌గ్రేడ్ (Downgrade) చేశారా అనేది వివరించండి.\n"
        f"3. ఫండమెంటల్ నెంబర్లను చూసి కంపెనీ ఆర్థిక స్థితి బలంగా ఉందా లేదా అనేది 'చంటి గారికి' అర్థమయ్యేలా 4-5 స్పష్టమైన బిజినెస్ తెలుగు వాక్యాల్లో విశ్లేషించి ఇవ్వండి సార్."
    )
    try:
        time.sleep(3) 
        chat_completion = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            temperature=0.2
        )
        return chat_completion.choices[0].message.content.strip()
    except Exception as e: return f"విశ్లేషణ లోపం: {e}"

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
                
                fund_msg = (
                    f"🔹 *Market Cap:* ₹{m_cap:,.2f} Cr\n"
                    f"🔹 *P/E Ratio:* {pe_ratio} | *P/B Ratio:* {pb_ratio}\n"
                    f"🔹 *ROE:* {roe} | *PEG Ratio:* {peg_ratio}\n"
                    f"🔹 *Debt/Equity:* {debt_equity}\n"
                    f"🔹 *Sales Growth:* {sales_growth} | *Profit Growth:* {profit_growth}\n"
                )
                
                broker_text = fetch_broker_research_reports(clean_name)
                ai_analysis_telugu = get_groq_ai_research_analysis(clean_name, fund_msg, broker_text)
                
                tradingview_url = f"https://in.tradingview.com/chart/?symbol=NSE:{clean_name}"
                screener_url = f"https://www.screener.in/company/{clean_name}/"
                trendlyne_google = f"https://www.google.com/search?q={clean_name}+trendlyne+share+price"
                moneycontrol_google = f"https://www.google.com/search?q={clean_name}+moneycontrol+share+price"

                signal_type = "🟢 *BUY CHANTI SIGNAL!*" if is_long else "🔴 *SELL CHANTI SIGNAL!*"
                msg = (
                    f"{signal_type}\n📌 *స్టాక్ పేరు:* `{clean_name}`\n📅 *తేదీ:* {date_str}\n💰 *Close Price:* ₹{close_price:.2f}\n\n"
                    f"📊 *COMPANY FUNDAMENTALS (Yahoo):*\n{fund_msg}\n"
                    f"🎯 *GROQ AI BROKER RESEARCH & TARGETS (Past 30 Days):*\n{ai_analysis_telugu}\n\n"
                    f"🛠️ *1-CLICK ANALYSIS LINKS:*\n📈 [TradingView]({tradingview_url}) | 📊 [Screener]({screener_url})\n"
                    f"📰 [Trendlyne]({trendlyne_google}) | 💰 [Moneycontrol]({moneycontrol_google})\n"
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
    # 1. బాట్ స్టార్ట్ అవ్వగానే మొదట ఎకనామిక్ ఈవెంట్స్ చెక్ చేస్తుంది (సెలవు అయితే సెలవు అని మెసేజ్ పంపుతుంది)
    fetch_and_save_daily_events(is_startup=True)
    
    scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
    # ప్రతి రోజు రాత్రి కరెక్ట్‌గా 12:05 AM (00:05) కి కొత్త ఎకనామిక్ డేటా లేదా వీకెండ్ అలర్ట్ లోడ్ అవుతుంది
    scheduler.add_job(fetch_and_save_daily_events, 'cron', hour=0, minute=5, args=[False])
    # ప్రతి 1 నిమిషానికి బ్యాక్‌గ్రౌండ్‌లో టైమ్ చెక్ చేసి ఎకనామిక్ ఈవెంట్స్ లైవ్ అలర్ట్ పంపుతుంది
    scheduler.add_job(check_and_trigger_live_alerts, 'interval', minutes=1)
    # 🚀 --- ప్రతి గంటకు (Hourly) కరెక్ట్‌గా ఒక హార్ట్‌బీట్ మెసేజ్ మీకు పంపుతుంది సార్ ---
    scheduler.add_job(send_hourly_heartbeat, 'cron', minute=0)
    scheduler.start()
    
    threading.Thread(target=background_scheduler_loop, daemon=True).start()
    threading.Thread(target=run_telebot_polling, daemon=True).start()
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
