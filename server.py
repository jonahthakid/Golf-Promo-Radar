#!/usr/bin/env python3
"""
Golf Promo Radar - Backend Server
Runs the scraper on a schedule and serves fresh data to the UI

Setup:
    pip install flask flask-cors playwright beautifulsoup4 apscheduler
    playwright install chromium
    python server.py

Then open http://localhost:5000 in your browser
"""

import asyncio
import json
import re
import os
import threading
from datetime import datetime
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler

# Try to import playwright - fallback gracefully if not installed
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    print("⚠️  Playwright not installed. Run: pip install playwright && playwright install chromium")

# =============================================================================
# CONFIG
# =============================================================================
REFRESH_INTERVAL_MINUTES = 5
DATA_FILE = "promo_data.json"
PORT = int(os.environ.get("PORT", 5000))

# Brand list
BRANDS = [
    # Major Athletic Brands
    {"name": "Nike Golf", "url": "https://www.nike.com/w/golf-3glsm", "category": "apparel", "tags": ["major", "footwear", "apparel"]},
    {"name": "Adidas Golf", "url": "https://www.adidas.com/us/golf", "category": "apparel", "tags": ["major", "footwear", "apparel"]},
    {"name": "Under Armour", "url": "https://www.underarmour.com/en-us/c/sports/golf/", "category": "apparel", "tags": ["major", "performance"]},
    {"name": "PUMA Golf", "url": "https://us.puma.com/us/en/golf", "category": "apparel", "tags": ["major", "rickie", "footwear"]},
    
    # Premium / Luxury
    {"name": "Peter Millar", "url": "https://www.petermillar.com/golf/", "category": "apparel", "tags": ["premium", "luxury", "country-club"]},
    {"name": "G/FORE", "url": "https://www.gfore.com", "category": "apparel", "tags": ["premium", "lifestyle", "footwear"]},
    {"name": "Greyson Clothiers", "url": "https://www.greysonclothiers.com", "category": "apparel", "tags": ["premium", "modern"]},
    {"name": "Ralph Lauren RLX", "url": "https://www.ralphlauren.com/brands-rlx", "category": "apparel", "tags": ["premium", "luxury", "polo"]},
    {"name": "J.Lindeberg", "url": "https://www.jlindeberg.com/us/golf", "category": "apparel", "tags": ["premium", "european", "tour"]},
    {"name": "Holderness & Bourne", "url": "https://www.holderness-bourne.com", "category": "apparel", "tags": ["premium", "polos"]},
    {"name": "Zero Restriction", "url": "https://www.zerorestriction.com", "category": "apparel", "tags": ["premium", "outerwear", "rain-gear"]},
    
    # Lifestyle / Culture
    {"name": "Malbon Golf", "url": "https://www.malbongolf.com", "category": "apparel", "tags": ["lifestyle", "streetwear", "buckets"]},
    {"name": "TravisMathew", "url": "https://www.travismathew.com", "category": "apparel", "tags": ["lifestyle", "socal", "callaway"]},
    {"name": "Eastside Golf", "url": "https://www.eastsidegolf.com", "category": "apparel", "tags": ["lifestyle", "culture", "jordan"]},
    {"name": "Bad Birdie", "url": "https://badbirdie.com", "category": "apparel", "tags": ["lifestyle", "loud", "prints"]},
    {"name": "Sunday Red", "url": "https://www.sundayred.com", "category": "apparel", "tags": ["lifestyle", "tiger", "taylormade"]},
    {"name": "Bogey Boys", "url": "https://bogeyboys.com", "category": "apparel", "tags": ["lifestyle", "macklemore", "streetwear"]},
    
    # Women's Focused
    {"name": "Lohla Sport", "url": "https://lohlasport.com", "category": "apparel", "tags": ["womens", "performance"]},
    {"name": "Foray Golf", "url": "https://foraygolf.com", "category": "apparel", "tags": ["womens", "modern"]},
    {"name": "Tory Sport", "url": "https://www.toryburch.com/en-us/sport/", "category": "apparel", "tags": ["womens", "luxury"]},
    {"name": "Daily Sports", "url": "https://dailysports.com", "category": "apparel", "tags": ["womens", "european"]},
    
    # Mid-Tier / Value
    {"name": "Rhoback", "url": "https://rhoback.com", "category": "apparel", "tags": ["mid-tier", "polos", "hoodies"]},
    {"name": "Swannies", "url": "https://swannies.co", "category": "apparel", "tags": ["mid-tier", "hoodies", "hats"]},
    {"name": "Radmor", "url": "https://radmor.com", "category": "apparel", "tags": ["mid-tier", "pants", "shorts"]},
    {"name": "Devereux Golf", "url": "https://devereuxgolf.com", "category": "apparel", "tags": ["mid-tier", "streetwear"]},
    {"name": "Avalon Golf", "url": "https://avalongolf.co", "category": "apparel", "tags": ["mid-tier", "joggers", "modern"]},
    {"name": "Walter Hagen", "url": "https://www.dickssportinggoods.com/f/walter-hagen-golf", "category": "apparel", "tags": ["value", "dicks-exclusive"]},
    {"name": "PGA TOUR Apparel", "url": "https://www.golfapparelshop.com", "category": "apparel", "tags": ["value", "licensed"]},
    {"name": "Uniqlo", "url": "https://www.uniqlo.com/us/en/sport-utility-wear", "category": "apparel", "tags": ["value", "basics", "adam-scott"]},
    
    # Footwear Specialists
    {"name": "FootJoy", "url": "https://www.footjoy.com", "category": "footwear", "tags": ["footwear", "tour", "gloves"]},
    {"name": "True Linkswear", "url": "https://truelinkswear.com", "category": "footwear", "tags": ["footwear", "comfort", "walking"]},
    
    # Bags & Accessories
    {"name": "Vessel", "url": "https://vesselgolf.com", "category": "bags", "tags": ["bags", "premium"]},
    {"name": "Stitch Golf", "url": "https://stitchgolf.com", "category": "bags", "tags": ["bags", "travel", "headcovers"]},
    
    # OEMs
    {"name": "TaylorMade", "url": "https://www.taylormadegolf.com", "category": "oem", "tags": ["clubs", "apparel", "balls"]},
    {"name": "Callaway Apparel", "url": "https://www.callawayapparel.com", "category": "oem", "tags": ["apparel", "oem"]},
    {"name": "Titleist", "url": "https://www.titleist.com", "category": "oem", "tags": ["clubs", "balls", "gear"]},
    {"name": "Cobra Golf", "url": "https://www.cobragolf.com", "category": "oem", "tags": ["clubs", "puma"]},
    {"name": "Cleveland Golf", "url": "https://www.clevelandgolf.com", "category": "oem", "tags": ["wedges", "clubs"]},
    {"name": "Ping", "url": "https://ping.com", "category": "oem", "tags": ["clubs", "fitting"]},
    {"name": "Mizuno Golf", "url": "https://mizunogolf.com", "category": "oem", "tags": ["irons", "clubs"]},
    {"name": "Srixon", "url": "https://www.srixon.com", "category": "oem", "tags": ["balls", "clubs"]},
    
    # Retailers
    {"name": "PGA Tour Superstore", "url": "https://www.pgatoursuperstore.com", "category": "retailer", "tags": ["multi-brand", "lessons"]},
    {"name": "Golf Galaxy", "url": "https://www.golfgalaxy.com", "category": "retailer", "tags": ["multi-brand", "dicks"]},
    {"name": "Carl's Golfland", "url": "https://www.carlsgolfland.com", "category": "retailer", "tags": ["multi-brand", "michigan"]},
    {"name": "Rock Bottom Golf", "url": "https://www.rockbottomgolf.com", "category": "retailer", "tags": ["discount", "deals"]},
    {"name": "Global Golf", "url": "https://www.globalgolf.com", "category": "retailer", "tags": ["used", "trade-in"]},
    {"name": "2nd Swing", "url": "https://www.2ndswing.com", "category": "retailer", "tags": ["used", "trade-in"]},
    {"name": "Trendy Golf", "url": "https://trendygolfusa.com", "category": "retailer", "tags": ["premium", "apparel"]},
    {"name": "Golf Apparel Shop", "url": "https://www.golfapparelshop.com", "category": "retailer", "tags": ["value", "clearance"]},
]

# Detection patterns
PROMO_PATTERNS = [
    r'\d+%\s*off', r'save\s*\$?\d+', r'free shipping',
    r'code[:\s]+[A-Z0-9]+', r'sitewide', r'limited time',
    r'flash sale', r'bogo', r'extra\s+\d+%', r'up to \d+%',
]

EMAIL_PATTERNS = [
    r'sign\s*up.*?\d+%', r'subscribe.*?\d+%', r'join.*?\d+%',
    r'newsletter.*?\d+%', r'first\s*(order|purchase).*?\d+%',
    r'\d+%.*?first\s*(order|purchase)', r'unlock\s+\d+%',
]

CODE_PATTERN = r'\b(CODE|PROMO|USE|ENTER)[:\s]*([A-Z0-9]{4,20})\b'

# =============================================================================
# SCRAPER
# =============================================================================
def matches_patterns(text, patterns):
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in patterns)

def extract_codes(text):
    matches = re.findall(CODE_PATTERN, text.upper())
    return list(set(code for _, code in matches if len(code) >= 4))

def clean_text(text, max_len=200):
    text = ' '.join(text.split())
    return text[:max_len] + "..." if len(text) > max_len else text

async def scrape_brand(page, brand):
    """Scrape a single brand"""
    result = {
        "brand": brand["name"],
        "url": brand["url"],
        "category": brand.get("category", "apparel"),
        "promo": None,
        "code": None,
        "tags": [],
        "email_offer": None,
        "error": None
    }
    
    try:
        await page.goto(brand["url"], wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)
        
        # Check announcement bars
        selectors = [
            '[class*="announcement"]', '[class*="promo-bar"]',
            '[class*="top-bar"]', '[class*="banner"]',
            '[class*="marquee"]', '[class*="ticker"]',
        ]
        
        for selector in selectors:
            try:
                elements = await page.query_selector_all(selector)
                for el in elements[:3]:
                    text = await el.inner_text()
                    if text and matches_patterns(text, PROMO_PATTERNS):
                        result["promo"] = clean_text(text)
                        codes = extract_codes(text)
                        if codes:
                            result["code"] = codes[0]
                        break
                if result["promo"]:
                    break
            except:
                pass
        
        # Check for email offers
        for selector in ['footer', '[class*="newsletter"]', '[class*="signup"]']:
            try:
                elements = await page.query_selector_all(selector)
                for el in elements[:2]:
                    text = await el.inner_text()
                    if text and matches_patterns(text, EMAIL_PATTERNS):
                        lines = text.split('\n')
                        for line in lines:
                            if matches_patterns(line, EMAIL_PATTERNS):
                                result["email_offer"] = clean_text(line, 100)
                                break
                    if result["email_offer"]:
                        break
            except:
                pass
            if result["email_offer"]:
                break
                
    except Exception as e:
        result["error"] = str(e)[:50]
    
    return result

async def run_scraper():
    """Run full scrape"""
    print(f"\n{'='*50}")
    print(f"🔄 Starting scrape at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")
    
    if not PLAYWRIGHT_AVAILABLE:
        print("❌ Playwright not available - using cached/static data")
        return None
    
    results = []
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            )
            page = await context.new_page()
            
            for i, brand in enumerate(BRANDS, 1):
                print(f"  [{i}/{len(BRANDS)}] {brand['name']}...", end=" ")
                result = await scrape_brand(page, brand)
                
                if result["error"]:
                    print("❌")
                elif result["promo"]:
                    print(f"✓ Found promo{' + code' if result['code'] else ''}")
                else:
                    print("○ No promo")
                
                if result["promo"]:
                    results.append(result)
            
            await browser.close()
    except Exception as e:
        print(f"❌ Scraper error: {e}")
        return None
    
    print(f"\n✅ Scrape complete: {len(results)} active promos found")
    return results

def scrape_sync():
    """Synchronous wrapper for scraper"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        results = loop.run_until_complete(run_scraper())
        if results:
            save_data(results)
    finally:
        loop.close()

def save_data(promos):
    """Save scraped data"""
    data = {
        "lastUpdated": datetime.now().isoformat(),
        "promos": promos,
        "codes": [
            {"brand": p["brand"], "code": p["code"], "discount": p["promo"][:50]}
            for p in promos if p.get("code")
        ],
        "emailOffers": [
            {"brand": p["brand"], "offer": p["email_offer"], "method": "Website"}
            for p in promos if p.get("email_offer")
        ]
    }
    
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)
    
    print(f"💾 Data saved to {DATA_FILE}")

def load_data():
    """Load data from file or return defaults"""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    
    # Return static fallback data
    return {
        "lastUpdated": datetime.now().isoformat(),
        "promos": [
            {"brand": "Rhoback", "url": "https://rhoback.com", "promo": "20% off sitewide", "code": None, "category": "apparel", "tags": ["polos", "hoodies"]},
            {"brand": "Swannies", "url": "https://swannies.co", "promo": "30% off sitewide", "code": None, "category": "apparel", "tags": ["hoodies", "hats"]},
            {"brand": "Malbon Golf", "url": "https://malbongolf.com", "promo": "Up to 60% off archive", "code": "ARCHIVESALE", "category": "apparel", "tags": ["archive"]},
            {"brand": "Stitch Golf", "url": "https://stitchgolf.com", "promo": "30% off sitewide", "code": "GIFTING", "category": "bags", "tags": ["bags", "travel"]},
            {"brand": "True Linkswear", "url": "https://truelinkswear.com", "promo": "20% sitewide + 70% select", "code": None, "category": "footwear", "tags": ["shoes"]},
        ],
        "codes": [
            {"brand": "Malbon Golf", "code": "ARCHIVESALE", "discount": "60% off archive"},
            {"brand": "Stitch Golf", "code": "GIFTING", "discount": "30% sitewide"},
        ],
        "emailOffers": [
            {"brand": "Malbon Golf", "offer": "10% off first order", "method": "Newsletter"},
        ]
    }

# =============================================================================
# FLASK APP
# =============================================================================
app = Flask(__name__, static_folder='.')
CORS(app)

@app.route('/')
def index():
    """Serve the HTML dashboard"""
    return send_from_directory('.', 'golf_promo_radar.html')

@app.route('/api/promos')
def get_promos():
    """API endpoint for promo data"""
    data = load_data()
    return jsonify(data)

@app.route('/api/refresh', methods=['POST'])
def trigger_refresh():
    """Manually trigger a refresh"""
    thread = threading.Thread(target=scrape_sync)
    thread.start()
    return jsonify({"status": "refresh_started"})

@app.route('/api/status')
def status():
    """Health check"""
    return jsonify({
        "status": "ok",
        "playwright_available": PLAYWRIGHT_AVAILABLE,
        "data_file_exists": os.path.exists(DATA_FILE),
        "refresh_interval_minutes": REFRESH_INTERVAL_MINUTES
    })

# =============================================================================
# MAIN
# =============================================================================
if __name__ == "__main__":
    print("\n" + "="*50)
    print("⛳ GOLF PROMO RADAR SERVER")
    print("="*50)
    
    # Run initial scrape in background
    if PLAYWRIGHT_AVAILABLE:
        print(f"\n🔄 Running initial scrape...")
        thread = threading.Thread(target=scrape_sync)
        thread.start()
    
    # Set up scheduler for periodic scrapes
    if PLAYWRIGHT_AVAILABLE:
        scheduler = BackgroundScheduler()
        scheduler.add_job(scrape_sync, 'interval', minutes=REFRESH_INTERVAL_MINUTES)
        scheduler.start()
        print(f"⏰ Scheduler started: refreshing every {REFRESH_INTERVAL_MINUTES} minutes")
    
    print(f"\n🌐 Starting server at http://localhost:{PORT}")
    print(f"   API endpoints:")
    print(f"   - GET  /api/promos   - Get current promo data")
    print(f"   - POST /api/refresh  - Trigger manual refresh")
    print(f"   - GET  /api/status   - Health check")
    print(f"\n   Open http://localhost:{PORT} in your browser\n")
    
    app.run(host='0.0.0.0', port=PORT, debug=False)
