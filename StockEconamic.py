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
import cloudscraper            # క్లౌడ్‌స్క్రాపర్ గ్లోబల్ ఇంపోర్ట్
from bs4 import BeautifulSoup  # బ్యూటిఫుల్ సూప్ ఇంపోర్ట్ ఎర్రర్ ఇక్కడ పూర్తిగా ఫిక్స్ చేశాను సార్

# --- SYSTEM ENCODING ---
sys.stdout.reconfigure(encoding='utf-8')

# ==========================================================
# ⚙ CONFIGURATION & API KEYS (Render Environment Variables)
# ==========================================================
TOKEN = os.environ.get("BOT_TOKEN", "")       # మీ టెలిగ్రామ్ బాట్ టోకెన్
CHAT_ID = os.environ.get("CHAT_ID", "")       # మీ టెలిగ్రామ్ ఛానెల్/చాట్ ID
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "") # మీ Groq API కీ

bot = telebot.TeleBot(TOKEN) if TOKEN else None
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
IST = pytz.timezone("Asia/Kolkata")

# FastAPI యాప్ క్రియేషన్ (Render కోసం)
app = FastAPI()

# రోజువారీ ఎకనామిక్ ఈవెంట్లను దాచుకోవడానికి గ్లోబల్ లిస్ట్
TODAY_EVENTS_STORE = []

def log(msg, level="INFO"):
    print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] [{level}] {msg}")

# ==========================================================
# 🛠️ ECONOMIC CALENDAR CORE LOGIC (JSON Parsing)
# ==========================================================
def find_events_in_json(data):
    if isinstance(data, dict):
        if "event" in data and "country" in data:
            return [data]
        results = []
        for key, value in data.items():
            found = find_events_in_json(value)
            if found:
                results.extend(found)
        return results
    elif isinstance(data, list):
        results = []
        for item in data:
            found = find_events_in_json(item)
            if found:
                results.extend(found)
        return results
    return []

def fetch_and_save_daily_events(is_startup=False):
    """రాత్రి 12:05 కి లేదా స్టార్టప్‌లో ఎకనామిక్ ఈవెంట్స్ సేకరించి మెమొరీలో సేవ్ చేస్తుంది"""
    global TODAY_EVENTS_STORE
    log("ఎకనామిక్ క్యాలెండర్ డేటా మొత్తం సేకరిస్తున్నాను...")
    
    scraper = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
    )
    url = "https://www.investing.com/economic-calendar/"
    
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9"
        }
        response = scraper.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            log("ఎకనామిక్ డేటా కలెక్ట్ చేయడం ఫెయిల్ అయింది.", "ERROR")
            return
            
        soup = BeautifulSoup(response.text, 'html.parser')
        next_data_script = soup.find('script', id='__NEXT_DATA__')
        if not next_data_script:
            return
            
        json_data = json.loads(next_data_script.string)
        raw_events = find_events_in_json(json_data)
        
        temp_store = []
        country_map = {
            "INDIA": "IN", "UNITED STATES": "US", "JAPAN": "JP",
            "CHINA": "CH/CN", "SWITZERLAND": "CH/CN", "EURO ZONE": "EU"
        }
        
        today_date_str = datetime.now(IST).strftime("%d-%b")
        
        for ev in raw_events:
            country_name = str(ev.get("country", "")).strip().upper()
            matched_code = None
            for key, code in country_map.items():
                if key in country_name:
                    matched_code = code
                    break
                    
            if matched_code:
                raw_time = ev.get("time", "")
                if raw_time and "T" in raw_time:
                    try:
                        time_part = raw_time.replace("Z", "").split(".")[0]
                        utc_dt = datetime.strptime(time_part, "%Y-%m-%dT%H:%M:%S")
                        event_ist_dt = utc_dt + timedelta(hours=5, minutes=30)
                        
                        # టైమ్‌జోన్ అవేర్ గా మార్చడం చెకింగ్ కోసం
                        event_ist_dt = IST.localize(event_ist_dt)
                        
                        if event_ist_dt.strftime("%d-%b") == today_date_str:
                            importance = str(ev.get("importance", "1"))
                            stars = "⭐" * int(importance) if importance.isdigit() else "⭐"
                            
                            event_url = ev.get("url", "/economic-calendar/")
                            full_event_link = f"https://www.investing.com{event_url}"
                            
                            temp_store.append({
                                "datetime": event_ist_dt,
                                "time_str": event_ist_dt.strftime("%H:%M"),
                                "time_display": event_ist_dt.strftime("%I:%M %p"),
                                "date_display": event_ist_dt.strftime("%d-%b"),
                                "country": matched_code,
                                "event": ev.get("event", "Unknown Event"),
                                "stars": stars,
                                "actual": ev.get("actual", ""),
                                "forecast": ev.get("forecast", ""),
                                "previous": ev.get("previous", ""),
                                "link": full_event_link,
                                "alerted": False
                            })
                    except:
                        pass
                        
        temp_store.sort(key=lambda x: x['datetime'])
        TODAY_EVENTS_STORE = temp_store
        log(f"మొత్తం {len(TODAY_EVENTS_STORE)} ఎకనామిక్ ఈవెంట్స్ మెమొరీలో సేవ్ చేయబడ్డాయి సార్.")
        
        send_master_list_to_telegram(is_startup)

    except Exception as e:
        log(f"Daily economic fetch error: {e}", "ERROR")

def send_master_list_to_telegram(is_startup=False):
    if not bot or not CHAT_ID or not TODAY_EVENTS_STORE:
        return
    
    today_str = datetime.now(IST).strftime("%d-%B-%Y")
    
    if is_startup:
        header = f"🚀 **Economic Events Bot విజయవంతంగా స్టార్ట్ అయింది సార్!**\n"
        header += f"📅 **ఈరోజు ఆర్థిక ఈవెంట్స్ మాస్టర్ లిస్ట్ ({today_str}):**\n\n"
    else:
        header = f"📅 **ఈరోజు ఆర్థిక ఈవెంట్స్ మాస్టర్ లిస్ట్ ({today_str}):**\n\n"
        
    current_chunk = header + "-----------------------------\n"
    
    for ev in TODAY_EVENTS_STORE:
        event_msg = f"📅 తేదీ: {ev['date_display']} | ⏰ IST టైమ్: {ev['time_display']} | 🌑 {ev['country']} {ev['stars']}\n"
        event_msg += f"📝 ఈవెంట్: {ev['event']}\n"
        event_msg += f"📊 Actual: {ev['actual']} | Forecast: {ev['forecast']} | Prev: {ev['previous']}\n"
        event_msg += "-----------------------------\n"
        
        if len(current_chunk) + len(event_msg) > 3800:
            bot.send_message(CHAT_ID, current_chunk, parse_mode="Markdown")
            current_chunk = "-----------------------------\n" + event_msg
        else:
            current_chunk += event_msg
            
    current_chunk += "\n*ప్రతి ఈవెంట్ సమయానికి మీకు లైవ్ అలర్ట్ లింక్‌తో సహా వస్తుంది సార్!*"
    bot.send_message(CHAT_ID, current_chunk, parse_mode="Markdown")

def check_and_trigger_live_alerts():
    global TODAY_EVENTS_STORE
    current_time_str = datetime.now(IST).strftime("%H:%M")
    
    for ev in TODAY_EVENTS_STORE:
        if ev['time_str'] == current_time_str and not ev['alerted']:
            ev['alerted'] = True
            
            alert_msg = f"🚨 **లైవ్ ఎకనామిక్ ఈవెంట్ అలర్ట్!**\n\n"
            alert_msg += f"⏰ **సమయం:** {ev['time_display']} (IST)\n"
            alert_msg += f"🪙 **దేశం:** {ev['country']} {ev['stars']}\n"
            alert_msg += f"📝 **ఈవెంట్:** {ev['event']}\n\n"
            alert_msg += f"📊 **Forecast:** `{ev['forecast']}`\n"
            alert_msg += f"📊 **Previous:** `{ev['previous']}`\n\n"
            alert_msg += f"🔗 **Investing Link:** [ఇక్కడ క్లిక్ చేయండి]({ev['link']})"
            
            if bot and CHAT_ID:
                bot.send_message(CHAT_ID, alert_msg, parse_mode="Markdown", disable_web_page_preview=True)

# ==========================================================
# 🤖 GROQ AI - REAL MARKET NEWS FILTER LOGIC
# ==========================================================
def get_groq_filtered_news(stock_name, headlines_list):
    if not groq_client: 
        return "Groq AI అనుసంధానం కాలేదు. వార్త లభించలేదు.", None
    
    if not headlines_list:
        return "ఈరోజు ప్రత్యేక మీడియా వార్తలు ఏవీ లభించలేదు చంటి గారు.", None
        
    formatted_headlines = ""
    for idx, (title, link) in enumerate(headlines_list):
        formatted_headlines += f"[{idx+1}] వార్త: {title} | లింక్: {link}\n"

    prompt = (
        f"మీరు ఒక సీనియర్ స్టాక్ మార్కెట్ రీసెర్చ్ అనలిస్ట్. కింది లిస్ట్‌లో {stock_name} స్టాక్‌కు సంబంధించిన కొన్ని తాజా న్యూస్ హెడ్‌లైన్స్ ఉన్నాయి.\n"
        f"వీటిని జాగ్రత్తగా పరిశీలించి, ఏది అత్యంత తాజాదైన, నిజమైన మరియు స్టాక్ ధరపై ప్రభావం చూపే 'కరెక్ట్ మార్కెట్ వార్తనో' గుర్తించండి. "
        f"ఆ కరెక్ట్ వార్తను మాత్రమే ఎంచుకుని, 'చంటి గారికి' అర్థమయ్యేలా 2 సులభమైన బిజినెస్ తెలుగు వాక్యాల్లో విశ్లేషించి ఇవ్వండి (వార్త పాజిటివా లేదా నెగటివా అనేది కూడా చెప్పండి).\n"
        f"చివరన మీరు ఎంచుకున్న వార్త యొక్క నెంబర్ కచ్చితంగా ఇలా రాయండి -> SELECTED_INDEX: 1\n"
        f"ఒకవేళ అన్నీ చాలా పాత వార్తలు లేదా మార్కెట్‌కి సంబంధం లేని పనికిరానివి అయితే 'SELECTED_INDEX: NONE' అని ఇచ్చి, 'ఈరోజు ప్రత్యేక మార్కెట్ తాజా వార్తలు ఏవీ లేవు చంటి గారు' అని రాయండి.\n\n"
        f"హెడ్‌లైన్స్ లిస్ట్:\n{formatted_headlines}"
    )

    try:
        # 🚀 --- ఇక్కడ మార్పు చేశాను సార్ (Groq రేట్ లిమిట్ ఫిక్స్) ---
        # Groq AI కి రిక్వెస్ట్ వెళ్లే ప్రతిసారీ పక్కాగా 3 సెకన్ల గ్యాప్ ఇచ్చి రేట్ లిమిట్ రాకుండా ఆపుతుంది.
        time.sleep(3)
        
        chat_completion = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            temperature=0.2
        )
        response_text = chat_completion.choices[0].message.content
        
        selected_link = None
        if "SELECTED_INDEX:" in response_text:
            try:
                parts = response_text.split("SELECTED_INDEX:")
                idx_str = parts[1].strip().split()[0]
                if idx_str.isdigit():
                    idx = int(idx_str) - 1
                    if 0 <= idx < len(headlines_list):
                        selected_link = headlines_list[idx][1]
            except:
                pass
                
        clean_analysis = response_text.split("SELECTED_INDEX:")[0].strip()
        return clean_analysis, selected_link
    except Exception as e:
        log(f"❌ Groq News Filter error: {e}", "ERROR")
        return "వార్తలను విశ్లేషించడంలో Groq AI లోపం జరిగింది.", None

def get_cnbc_news_free(stock_name):
    try:
        search_query = f"{stock_name} share news CNBC"
        encoded_query = urllib.parse.quote(search_query)
        rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"
        
        response = requests.get(rss_url, timeout=10)
        headlines_list = []
        if response.status_code == 200:
            root = ET.fromstring(response.content)
            items = root.findall('.//item')
            for item in items[:3]:
                title = item.find('title').text
                link = item.find('link').text
                headlines_list.append((title, link))
        
        if headlines_list:
            ai_filtered_telugu, best_link = get_groq_filtered_news(stock_name, headlines_list)
            if best_link:
                try:
                    res = requests.head(best_link, allow_redirects=True, timeout=5)
                    best_link = res.url
                except:
                    pass
            return ai_filtered_telugu, best_link
            
        return "ఈరోజు ప్రత్యేక మీడియా వార్తలు ఏవీ లభించలేదు చంటి గారు.", None
    except Exception as e:
        log(f"⚠️ వార్త సేకరించడంలో లోపం: {e}")
        return "వార్తలను సేకరించడంలో సాంకేతిక లోపం జరిగింది.", None

# ==========================================================
# 📊 CHANTI 50EMA SCANNER LOGIC
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
                        if float_max_h >= L[idx_j] >= float_min_l:
                            support_found = True
                            break
                    if support_found:
                        zone_high, zone_low = float_max_h, float_min_l
                        price_was_outside_above, price_was_outside_below = False, False

        if zone_high is not None and zone_low is not None:
            if C[i] > zone_high: price_was_outside_above = True
            if C[i] < zone_low: price_was_outside_below = True
            if price_was_outside_above and L[i] <= zone_high and C[i] > zone_high and C[i] > O[i] and C[i] > ema_50[i]:
                long_signals[i], price_was_outside_above = True, False
            if price_was_outside_below and H[i] >= zone_low and C[i] < zone_low and C[i] < O[i] and C[i] < ema_50[i]:
                short_signals[i], price_was_outside_below = True, False

    df['Long_Signal'], df['Short_Signal'] = long_signals, short_signals
    return df

# ==========================================================
# 🎯 300+ STOCKS LIST (PERFECTLY CLEANED & VERIFIED FOR YAHOO)
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
    log(f"📡 చంటి 300+ స్టాక్స్ స్కానర్ ప్రారంభమైంది... (బ్యాచ్ వైజ్ సేఫ్ డౌన్‌లోడ్)")
    total_signals_found = 0
    
    # 300+ స్టాక్స్ ని ఒక్కొక్క బ్యాచ్ లో 30 చొప్పున విడగొడుతున్నాం సార్
    batch_size = 30
    all_data_frames = {}
    
    log(f"🔄 యాహూ ఫైనాన్స్ నుండి డేటాను బ్యాచ్ ల వారీగా సేకరిస్తున్నాను...")
    for i in range(0, len(combined_stocks), batch_size):
        batch = combined_stocks[i:i + batch_size]
        try:
            # చిన్న బ్యాచ్ గా డౌన్‌లోడ్ చేయడం వల్ల యాహూ ఎర్రర్స్ ఇవ్వదు సార్
            batch_data = yf.download(batch, period="1y", interval="1d", group_by='ticker', progress=False, auto_adjust=False)
            
            # డౌన్‌లోడ్ అయిన డేటాను ఒక మెయిన్ డిక్షనరీ లోకి చేర్చుకుంటున్నాం
            for stock in batch:
                if stock in batch_data.columns.levels[0]:
                    all_data_frames[stock] = batch_data[stock]
            
            # యాహూ సర్వర్ రేట్ లిమిట్ రాకుండా బ్యాచ్ కి బ్యాచ్ కి మధ్య 1 సెకన్ చిన్న విరామం
            time.sleep(1.0)
        except Exception as batch_ex:
            log(f"⚠️ బ్యాచ్ డౌన్‌లోడ్ లోపం ({i} నుండి {i+batch_size}): {batch_ex}", "WARNING")
            continue

    log(f"📊 డేటా సేకరణ పూర్తయింది. ఇప్పుడు టెక్నికల్ లాజిక్ స్కాన్ చేస్తున్నాను...")
    
    # సేకరించిన మొత్తం డేటాపై చంటి లాజిక్ రన్ చేయడం
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
                
                telugu_news, news_url = get_cnbc_news_free(clean_name)
                news_section = f"📰 *Groq AI ఫిల్టర్డ్ వార్త:* {telugu_news}\n🔗 [అసలైన CNBC వార్త ఇక్కడ చూడండి]({news_url})" if news_url else f"📰 *Groq AI ఫిల్టర్డ్ వార్త:* {telugu_news}"

                tradingview_url = f"https://in.tradingview.com/chart/?symbol=NSE:{clean_name}"
                screener_url = f"https://www.screener.in/company/{clean_name}/"
                trendlyne_google = f"https://www.google.com/search?q={clean_name}+trendlyne+share+price"
                moneycontrol_google = f"https://www.google.com/search?q={clean_name}+moneycontrol+share+price"

                signal_type = "🟢 *BUY CHANTI SIGNAL!*" if is_long else "🔴 *SELL CHANTI SIGNAL!*"
                msg = (
                    f"{signal_type}\n📌 *స్టాక్ పేరు:* `{clean_name}`\n📅 *తేదీ:* {date_str}\n💰 *Close Price:* ₹{close_price:.2f}\n\n"
                    f"{news_section}\n\n🛠️ *1-CLICK ANALYSIS LINKS:*\n📈 [TradingView]({tradingview_url}) | 📊 [Screener]({screener_url})\n"
                    f"📰 [Trendlyne]({trendlyne_google}) | 💰 [Moneycontrol]({moneycontrol_google})\n"
                )
                if bot and CHAT_ID:
                    bot.send_message(CHAT_ID, msg, parse_mode="Markdown", disable_web_page_preview=False)
                total_signals_found += 1
                time.sleep(1.5) 
        except Exception as ex:
            continue

    status_msg = f"✅ *300+ CHANTI SCANNER UPDATE*\n\nసార్, ఈరోజు స్కాన్ విజయవంతంగా పూర్తయింది. "
    status_msg += f"మొత్తం *{total_signals_found}* స్టాక్స్‌లో సిగ్నల్స్ లభించాయి." if total_signals_found > 0 else "❌ కానీ మన లాజిక్ ప్రకారం ఏ సిగ్నల్స్ రాలేదు సార్."
    if bot and CHAT_ID:
        bot.send_message(CHAT_ID, status_msg, parse_mode="Markdown")

# ==========================================================
# ⏰ BACKGROUND TIMERS & SELF-PING
# ==========================================================
def background_scheduler_loop():
    already_run_today = False
    app_url = "https://stockscaner-py.onrender.com/" # మీ Render URL ఇక్కడ పెట్టండి సార్
    last_ping_time = time.time()
    
    log("🚀 బాట్ బ్యాక్‌గ్రౌండ్‌లో యాక్టివ్‌గా రన్ అవుతోంది...")
    while True:
        try:
            now_ist = datetime.now(IST)
            if time.time() - last_ping_time >= 600:
                try: requests.get(app_url, timeout=5)
                except: pass
                last_ping_time = time.time()
                
            if now_ist.weekday() < 5:
                if now_ist.hour >= 16 and not already_run_today:
                    log("⏰ సమయం సాయంత్రం 4:00 PM దాటింది. 320 స్టాక్స్ స్కాన్ స్టార్ట్ చేస్తున్నాను...")
                    run_scanner()
                    already_run_today = True
                if now_ist.hour == 0: already_run_today = False
            else:
                already_run_today = False
                
        except Exception as e:
            log(f"Error in background loop: {e}", "ERROR")
        time.sleep(10)

# ==========================================================
# 🚀 FASTAPI WEB ENDPOINTS & MANUAL COMMANDS
# ==========================================================
@app.api_route("/", methods=["GET", "HEAD"])
def home():
    return {"status": "running", "bot_name": "Combined Chanti Scanner & Economic Calendar Bot", "time": datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}

if bot:
    @bot.message_handler(commands=['start'])
    def cmd_start(message): 
        start_html = (
            "🚀 <b>చంటి గారి 50EMA 320+ AI స్కానర్ & ఎకనామిక్ క్యాలెండర్ బాట్ రెడీ సార్!</b>\n\n"
            "• ఇందులో Nifty 100 మరియు పూర్తి F&O స్టాక్స్ ఉన్నాయి.\n"
            "• ప్రతి రోజు సాయంత్రం 4:00 PM కి ఆటోమేటిక్‌గా స్టాక్స్ స్కాన్ రన్ అవుతుంది.\n"
            "• రాత్రి 12:05 AM కి ఆటోమేటిక్‌గా ఈరోజు ఎకనామిక్ క్యాలెండర్ లోడ్ అవుతుంది.\n\n"
            "🛠️ <b>కమాండ్స్:</b>\n"
            "/today - ఈరోజు జరగబోయే రాబోయే ఎకనామిక్ ఈవెంట్స్ లిస్ట్ చూపిస్తుంది.\n"
            "/scan - మాన్యువల్‌గా 320+ స్టాక్స్ స్కాన్ స్టార్ట్ చేస్తుంది."
        )
        bot.reply_to(message, start_html, parse_mode='HTML')

    @bot.message_handler(commands=['scan'])
    def handle_manual_scan(message):
        bot.reply_to(message, "📡 మొత్తం 320+ (Nifty 100 + F&O) స్టాక్స్ స్కాన్ మాన్యువల్‌గా స్టార్ట్ చేశాను సార్, Groq AI వార్తలను వెరిఫై చేస్తోంది. కాసేపట్లో అలర్ట్స్ వస్తాయి...")
        threading.Thread(target=run_scanner).start()

    @bot.message_handler(commands=['today'])
    def handle_today_command(message):
        """ఎప్పుడు /today అని టైప్ చేసినా మెమొరీ లో ఉన్న లిస్ట్ లో కేవలం రాబోయే ఈవెంట్స్ చూపిస్తుంది"""
        if not TODAY_EVENTS_STORE:
            bot.reply_to(message, "📅 ప్రస్తుతం మెమొరీలో ఈరోజు డేటా ఏమీ లేదు సార్. రాత్రి 12:05 కి ఆటోమేటిక్ గా లోడ్ అవుతుంది.")
            return
            
        current_time = datetime.now(IST)
        today_str = current_time.strftime("%d-%B-%Y")
        
        header = f"📅 **ఈరోజు ఆర్థిక ఈవెంట్స్ (రాబోయేవి) - {today_str}:**\n\n"
        current_chunk = header + "-----------------------------\n"
        has_upcoming = False
        
        for ev in TODAY_EVENTS_STORE:
            if ev['datetime'] >= current_time:
                event_msg = f"📅 తేదీ: {ev['date_display']} | ⏰ IST టైమ్: {ev['time_display']} | 🌑 {ev['country']} {ev['stars']}\n"
                event_msg += f"📝 ఈవెంట్: {ev['event']}\n"
                event_msg += f"📊 Actual: {ev['actual']} | Forecast: {ev['forecast']} | Prev: {ev['previous']}\n"
                event_msg += "-----------------------------\n"
                has_upcoming = True
                
                if len(current_chunk) + len(event_msg) > 3800:
                    bot.send_message(message.chat.id, current_chunk, parse_mode="Markdown")
                    current_chunk = "-----------------------------\n" + event_msg
                else:
                    current_chunk += event_msg
                    
        if not has_upcoming:
            current_chunk += "🎉 ఈరోజు జరగబోయే ముఖ్యమైన ఆర్థిక ఈవెంట్స్ అన్నీ ముగిసిపోయాయి సార్!\n-----------------------------\n"
            
        bot.send_message(message.chat.id, current_chunk, parse_mode="Markdown")

def run_telebot_polling():
    if bot:
        log("🟢 Telebot Infinity Polling Started...")
        bot.infinity_polling(skip_pending=True)

# ==========================================================
# 🏁 MAIN STARTUP ENTRY POINT
# ==========================================================
if __name__ == "__main__":
    startup_time = datetime.now(IST).strftime('%Y-%m-%d %I:%M:%S %p')
    
    # 1. బాట్ స్టార్ట్ అవ్వగానే మొదట ఎకనామిక్ ఈవెంట్స్ లోడ్ చేసి మాస్టర్ లిస్ట్ అలర్ట్ పంపుతుంది
    fetch_and_save_daily_events(is_startup=True)
    
    # 2. స్కానర్ బాట్ స్టార్ట్ అయిన అలర్ట్
    start_msg = f"🚀 *CHANTI 320+ STOCKS SCANNER BOT STARTED!*\n\nసార్, బాట్ మరియు Groq AI పక్కా న్యూస్ ఫిల్టర్ విజయవంతంగా రన్ అయ్యాయి.\n📅 *సమయం:* `{startup_time} (IST)`"
    if bot and CHAT_ID:
        try: bot.send_message(CHAT_ID, start_msg, parse_mode="Markdown")
        except: pass

    # 3. ఆటోమేటిక్ అలర్ట్స్ కోసం షెడ్యూలర్ సెటప్
    scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
    # ప్రతి రోజు రాత్రి కరెక్ట్‌గా 12:05 AM (00:05) కి కొత్త ఎకనామిక్ డేటా లోడ్ అవుతుంది
    scheduler.add_job(fetch_and_save_daily_events, 'cron', hour=0, minute=5, args=[False])
    # ప్రతి 1 నిమిషానికి బ్యాక్‌గ్రౌండ్‌లో టైమ్嫌 చెక్ చేసి ఎకనామిక్ ఈవెంట్స్ లైవ్ అలర్ట్ పంపుతుంది
    scheduler.add_job(check_and_trigger_live_alerts, 'interval', minutes=1)
    scheduler.start()

    # 4. బ్యాక్‌గ్రౌండ్ థ్రెడ్స్ రన్ చేయడం
    threading.Thread(target=background_scheduler_loop, daemon=True).start()
    threading.Thread(target=run_telebot_polling, daemon=True).start()

    # 5. FastAPI సర్వర్ రన్ చేయడం (Render పోర్ట్ బైండింగ్ కోసం)
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
