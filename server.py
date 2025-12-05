#!/usr/bin/env python3
"""
Golf Promo Radar - Backend Server (No Playwright version)
Uses simple HTTP requests - works on Railway without system deps
"""

import json
import re
import os
import threading
import requests
from datetime import datetime
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup

# =============================================================================
# CONFIG
# =============================================================================
REFRESH_INTERVAL_MINUTES = 5
DATA_FILE = "promo_data.json"
PORT = int(os.environ.get("PORT", 5000))

# Brand list with direct URLs
BRANDS = [
    # Major Athletic
    {"name": "Nike Golf", "url": "https://www.nike.com/w/sale-golf-3glsmq0h4t", "category": "apparel", "tags": ["major", "footwear"]},
    {"name": "Adidas Golf", "url": "https://www.adidas.com/us/golf-sale", "category": "apparel", "tags": ["major", "footwear"]},
    {"name": "Under Armour", "url": "https://www.underarmour.com/en-us/c/mens/golf/", "category": "apparel", "tags": ["major", "performance"]},
    {"name": "PUMA Golf", "url": "https://us.puma.com/us/en/golf", "category": "apparel", "tags": ["major", "rickie"]},
    
    # Premium
    {"name": "Peter Millar", "url": "https://www.petermillar.com/golf/", "category": "apparel", "tags": ["premium", "luxury"]},
    {"name": "G/FORE", "url": "https://www.gfore.com", "category": "apparel", "tags": ["premium", "lifestyle"]},
    {"name": "Greyson Clothiers", "url": "https://www.greysonclothiers.com", "category": "apparel", "tags": ["premium", "modern"]},
    {"name": "J.Lindeberg", "url": "https://www.jlindeberg.com/us/men/golf", "category": "apparel", "tags": ["premium", "european"]},
    {"name": "Holderness & Bourne", "url": "https://www.holderness-bourne.com", "category": "apparel", "tags": ["premium", "polos"]},
    {"name": "Zero Restriction", "url": "https://www.zerorestriction.com", "category": "apparel", "tags": ["premium", "outerwear"]},
    
    # Lifestyle
    {"name": "Malbon Golf", "url": "https://www.malbongolf.com", "category": "apparel", "tags": ["lifestyle", "streetwear"]},
    {"name": "TravisMathew", "url": "https://www.travismathew.com", "category": "apparel", "tags": ["lifestyle", "socal"]},
    {"name": "Eastside Golf", "url": "https://www.eastsidegolf.com", "category": "apparel", "tags": ["lifestyle", "jordan"]},
    {"name": "Bad Birdie", "url": "https://badbirdie.com", "category": "apparel", "tags": ["lifestyle", "prints"]},
    {"name": "Sunday Red", "url": "https://www.sundayred.com", "category": "apparel", "tags": ["lifestyle", "tiger"]},
    {"name": "Bogey Boys", "url": "https://bogeyboys.com", "category": "apparel", "tags": ["lifestyle", "macklemore"]},
    
    # Women's
    {"name": "Lohla Sport", "url": "https://lohlasport.com", "category": "apparel", "tags": ["womens", "performance"]},
    {"name": "Foray Golf", "url": "https://foraygolf.com", "category": "apparel", "tags": ["womens", "modern"]},
    {"name": "Daily Sports", "url": "https://us.dailysports.com", "category": "apparel", "tags": ["womens", "european"]},
    
    # Mid-Tier
    {"name": "Rhoback", "url": "https://rhoback.com", "category": "apparel", "tags": ["mid-tier", "polos"]},
    {"name": "Swannies", "url": "https://swannies.co", "category": "apparel", "tags": ["mid-tier", "hoodies"]},
    {"name": "Radmor", "url": "https://radmor.com", "category": "apparel", "tags": ["mid-tier", "pants"]},
    {"name": "Devereux Golf", "url": "https://devereuxgolf.com", "category": "apparel", "tags": ["mid-tier", "streetwear"]},
    {"name": "Avalon Golf", "url": "https://avalongolf.co", "category": "apparel", "tags": ["mid-tier", "joggers"]},
    {"name": "B. Draddy", "url": "https://www.bdraddy.com", "category": "apparel", "tags": ["mid-tier", "premium"]},
    
    # Footwear
    {"name": "FootJoy", "url": "https://www.footjoy.com", "category": "footwear", "tags": ["footwear", "tour"]},
    {"name": "True Linkswear", "url": "https://truelinkswear.com", "category": "footwear", "tags": ["footwear", "comfort"]},
    
    # Bags
    {"name": "Vessel", "url": "https://vesselgolf.com", "category": "bags", "tags": ["bags", "premium"]},
    {"name": "Stitch Golf", "url": "https://stitchgolf.com", "category": "bags", "tags": ["bags", "travel"]},
    
    # OEMs
    {"name": "TaylorMade", "url": "https://www.taylormadegolf.com", "category": "oem", "tags": ["clubs", "apparel"]},
    {"name": "Callaway Apparel", "url": "https://www.callawayapparel.com", "category": "oem", "tags": ["apparel"]},
    {"name": "Titleist", "url": "https://www.titleist.com", "category": "oem", "tags": ["balls", "gear"]},
    {"name": "Cobra Golf", "url": "https://www.cobragolf.com", "category": "oem", "tags": ["clubs"]},
    
    # Retailers
    {"name": "PGA Tour Superstore", "url": "https://www.pgatoursuperstore.com", "category": "retailer", "tags": ["multi-brand"]},
    {"name": "Golf Galaxy", "url": "https://www.golfgalaxy.com", "category": "retailer", "tags": ["multi-brand"]},
    {"name": "Carl's Golfland", "url": "https://www.carlsgolfland.com", "category": "retailer", "tags": ["multi-brand"]},
    {"name": "Rock Bottom Golf", "url": "https://www.rockbottomgolf.com", "category": "retailer", "tags": ["discount"]},
    {"name": "Global Golf", "url": "https://www.globalgolf.com", "category": "retailer", "tags": ["used"]},
    {"name": "Golf Apparel Shop", "url": "https://www.golfapparelshop.com", "category": "retailer", "tags": ["value"]},
    {"name": "Trendy Golf", "url": "https://www.trendygolfusa.com", "category": "retailer", "tags": ["premium"]},
]

# Detection patterns
PROMO_PATTERNS = [
    r'(\d+)%\s*off',
    r'save\s*\$?(\d+)',
    r'free shipping',
    r'(code|promo)[:\s]+([A-Z0-9]+)',
    r'sitewide',
    r'limited time',
    r'flash sale',
    r'extra\s+(\d+)%',
    r'up to (\d+)%',
    r'sale',
    r'clearance',
]

EMAIL_PATTERNS = [
    r'(\d+)%.*?(sign|join|subscribe|email|newsletter|first)',
    r'(sign|join|subscribe).*?(\d+)%',
    r'first.*?order.*?(\d+)%',
    r'welcome.*?(\d+)%',
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}


def matches_promo(text):
    """Check if text contains promo patterns"""
    text_lower = text.lower()
    for pattern in PROMO_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    return False


def extract_discount(text):
    """Extract discount percentage from text"""
    match = re.search(r'(\d+)%', text)
    return int(match.group(1)) if match else 0


def extract_code(text):
    """Extract promo code from text"""
    patterns = [
        r'(?:code|promo|use|enter)[:\s]+([A-Z0-9]{4,20})',
        r'\b([A-Z]{2,}[0-9]{1,}[A-Z0-9]*)\b',
    ]
    text_upper = text.upper()
    for pattern in patterns:
        match = re.search(pattern, text_upper)
        if match:
            code = match.group(1)
            # Filter out common false positives
            if code not in ['HTTP', 'HTTPS', 'HTML', 'CSS', 'USD', 'OFF', 'NEW', 'SALE']:
                return code
    return None


def clean_text(text, max_len=150):
    """Clean and truncate text"""
    text = ' '.join(text.split())
    return text[:max_len] + "..." if len(text) > max_len else text


def scrape_brand(brand):
    """Scrape a single brand using requests"""
    result = {
        "brand": brand["name"],
        "url": brand["url"],
        "category": brand.get("category", "apparel"),
        "tags": brand.get("tags", []),
        "promo": None,
        "code": None,
        "email_offer": None,
        "error": None
    }
    
    try:
        response = requests.get(brand["url"], headers=HEADERS, timeout=15, allow_redirects=True)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Remove script/style elements
        for element in soup(['script', 'style', 'noscript']):
            element.decompose()
        
        # Check announcement bars and headers
        promo_selectors = [
            '[class*="announcement"]',
            '[class*="promo"]',
            '[class*="banner"]',
            '[class*="marquee"]',
            '[class*="ticker"]',
            '[class*="top-bar"]',
            '[class*="sale"]',
            'header',
        ]
        
        for selector in promo_selectors:
            try:
                elements = soup.select(selector)[:5]
                for el in elements:
                    text = el.get_text(separator=' ', strip=True)
                    if text and len(text) < 500 and matches_promo(text):
                        # Found a promo
                        result["promo"] = clean_text(text)
                        code = extract_code(text)
                        if code:
                            result["code"] = code
                        break
                if result["promo"]:
                    break
            except:
                pass
        
        # Check for email signup offers in footer
        footer_selectors = ['footer', '[class*="footer"]', '[class*="newsletter"]', '[class*="signup"]']
        for selector in footer_selectors:
            try:
                elements = soup.select(selector)[:3]
                for el in elements:
                    text = el.get_text(separator=' ', strip=True)
                    if text:
                        for pattern in EMAIL_PATTERNS:
                            if re.search(pattern, text.lower()):
                                # Extract just the relevant part
                                lines = text.split('.')
                                for line in lines:
                                    if re.search(r'\d+%', line.lower()):
                                        result["email_offer"] = clean_text(line, 100)
                                        break
                                break
                if result["email_offer"]:
                    break
            except:
                pass
                
    except requests.exceptions.Timeout:
        result["error"] = "timeout"
    except requests.exceptions.RequestException as e:
        result["error"] = str(e)[:50]
    except Exception as e:
        result["error"] = str(e)[:50]
    
    return result


def run_scraper():
    """Run full scrape of all brands"""
    print(f"\n{'='*50}")
    print(f"🔄 Starting scrape at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")
    
    results = []
    success_count = 0
    error_count = 0
    
    for i, brand in enumerate(BRANDS, 1):
        print(f"  [{i}/{len(BRANDS)}] {brand['name']}...", end=" ", flush=True)
        result = scrape_brand(brand)
        
        if result["error"]:
            print(f"❌ {result['error']}")
            error_count += 1
        elif result["promo"]:
            code_str = f" (code: {result['code']})" if result['code'] else ""
            print(f"✓ Found promo{code_str}")
            success_count += 1
            results.append(result)
        else:
            print("○ No promo found")
            # Still add to results so we track all brands
            results.append(result)
    
    print(f"\n✅ Scrape complete: {success_count} promos found, {error_count} errors")
    
    if results:
        save_data(results)
    
    return results


def save_data(promos):
    """Save scraped data to file"""
    # Filter to only those with promos for display
    active_promos = [p for p in promos if p.get("promo")]
    
    data = {
        "lastUpdated": datetime.now().isoformat(),
        "promos": active_promos,
        "codes": [
            {"brand": p["brand"], "code": p["code"], "discount": p["promo"][:50]}
            for p in active_promos if p.get("code")
        ],
        "emailOffers": [
            {"brand": p["brand"], "offer": p["email_offer"], "method": "Website"}
            for p in promos if p.get("email_offer")
        ]
    }
    
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)
    
    print(f"💾 Data saved: {len(active_promos)} promos, {len(data['codes'])} codes, {len(data['emailOffers'])} email offers")


def load_data():
    """Load data from file or return defaults"""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE) as f:
                return json.load(f)
        except:
            pass
    
    # Return static fallback
    return {
        "lastUpdated": datetime.now().isoformat(),
        "promos": [
            {"brand": "Rhoback", "url": "https://rhoback.com", "promo": "20% off sitewide", "code": None, "category": "apparel", "tags": ["mid-tier", "polos"]},
            {"brand": "Swannies", "url": "https://swannies.co", "promo": "30% off sitewide", "code": None, "category": "apparel", "tags": ["mid-tier", "hoodies"]},
            {"brand": "Malbon Golf", "url": "https://malbongolf.com", "promo": "Up to 60% off archive", "code": "ARCHIVESALE", "category": "apparel", "tags": ["lifestyle"]},
            {"brand": "Stitch Golf", "url": "https://stitchgolf.com", "promo": "30% off sitewide", "code": "GIFTING", "category": "bags", "tags": ["bags"]},
        ],
        "codes": [
            {"brand": "Malbon Golf", "code": "ARCHIVESALE", "discount": "60% off archive"},
            {"brand": "Stitch Golf", "code": "GIFTING", "discount": "30% sitewide"},
        ],
        "emailOffers": []
    }


# =============================================================================
# FLASK APP
# =============================================================================
app = Flask(__name__, static_folder='.')
CORS(app)

@app.route('/')
def index():
    return send_from_directory('.', 'golf_promo_radar.html')

@app.route('/api/promos')
def get_promos():
    return jsonify(load_data())

@app.route('/api/refresh', methods=['POST'])
def trigger_refresh():
    thread = threading.Thread(target=run_scraper)
    thread.start()
    return jsonify({"status": "refresh_started"})

@app.route('/api/status')
def status():
    return jsonify({
        "status": "ok",
        "data_file_exists": os.path.exists(DATA_FILE),
        "brand_count": len(BRANDS),
        "refresh_interval_minutes": REFRESH_INTERVAL_MINUTES
    })


# =============================================================================
# MAIN
# =============================================================================
if __name__ == "__main__":
    print("\n" + "="*50)
    print("⛳ GOLF PROMO RADAR SERVER")
    print("="*50)
    
    # Run initial scrape
    print(f"\n🔄 Running initial scrape...")
    thread = threading.Thread(target=run_scraper)
    thread.start()
    
    # Set up scheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(run_scraper, 'interval', minutes=REFRESH_INTERVAL_MINUTES)
    scheduler.start()
    print(f"⏰ Scheduler: refreshing every {REFRESH_INTERVAL_MINUTES} minutes")
    
    print(f"\n🌐 Server starting at http://localhost:{PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False)
