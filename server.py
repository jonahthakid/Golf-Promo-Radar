#!/usr/bin/env python3
"""
SKRATCH RADAR - Golf Promo Scraper Backend
Scans 170+ golf brands for promos, codes, and email offers
Integrates with Impact Radius for affiliate tracking + deals
"""

import json
import re
import os
import threading
import requests
from datetime import datetime, timedelta
from urllib.parse import urlparse, urljoin
from flask import Flask, jsonify, send_from_directory, request, session, Response
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup

# =============================================================================
# IMPACT RADIUS CONFIG
# =============================================================================
IMPACT_ENABLED = os.environ.get("IMPACT_ENABLED", "true").lower() == "true"
IMPACT_MEDIA_PARTNER_ID = os.environ.get("IMPACT_MEDIA_PARTNER_ID", "5770409")
IMPACT_ACCOUNT_SID = os.environ.get("IMPACT_ACCOUNT_SID", "IRegUCDRRCRj5770409FimSuCrN9KE65z1")
IMPACT_AUTH_TOKEN = os.environ.get("IMPACT_AUTH_TOKEN", "LMwc6y~ALQvsLtN_UorwhsXV6eFEyVPD")

# Import affiliate links (create affiliate_urls.py with your links)
try:
    from affiliate_urls import merge_affiliate_links
    HAS_AFFILIATE_LINKS = True
except ImportError:
    HAS_AFFILIATE_LINKS = False
    def merge_affiliate_links(brands): return brands

# =============================================================================
# CONFIG
# =============================================================================
REFRESH_INTERVAL_MINUTES = 10
DATA_FILE = "promo_data.json"
DEAL_HISTORY_FILE = "deal_history.json"
PORT = int(os.environ.get("PORT", 5000))

# Freshness settings
DEAL_EXPIRE_HOURS = 24  # Remove deals not seen in this many hours
DEAL_STALE_DAYS = 7     # Flag deals running for this many days as "always on"


# =============================================================================
# DEAL FRESHNESS TRACKING
# =============================================================================
def get_deal_key(deal):
    """Generate unique key for a deal based on brand + promo text"""
    brand = deal.get("brand", "").lower().strip()
    promo = deal.get("promo", "") or deal.get("offer", "") or ""
    # Normalize: remove extra spaces, lowercase
    promo_normalized = re.sub(r'\s+', ' ', promo.lower().strip())
    # Take first 100 chars to avoid minor text changes creating new deals
    return f"{brand}:{promo_normalized[:100]}"


def load_deal_history():
    """Load deal history from file"""
    if os.path.exists(DEAL_HISTORY_FILE):
        try:
            with open(DEAL_HISTORY_FILE) as f:
                return json.load(f)
        except:
            pass
    return {}


def save_deal_history(history):
    """Save deal history to file"""
    with open(DEAL_HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def parse_expiration_date(promo_text):
    """Try to extract expiration date from promo text"""
    if not promo_text:
        return None
    
    text = promo_text.lower()
    now = datetime.now()
    
    # Patterns like "ends 12/20", "through 12/20", "expires 12/20"
    date_patterns = [
        r'(?:ends?|through|until|expires?|thru)\s+(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?',
        r'(?:ends?|through|until|expires?|thru)\s+(\d{1,2})[/\-](\d{1,2})',
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, text)
        if match:
            try:
                month = int(match.group(1))
                day = int(match.group(2))
                year = now.year
                if match.lastindex >= 3 and match.group(3):
                    year = int(match.group(3))
                    if year < 100:
                        year += 2000
                
                exp_date = datetime(year, month, day, 23, 59, 59)
                # If date is in past and month is less than current, assume next year
                if exp_date < now and month < now.month:
                    exp_date = datetime(year + 1, month, day, 23, 59, 59)
                return exp_date.isoformat()
            except:
                pass
    
    # Day-based patterns like "ends Sunday", "ends tomorrow"
    day_names = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
    for i, day in enumerate(day_names):
        if f'ends {day}' in text or f'through {day}' in text or f'until {day}' in text:
            # Calculate next occurrence of that day
            days_ahead = i - now.weekday()
            if days_ahead <= 0:  # Target day already happened this week
                days_ahead += 7
            exp_date = now + timedelta(days=days_ahead)
            return exp_date.replace(hour=23, minute=59, second=59).isoformat()
    
    # "ends today", "today only"
    if 'today only' in text or 'ends today' in text:
        return now.replace(hour=23, minute=59, second=59).isoformat()
    
    # "ends tomorrow"
    if 'ends tomorrow' in text or 'tomorrow only' in text:
        return (now + timedelta(days=1)).replace(hour=23, minute=59, second=59).isoformat()
    
    # "this weekend", "weekend only"
    if 'this weekend' in text or 'weekend only' in text:
        days_until_sunday = 6 - now.weekday()
        if days_until_sunday < 0:
            days_until_sunday += 7
        exp_date = now + timedelta(days=days_until_sunday)
        return exp_date.replace(hour=23, minute=59, second=59).isoformat()
    
    # "limited time" - give it 3 days
    if 'limited time' in text:
        return (now + timedelta(days=3)).replace(hour=23, minute=59, second=59).isoformat()
    
    return None


def update_deal_history(deals, history):
    """
    Update deal history with current deals.
    Returns (updated_history, fresh_deals) where fresh_deals have freshness metadata.
    """
    now = datetime.now()
    now_iso = now.isoformat()
    
    # Track which deals we see this scan
    seen_keys = set()
    
    fresh_deals = []
    
    for deal in deals:
        key = get_deal_key(deal)
        seen_keys.add(key)
        
        promo_text = deal.get("promo") or deal.get("offer") or ""
        
        if key in history:
            # Existing deal - update last_seen
            history[key]["last_seen"] = now_iso
            history[key]["times_seen"] = history[key].get("times_seen", 1) + 1
            first_seen = datetime.fromisoformat(history[key]["first_seen"])
        else:
            # New deal
            history[key] = {
                "first_seen": now_iso,
                "last_seen": now_iso,
                "times_seen": 1,
                "brand": deal.get("brand"),
                "promo_preview": promo_text[:60]
            }
            first_seen = now
        
        # Parse expiration if not already set
        if "expires" not in history[key] or not history[key]["expires"]:
            history[key]["expires"] = parse_expiration_date(promo_text)
        
        # Calculate freshness metadata
        deal_age_hours = (now - first_seen).total_seconds() / 3600
        deal_age_days = deal_age_hours / 24
        
        # Check if expired by parsed date
        expires = history[key].get("expires")
        is_expired = False
        if expires:
            try:
                exp_date = datetime.fromisoformat(expires)
                is_expired = now > exp_date
            except:
                pass
        
        # Add freshness metadata to deal
        deal_with_meta = deal.copy()
        deal_with_meta["first_seen"] = history[key]["first_seen"]
        deal_with_meta["last_seen"] = history[key]["last_seen"]
        deal_with_meta["times_seen"] = history[key]["times_seen"]
        deal_with_meta["is_new"] = deal_age_hours < 24
        deal_with_meta["is_stale"] = deal_age_days > DEAL_STALE_DAYS
        deal_with_meta["is_expired"] = is_expired
        deal_with_meta["expires"] = expires
        
        # Only include if not expired
        if not is_expired:
            fresh_deals.append(deal_with_meta)
    
    # Remove deals not seen in DEAL_EXPIRE_HOURS
    expired_keys = []
    for key, data in history.items():
        if key not in seen_keys:
            last_seen = datetime.fromisoformat(data["last_seen"])
            hours_since = (now - last_seen).total_seconds() / 3600
            if hours_since > DEAL_EXPIRE_HOURS:
                expired_keys.append(key)
    
    for key in expired_keys:
        del history[key]
    
    if expired_keys:
        print(f"🧹 Cleaned up {len(expired_keys)} stale deals from history")
    
    return history, fresh_deals

# =============================================================================
# FULL BRAND LIST - 170+ Golf Brands
# Affiliate URLs are merged from affiliate_urls.py if available
# =============================================================================
BRANDS = [
    # ==========================================================================
    # MAJOR ATHLETIC BRANDS
    # ==========================================================================
    {"name": "Nike Golf", "url": "https://www.nike.com/w/golf-3glsm", "category": "apparel", "tags": ["major", "athletic"]},
    {"name": "Adidas Golf", "url": "https://www.adidas.com/us/golf", "category": "apparel", "tags": ["major", "athletic"]},
    {"name": "Under Armour Golf", "url": "https://www.underarmour.com/en-us/c/mens/golf/", "category": "apparel", "tags": ["major", "athletic"]},
    {"name": "PUMA Golf", "url": "https://us.puma.com/us/en/golf", "category": "apparel", "tags": ["major", "athletic", "rickie"]},
    
    # ==========================================================================
    # PREMIUM / LUXURY APPAREL
    # ==========================================================================
    {"name": "Peter Millar", "url": "https://www.petermillar.com/golf/", "category": "apparel", "tags": ["premium", "luxury"]},
    {"name": "G/FORE", "url": "https://www.gfore.com", "category": "apparel", "tags": ["premium", "luxury", "footwear"]},
    {"name": "Greyson Clothiers", "url": "https://www.greysonclothiers.com", "category": "apparel", "tags": ["premium", "modern"]},
    {"name": "Ralph Lauren RLX", "url": "https://www.ralphlauren.com/brands-rlx", "category": "apparel", "tags": ["premium", "luxury"]},
    {"name": "J.Lindeberg", "url": "https://www.jlindeberg.com/us/golf", "category": "apparel", "tags": ["premium", "european"]},
    {"name": "Holderness & Bourne", "url": "https://www.holderness-bourne.com", "category": "apparel", "tags": ["premium", "polos"]},
    {"name": "Zero Restriction", "url": "https://www.zerorestriction.com", "category": "apparel", "tags": ["premium", "outerwear"]},
    {"name": "Dunning Golf", "url": "https://dunninggolf.com", "category": "apparel", "tags": ["premium", "classic"]},
    {"name": "Kjus", "url": "https://www.kjus.com/us/golf", "category": "apparel", "tags": ["premium", "european", "tech"]},
    {"name": "Bogner", "url": "https://www.bogner.com/en-us/", "category": "apparel", "tags": ["premium", "luxury", "german"]},
    {"name": "Southern Tide", "url": "https://www.southerntide.com", "category": "apparel", "tags": ["premium", "southern"]},
    
    # ==========================================================================
    # LIFESTYLE / STREETWEAR / CULTURE
    # ==========================================================================
    {"name": "Malbon Golf", "url": "https://www.malbongolf.com", "category": "apparel", "tags": ["lifestyle", "streetwear", "hot"]},
    {"name": "TravisMathew", "url": "https://www.travismathew.com", "category": "apparel", "tags": ["lifestyle", "socal"]},
    {"name": "Eastside Golf", "url": "https://www.eastsidegolf.com", "category": "apparel", "tags": ["lifestyle", "streetwear", "jordan"]},
    {"name": "Bad Birdie", "url": "https://badbirdie.com", "category": "apparel", "tags": ["lifestyle", "prints", "bold"]},
    {"name": "Sunday Red", "url": "https://www.sundayred.com", "category": "apparel", "tags": ["lifestyle", "tiger"]},
    {"name": "Bogey Boys", "url": "https://bogeyboys.com", "category": "apparel", "tags": ["lifestyle", "streetwear", "macklemore"]},
    {"name": "Metalwood Studio", "url": "https://metalwoodstudio.com", "category": "apparel", "tags": ["lifestyle", "streetwear", "vintage"]},
    {"name": "Students Golf", "url": "https://studentsgolf.com", "category": "apparel", "tags": ["lifestyle", "streetwear"]},
    {"name": "Random Golf Club", "url": "https://randomgolfclub.com", "category": "apparel", "tags": ["lifestyle", "inclusive"]},
    {"name": "Quiet Golf", "url": "https://quietgolf.com", "category": "apparel", "tags": ["lifestyle", "minimalist"]},
    {"name": "Whim Golf", "url": "https://whimgolf.com", "category": "apparel", "tags": ["lifestyle", "streetwear"]},
    {"name": "Manors", "url": "https://manorsgolf.com", "category": "apparel", "tags": ["lifestyle", "uk"]},
    {"name": "The Golfer's Journal", "url": "https://www.thegolfersjournal.co", "category": "apparel", "tags": ["lifestyle", "media"]},
    {"name": "Swing Juice", "url": "https://swingjuice.com", "category": "apparel", "tags": ["lifestyle", "fun"]},
    {"name": "Blackballed Golf", "url": "https://blackballedgolf.com", "category": "apparel", "tags": ["lifestyle", "diversity"]},
    {"name": "Gumtree Golf", "url": "https://gumtreegolf.com", "category": "apparel", "tags": ["lifestyle", "nature"]},
    {"name": "WAAC Golf", "url": "https://waacgolf.com", "category": "apparel", "tags": ["lifestyle", "korean"]},
    {"name": "ANEW Golf", "url": "https://anewgolf.com", "category": "apparel", "tags": ["lifestyle", "korean", "womens"]},
    {"name": "Miura Golf", "url": "https://miuragolf.com", "category": "apparel", "tags": ["lifestyle", "premium", "oem"]},
    
    # ==========================================================================
    # WOMEN'S FOCUSED
    # ==========================================================================
    {"name": "Lohla Sport", "url": "https://lohlasport.com", "category": "apparel", "tags": ["womens", "performance"]},
    {"name": "Foray Golf", "url": "https://foraygolf.com", "category": "apparel", "tags": ["womens", "modern"]},
    {"name": "Tory Sport", "url": "https://www.toryburch.com/en-us/clothing/sport/", "category": "apparel", "tags": ["womens", "luxury"]},
    {"name": "Daily Sports", "url": "https://us.dailysports.com", "category": "apparel", "tags": ["womens", "european"]},
    {"name": "Fore All", "url": "https://www.foreall.com", "category": "apparel", "tags": ["womens", "inclusive"]},
    {"name": "KINONA", "url": "https://kinonasport.com", "category": "apparel", "tags": ["womens", "performance"]},
    {"name": "A. Putnam", "url": "https://aputnam.com", "category": "apparel", "tags": ["womens", "luxury"]},
    {"name": "Belyn Key", "url": "https://belynkey.com", "category": "apparel", "tags": ["womens", "classic"]},
    {"name": "GGblue", "url": "https://ggbluegolf.com", "category": "apparel", "tags": ["womens", "performance"]},
    {"name": "LIJA", "url": "https://lijastyle.com", "category": "apparel", "tags": ["womens", "activewear"]},
    {"name": "Jofit", "url": "https://www.jofit.com", "category": "apparel", "tags": ["womens", "performance"]},
    {"name": "EP Pro / EPNY", "url": "https://epnygolf.com", "category": "apparel", "tags": ["womens", "classic"]},
    {"name": "Golftini", "url": "https://golftini.com", "category": "apparel", "tags": ["womens", "fun"]},
    {"name": "Course & Club", "url": "https://courseandclub.com", "category": "apparel", "tags": ["womens", "lifestyle"]},
    {"name": "Beldrie", "url": "https://beldrie.com", "category": "apparel", "tags": ["womens", "beginner"]},
    {"name": "Draw and Fade", "url": "https://drawandfade.com", "category": "apparel", "tags": ["womens", "modern"]},
    {"name": "Famara Golf", "url": "https://famaragolf.com", "category": "apparel", "tags": ["womens", "uk", "art"]},
    {"name": "Fairmonde", "url": "https://fairmonde.com", "category": "apparel", "tags": ["womens", "new"]},
    {"name": "Jayebird", "url": "https://jayebirdgolf.com", "category": "apparel", "tags": ["womens", "classic"]},
    {"name": "Hedge Golf", "url": "https://hedgegolf.com", "category": "apparel", "tags": ["womens", "preppy"]},
    {"name": "Prio Golf", "url": "https://priogolf.com", "category": "apparel", "tags": ["womens", "lifestyle"]},
    
    # ==========================================================================
    # MID-TIER / VALUE APPAREL
    # ==========================================================================
    {"name": "Rhoback", "url": "https://rhoback.com", "category": "apparel", "tags": ["mid-tier", "polos"]},
    {"name": "Swannies", "url": "https://swannies.co", "category": "apparel", "tags": ["mid-tier", "hoodies"]},
    {"name": "Radmor", "url": "https://radmor.com", "category": "apparel", "tags": ["mid-tier", "pants"]},
    {"name": "Devereux Golf", "url": "https://devereuxgolf.com", "category": "apparel", "tags": ["mid-tier", "texas"]},
    {"name": "Avalon Golf", "url": "https://avalongolf.co", "category": "apparel", "tags": ["mid-tier", "joggers"]},
    {"name": "B. Draddy", "url": "https://www.bdraddy.com", "category": "apparel", "tags": ["mid-tier", "classic"]},
    {"name": "Linksoul", "url": "https://linksoul.com", "category": "apparel", "tags": ["mid-tier", "sustainable"]},
    {"name": "Vuori", "url": "https://vuoriclothing.com", "category": "apparel", "tags": ["mid-tier", "activewear"]},
    {"name": "Rhone", "url": "https://www.rhone.com", "category": "apparel", "tags": ["mid-tier", "performance"]},
    {"name": "Bonobos Golf", "url": "https://bonobos.com/shop/golf", "category": "apparel", "tags": ["mid-tier", "pants"]},
    {"name": "Original Penguin Golf", "url": "https://www.originalpenguin.com/collections/golf", "category": "apparel", "tags": ["mid-tier", "heritage"]},
    {"name": "Walter Hagen", "url": "https://www.dickssportinggoods.com/f/walter-hagen-golf-apparel", "category": "apparel", "tags": ["value", "dicks"]},
    {"name": "PGA TOUR Apparel", "url": "https://pgatour.com/shop", "category": "apparel", "tags": ["value", "tour"]},
    {"name": "Wilson Golf Apparel", "url": "https://www.wilson.com/en-us/golf/apparel", "category": "apparel", "tags": ["value", "heritage"]},
    {"name": "Amazon Essentials Golf", "url": "https://www.amazon.com/stores/page/E48ACFEA-F0D9-4E34-9C2E-6ABEEF9EDE9C", "category": "apparel", "tags": ["value", "budget"]},
    {"name": "Uniqlo", "url": "https://www.uniqlo.com/us/en/", "category": "apparel", "tags": ["value", "basics"]},
    {"name": "Maelreg", "url": "https://maelreg.com", "category": "apparel", "tags": ["value", "amazon"]},
    {"name": "Brady Brand", "url": "https://bradybrand.com", "category": "apparel", "tags": ["mid-tier", "tom-brady"]},
    
    # ==========================================================================
    # FOOTWEAR
    # ==========================================================================
    {"name": "FootJoy", "url": "https://www.footjoy.com", "category": "footwear", "tags": ["footwear", "tour", "classic"]},
    {"name": "True Linkswear", "url": "https://truelinkswear.com", "category": "footwear", "tags": ["footwear", "comfort"]},
    {"name": "Ecco Golf", "url": "https://us.ecco.com/golf/", "category": "footwear", "tags": ["footwear", "comfort"]},
    {"name": "Duca del Cosma", "url": "https://ducadelcosma.com", "category": "footwear", "tags": ["footwear", "italian"]},
    {"name": "Cuater Golf", "url": "https://cuatergolf.com", "category": "footwear", "tags": ["footwear", "travismathew"]},
    {"name": "Sqairz Golf", "url": "https://sqairz.com", "category": "footwear", "tags": ["footwear", "performance"]},
    {"name": "Athalonz Golf", "url": "https://athalonz.com", "category": "footwear", "tags": ["footwear", "performance"]},
    
    # ==========================================================================
    # BAGS & TRAVEL
    # ==========================================================================
    {"name": "Vessel Golf", "url": "https://vesselgolf.com", "category": "bags", "tags": ["bags", "premium"]},
    {"name": "Stitch Golf", "url": "https://stitchgolf.com", "category": "bags", "tags": ["bags", "travel", "leather"]},
    {"name": "Sun Mountain", "url": "https://www.sunmountain.com", "category": "bags", "tags": ["bags", "carts"]},
    {"name": "Jones Golf Bags", "url": "https://www.jonessportsco.com", "category": "bags", "tags": ["bags", "carry", "classic"]},
    {"name": "OGIO Golf", "url": "https://www.ogio.com/golf/", "category": "bags", "tags": ["bags", "callaway"]},
    {"name": "Subtle Patriot", "url": "https://subtlepatriot.com", "category": "bags", "tags": ["bags", "usa"]},
    {"name": "Club Glove", "url": "https://clubglove.com", "category": "bags", "tags": ["bags", "travel"]},
    {"name": "Sunday Golf", "url": "https://sundaygolf.com", "category": "bags", "tags": ["bags", "lightweight"]},
    {"name": "Ghost Golf", "url": "https://ghostgolf.com", "category": "bags", "tags": ["bags", "accessories", "towels"]},
    
    # ==========================================================================
    # ACCESSORIES / HEADCOVERS
    # ==========================================================================
    {"name": "Pins & Aces", "url": "https://pinsandaces.com", "category": "accessories", "tags": ["headcovers", "fun"]},
    {"name": "Rose & Fire", "url": "https://www.roseandfire.com", "category": "accessories", "tags": ["headcovers", "premium"]},
    {"name": "PRG Golf", "url": "https://prg.golf", "category": "accessories", "tags": ["headcovers", "irish"]},
    {"name": "Ace of Clubs Golf", "url": "https://www.aceofclubsgolfco.com", "category": "accessories", "tags": ["accessories", "leather"]},
    {"name": "Daphne's Headcovers", "url": "https://www.daphnesheadcovers.com", "category": "accessories", "tags": ["headcovers", "novelty"]},
    {"name": "Cayce Golf", "url": "https://caycegolf.com", "category": "accessories", "tags": ["headcovers", "custom"]},
    {"name": "Dormie Workshop", "url": "https://dormieworkshop.com", "category": "accessories", "tags": ["headcovers", "leather"]},
    {"name": "Seamus Golf", "url": "https://seamusgolf.com", "category": "accessories", "tags": ["headcovers", "wool"]},
    {"name": "Nevr Looz", "url": "https://nevrlooz.com", "category": "accessories", "tags": ["accessories", "tools"]},
    {"name": "Transfusion Golf", "url": "https://transfusiongolf.com", "category": "accessories", "tags": ["accessories", "drinkware"]},
    {"name": "Fore Ewe", "url": "https://foreewe.com", "category": "accessories", "tags": ["headcovers", "sheep"]},
    {"name": "Winston Collection", "url": "https://winstoncollection.com", "category": "accessories", "tags": ["accessories", "leather"]},
    {"name": "VivanTee Golf", "url": "https://vivanteegolf.com", "category": "accessories", "tags": ["accessories", "gloves"]},
    {"name": "Branded Bills", "url": "https://www.brandedbills.com", "category": "accessories", "tags": ["hats", "state"]},
    {"name": "Melin", "url": "https://melin.com", "category": "accessories", "tags": ["hats", "premium"]},
    {"name": "Imperial Headwear", "url": "https://imperialsports.com", "category": "accessories", "tags": ["hats", "tour"]},
    {"name": "Pukka Golf", "url": "https://pukka.com", "category": "accessories", "tags": ["hats", "custom"]},
    {"name": "Oakley Golf", "url": "https://www.oakley.com/en-us/category/golf", "category": "accessories", "tags": ["eyewear", "sunglasses"]},
    
    # ==========================================================================
    # OEM / EQUIPMENT (with apparel)
    # ==========================================================================
    {"name": "TaylorMade", "url": "https://www.taylormadegolf.com", "category": "oem", "tags": ["clubs", "apparel"]},
    {"name": "Callaway Golf", "url": "https://www.callawaygolf.com", "category": "oem", "tags": ["clubs", "balls"]},
    {"name": "Callaway Apparel", "url": "https://www.callawayapparel.com", "category": "oem", "tags": ["apparel"]},
    {"name": "Titleist", "url": "https://www.titleist.com", "category": "oem", "tags": ["balls", "clubs"]},
    {"name": "Cobra Golf", "url": "https://www.cobragolf.com", "category": "oem", "tags": ["clubs", "puma"]},
    {"name": "PING", "url": "https://ping.com", "category": "oem", "tags": ["clubs", "fitting"]},
    {"name": "Cleveland Golf", "url": "https://www.clevelandgolf.com", "category": "oem", "tags": ["wedges", "clubs"]},
    {"name": "Srixon Golf", "url": "https://www.srixon.com", "category": "oem", "tags": ["balls", "clubs"]},
    {"name": "Mizuno Golf", "url": "https://mizunogolf.com", "category": "oem", "tags": ["irons", "apparel"]},
    {"name": "Bridgestone Golf", "url": "https://www.bridgestonegolf.com", "category": "oem", "tags": ["balls", "clubs"]},
    {"name": "PXG", "url": "https://www.pxg.com", "category": "oem", "tags": ["clubs", "premium", "apparel"]},
    {"name": "Wilson Sporting Goods", "url": "https://www.wilson.com/en-us/golf", "category": "oem", "tags": ["clubs", "balls"]},
    {"name": "Tour Edge", "url": "https://www.touredge.com", "category": "oem", "tags": ["clubs", "value"]},
    {"name": "Honma Golf", "url": "https://us.honmagolf.com", "category": "oem", "tags": ["clubs", "japanese", "luxury"]},
    {"name": "Bettinardi Golf", "url": "https://bettinardi.com", "category": "oem", "tags": ["putters", "premium"]},
    {"name": "Scotty Cameron", "url": "https://www.scottycameron.com", "category": "oem", "tags": ["putters", "titleist"]},
    {"name": "Odyssey Golf", "url": "https://www.odysseygolf.com", "category": "oem", "tags": ["putters", "callaway"]},
    {"name": "XXIO Golf", "url": "https://www.xxio.com/us/", "category": "oem", "tags": ["clubs", "lightweight"]},
    {"name": "Ben Hogan Golf", "url": "https://benhogangolf.com", "category": "oem", "tags": ["clubs", "heritage"]},
    {"name": "L.A.B. Golf", "url": "https://labgolf.com", "category": "oem", "tags": ["putters", "lie-angle"]},
    {"name": "Maxfli", "url": "https://www.maxfli.com", "category": "oem", "tags": ["balls", "dicks"]},
    {"name": "Vice Golf", "url": "https://www.vicegolf.com", "category": "oem", "tags": ["balls", "dtc"]},
    {"name": "OnCore Golf", "url": "https://oncoregolf.com", "category": "oem", "tags": ["balls", "dtc"]},
    {"name": "Snell Golf", "url": "https://www.snellgolf.com", "category": "oem", "tags": ["balls", "dtc"]},
    {"name": "Seed Golf", "url": "https://seedgolf.com", "category": "oem", "tags": ["balls", "dtc"]},
    {"name": "Cut Golf", "url": "https://cutgolf.co", "category": "oem", "tags": ["balls", "dtc"]},
    {"name": "SuperStroke", "url": "https://superstrokeusa.com", "category": "oem", "tags": ["grips", "putters"]},
    {"name": "Golf Pride", "url": "https://www.golfpride.com", "category": "oem", "tags": ["grips"]},
    {"name": "Lamkin Grips", "url": "https://www.lamkingrips.com", "category": "oem", "tags": ["grips"]},
    {"name": "Fujikura Golf", "url": "https://www.fujikuragolf.com", "category": "oem", "tags": ["shafts"]},
    {"name": "Project X Golf", "url": "https://www.projectxgolf.com", "category": "oem", "tags": ["shafts", "true-temper"]},
    {"name": "Graphite Design", "url": "https://www.graphitedesign.com", "category": "oem", "tags": ["shafts", "japanese"]},
    
    # ==========================================================================
    # RETAILERS
    # ==========================================================================
    {"name": "PGA Tour Superstore", "url": "https://www.pgatoursuperstore.com", "category": "retailer", "tags": ["multi-brand", "big-box"]},
    {"name": "Golf Galaxy", "url": "https://www.golfgalaxy.com", "category": "retailer", "tags": ["multi-brand", "dicks"]},
    {"name": "Carl's Golfland", "url": "https://www.carlsgolfland.com", "category": "retailer", "tags": ["multi-brand", "michigan"]},
    {"name": "Rock Bottom Golf", "url": "https://www.rockbottomgolf.com", "category": "retailer", "tags": ["discount", "value"]},
    {"name": "Global Golf", "url": "https://www.globalgolf.com", "category": "retailer", "tags": ["used", "trade-in"]},
    {"name": "2nd Swing", "url": "https://www.2ndswing.com", "category": "retailer", "tags": ["used", "trade-in"]},
    {"name": "Golf Apparel Shop", "url": "https://www.golfapparelshop.com", "category": "retailer", "tags": ["apparel", "value"]},
    {"name": "Trendy Golf", "url": "https://www.trendygolfusa.com", "category": "retailer", "tags": ["premium", "curated"]},
    {"name": "Worldwide Golf Shops", "url": "https://www.worldwidegolfshops.com", "category": "retailer", "tags": ["multi-brand"]},
    {"name": "Golf Discount", "url": "https://www.golfdiscount.com", "category": "retailer", "tags": ["discount", "seattle"]},
    {"name": "Budget Golf", "url": "https://www.budgetgolf.com", "category": "retailer", "tags": ["discount"]},
    {"name": "Fairway Golf", "url": "https://fairwaygolfusa.com", "category": "retailer", "tags": ["japanese", "jdm"]},
    {"name": "Golf Locker", "url": "https://www.golflocker.com", "category": "retailer", "tags": ["apparel", "accessories"]},
    {"name": "The Golf Warehouse", "url": "https://www.tgw.com", "category": "retailer", "tags": ["multi-brand"]},
    {"name": "Rain or Shine Golf", "url": "https://rainorshinegolf.com", "category": "retailer", "tags": ["simulators", "equipment"]},
    {"name": "Golf Avenue", "url": "https://www.golfavenue.com", "category": "retailer", "tags": ["used", "canada"]},
    {"name": "Golf Headquarters", "url": "https://www.golfheadquarters.com", "category": "retailer", "tags": ["multi-brand"]},
    {"name": "Golfers Warehouse", "url": "https://www.golferswarehouse.com", "category": "retailer", "tags": ["northeast"]},
    {"name": "Dick's Sporting Goods", "url": "https://www.dickssportinggoods.com/f/golf", "category": "retailer", "tags": ["big-box"]},
    {"name": "Amazon Golf", "url": "https://www.amazon.com/golf/b?node=3410851", "category": "retailer", "tags": ["marketplace"]},
    
    # ==========================================================================
    # IMPACT PARTNER BRANDS (auto-added from Impact Radius)
    # ==========================================================================
    {"name": "Mizzen+Main", "url": "https://www.mizzenandmain.com", "category": "apparel", "tags": ["premium", "dress-shirts", "impact"]},
    {"name": "Boston Scally", "url": "https://www.bostonscally.com", "category": "accessories", "tags": ["hats", "caps", "impact"]},
    {"name": "Stewart Golf", "url": "https://www.stewartgolfusa.com", "category": "equipment", "tags": ["push-carts", "electric", "impact"]},
    {"name": "Scheels", "url": "https://www.scheels.com/c/golf", "category": "retailer", "tags": ["big-box", "midwest", "impact"]},
    {"name": "Sounder Golf", "url": "https://www.soundergolf.com", "category": "apparel", "tags": ["lifestyle", "modern", "impact"]},
    {"name": "Rapsodo", "url": "https://rapsodo.com", "category": "tech", "tags": ["launch-monitor", "simulator", "impact"]},
    {"name": "Five Iron Golf", "url": "https://fiveirongolf.com", "category": "experience", "tags": ["simulator", "urban", "impact"]},
]

# Merge affiliate links into brands list
BRANDS = merge_affiliate_links(BRANDS)

# =============================================================================
# IMPACT RADIUS API INTEGRATION
# =============================================================================
class ImpactAPI:
    """Impact Radius API client for fetching campaigns, ads, and tracking links"""
    
    def __init__(self):
        self.media_partner_id = IMPACT_MEDIA_PARTNER_ID
        self.account_sid = IMPACT_ACCOUNT_SID
        self.auth_token = IMPACT_AUTH_TOKEN
        self.base_url = f"https://api.impact.com/Mediapartners/{self.account_sid}"
        self.session = requests.Session()
        self.session.auth = (self.account_sid, self.auth_token)
        self.session.headers.update({
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        })
        # Cache
        self._campaigns = None
        self._ads = None
        self._tracking_links = {}
    
    def _get(self, endpoint, params=None):
        """Make GET request to Impact API"""
        url = f"{self.base_url}/{endpoint}"
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Impact API Error: {e}")
            return None
    
    def get_campaigns(self, force_refresh=False):
        """Get all active campaigns (cached)"""
        if self._campaigns is None or force_refresh:
            data = self._get("Campaigns", {"PageSize": 100})
            if data and "Campaigns" in data:
                self._campaigns = data["Campaigns"]
            else:
                self._campaigns = []
        return self._campaigns
    
    def get_ads(self, force_refresh=False):
        """Get all available ads/deals (cached)"""
        if self._ads is None or force_refresh:
            all_ads = []
            page = 1
            while True:
                data = self._get("Ads", {"PageSize": 100, "Page": page})
                if data and "Ads" in data:
                    all_ads.extend(data["Ads"])
                    if len(data["Ads"]) < 100:
                        break
                    page += 1
                else:
                    break
            self._ads = all_ads
        return self._ads
    
    def get_actions(self, start_date=None, end_date=None):
        """Get conversion actions (sales/leads)"""
        if not start_date:
            start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00Z")
        if not end_date:
            end_date = datetime.now().strftime("%Y-%m-%dT23:59:59Z")
        
        params = {
            "StartDate": start_date,
            "EndDate": end_date,
            "PageSize": 1000
        }
        data = self._get("Actions", params)
        if data and "Actions" in data:
            return data["Actions"]
        return []
    
    def get_action_inquiries(self, start_date=None, end_date=None):
        """Get action inquiries (pending conversions)"""
        if not start_date:
            start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00Z")
        if not end_date:
            end_date = datetime.now().strftime("%Y-%m-%dT23:59:59Z")
        
        params = {
            "StartDate": start_date,
            "EndDate": end_date,
            "PageSize": 1000
        }
        data = self._get("ActionInquiries", params)
        if data and "ActionInquiries" in data:
            return data["ActionInquiries"]
        return []
    
    def get_performance_report(self, days=30):
        """Get aggregated performance data"""
        campaigns = self.get_campaigns()
        actions = self.get_actions()
        
        # Aggregate by campaign
        campaign_stats = {}
        for campaign in campaigns:
            campaign_id = campaign.get("CampaignId")
            campaign_name = campaign.get("CampaignName", "Unknown")
            campaign_stats[campaign_id] = {
                "name": campaign_name,
                "advertiser": campaign.get("AdvertiserName", ""),
                "tracking_link": campaign.get("TrackingLink", ""),
                "actions": 0,
                "revenue": 0.0,
                "payout": 0.0
            }
        
        total_actions = 0
        total_revenue = 0.0
        total_payout = 0.0
        
        for action in actions:
            campaign_id = action.get("CampaignId")
            amount = float(action.get("Amount", 0) or 0)
            payout = float(action.get("Payout", 0) or 0)
            
            total_actions += 1
            total_revenue += amount
            total_payout += payout
            
            if campaign_id in campaign_stats:
                campaign_stats[campaign_id]["actions"] += 1
                campaign_stats[campaign_id]["revenue"] += amount
                campaign_stats[campaign_id]["payout"] += payout
        
        # Sort campaigns by payout
        top_campaigns = sorted(
            [v for v in campaign_stats.values() if v["actions"] > 0],
            key=lambda x: x["payout"],
            reverse=True
        )[:10]
        
        return {
            "period_days": days,
            "total_campaigns": len(campaigns),
            "total_actions": total_actions,
            "total_revenue": round(total_revenue, 2),
            "total_payout": round(total_payout, 2),
            "top_campaigns": top_campaigns,
            "all_campaigns": list(campaign_stats.values())
        }
    
    def get_tracking_link_for_brand(self, brand_name):
        """Get tracking link for a brand by matching campaign name"""
        if brand_name in self._tracking_links:
            return self._tracking_links[brand_name]
        
        campaigns = self.get_campaigns()
        brand_lower = brand_name.lower().replace(" golf", "").replace("golf ", "").strip()
        
        for campaign in campaigns:
            campaign_name = campaign.get("CampaignName", "").lower()
            advertiser_name = campaign.get("AdvertiserName", "").lower()
            
            # Try to match
            for name in [campaign_name, advertiser_name]:
                name_clean = name.replace(" golf", "").replace("golf ", "").strip()
                if brand_lower in name_clean or name_clean in brand_lower:
                    link = campaign.get("TrackingLink", "")
                    if link:
                        self._tracking_links[brand_name] = link
                        return link
        
        return None
    
    def get_deals_for_brand(self, brand_name):
        """Get any deals/promos from Impact for a brand"""
        deals = []
        ads = self.get_ads()
        brand_lower = brand_name.lower().replace(" golf", "").replace("golf ", "").strip()
        
        for ad in ads:
            campaign_name = ad.get("CampaignName", "").lower()
            if brand_lower in campaign_name or campaign_name.replace(" golf", "").strip() in brand_lower:
                description = ad.get("Description", "")
                if description and len(description) > 10:
                    # Filter out generic product descriptions
                    if any(word in description.lower() for word in ['off', 'save', 'free', 'discount', '%', 'sale']):
                        deals.append({
                            "text": description,
                            "link": ad.get("TrackingLink", ""),
                            "type": ad.get("Type", "TEXT_LINK")
                        })
        
        return deals
    
    def get_all_deals(self):
        """Get all deals from Impact, formatted for Radar"""
        all_deals = []
        ads = self.get_ads()
        campaigns = {c.get("CampaignId"): c for c in self.get_campaigns()}
        
        for ad in ads:
            description = ad.get("Description", "")
            # Only include if it looks like a real deal
            if description and len(description) > 10:
                if any(word in description.lower() for word in ['off', 'save', 'free', 'discount', '%', 'sale', 'refer']):
                    campaign_name = ad.get("CampaignName", "Unknown")
                    tracking_link = ad.get("TrackingLink", "")
                    
                    # Extract discount percentage if present
                    discount_match = re.search(r'(\d+)%', description)
                    discount = int(discount_match.group(1)) if discount_match else 0
                    
                    all_deals.append({
                        "brand": campaign_name,
                        "promo": description[:150],
                        "discount": discount,
                        "affiliate_url": tracking_link,
                        "source": "impact",
                        "type": "impact_deal"
                    })
        
        return all_deals


# Global Impact API instance
impact_api = None
if IMPACT_ENABLED:
    try:
        impact_api = ImpactAPI()
        print("✅ Impact Radius API initialized")
    except Exception as e:
        print(f"⚠️  Impact API init failed: {e}")
        impact_api = None


def merge_impact_tracking_links(brands):
    """Merge Impact tracking links into brands that don't have affiliate URLs"""
    if not impact_api:
        return brands
    
    updated = 0
    for brand in brands:
        if not brand.get("affiliate_url"):
            tracking_link = impact_api.get_tracking_link_for_brand(brand["name"])
            if tracking_link:
                brand["affiliate_url"] = tracking_link
                updated += 1
    
    if updated > 0:
        print(f"✅ Added {updated} Impact tracking links to brands")
    
    return brands


# Merge Impact tracking links
if IMPACT_ENABLED and impact_api:
    BRANDS = merge_impact_tracking_links(BRANDS)

# =============================================================================
# DETECTION PATTERNS
# =============================================================================
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
    r'bogo',
    r'buy one get',
    r'final sale',
    r'warehouse sale',
    r'holiday',
    r'cyber',
    r'black friday',
]

EMAIL_PATTERNS = [
    r'(\d+)%.*?(sign|join|subscribe|email|newsletter|first)',
    r'(sign|join|subscribe).*?(\d+)%',
    r'first.*?order.*?(\d+)%',
    r'welcome.*?(\d+)%',
    r'join.*?list.*?(\d+)',
    r'email.*?exclusive',
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate',
    'Connection': 'keep-alive',
}

# =============================================================================
# SCRAPER FUNCTIONS
# =============================================================================

# Junk phrases to filter out (navigation, generic text, etc.)
JUNK_PHRASES = [
    'shop now', 'shop all', 'view all', 'see all', 'learn more', 'read more',
    'sign in', 'log in', 'my account', 'cart', 'checkout', 'search',
    'menu', 'close', 'open', 'skip to', 'accessibility',
    'men', 'women', 'new arrivals', 'best sellers', 'collections',
    'contact us', 'customer service', 'help', 'faq',
    'privacy policy', 'terms', 'cookie', 'accept',
    'instagram', 'facebook', 'twitter', 'tiktok', 'youtube', 'pinterest',
    'download', 'app store', 'google play',
    'united states', 'select country', 'change location',
    'loading', 'please wait',
    # Browser/site warnings
    'limited support for your browser', 'we recommend switching', 'recommend switching to',
    'chrome, safari', 'edge, chrome', 'firefox',
    # Cart/order messages (not promos)
    'congratulations! your order', 'your order qualifies', 'order qualifies for',
    'your cart is empty', 'no items in', 'items in your cart',
    # Currency selectors
    'currency', 'usd $', 'eur €', 'gbp £',
]

# Words that indicate this is likely a real promo
PROMO_BOOST_WORDS = [
    'off', 'save', 'discount', 'deal', 'sale', 'code', 'promo',
    'free shipping', 'gift', 'extra', 'clearance', 'final',
    'limited', 'today', 'ends', 'last chance', 'hurry',
    'holiday', 'cyber', 'black friday', 'bogo', 'buy one',
]


def is_junk_text(text):
    """Check if text is likely navigation/junk"""
    text_lower = text.lower()
    
    # Too short or too long
    if len(text) < 15 or len(text) > 300:
        return True
    
    # Mostly junk phrases
    junk_count = sum(1 for phrase in JUNK_PHRASES if phrase in text_lower)
    word_count = len(text.split())
    if junk_count > 2 or (junk_count > 0 and word_count < 8):
        return True
    
    # Too many pipes/bullets (likely navigation)
    if text.count('|') > 2 or text.count('•') > 2 or text.count('›') > 2:
        return True
    
    # Mostly uppercase nav items
    if text.isupper() and len(text) > 50:
        return True
        
    return False


def score_promo_text(text):
    """Score how likely this is a real promo (higher = better)"""
    score = 0
    text_lower = text.lower()
    
    # Must have a percentage or dollar amount
    if re.search(r'\d+%', text):
        score += 30
    if re.search(r'\$\d+', text):
        score += 20
    
    # Boost for promo keywords
    for word in PROMO_BOOST_WORDS:
        if word in text_lower:
            score += 10
    
    # Boost for promo codes
    if re.search(r'code[:\s]+[A-Z0-9]+', text, re.IGNORECASE):
        score += 25
    
    # Penalty for junk
    for phrase in JUNK_PHRASES:
        if phrase in text_lower:
            score -= 15
    
    # Penalty for being too long (likely grabbed extra stuff)
    if len(text) > 150:
        score -= 10
    if len(text) > 200:
        score -= 20
        
    # Bonus for reasonable length
    if 30 < len(text) < 100:
        score += 10
    
    return score


def clean_promo_text(text):
    """Clean up promo text, removing junk"""
    # Normalize whitespace
    text = ' '.join(text.split())
    
    # Remove common prefix/suffix junk
    remove_patterns = [
        r'^(skip to content|menu|close|open)\s*',
        r'\s*(shop now|learn more|view all|see details)\.?\s*$',
        r'\s*\|\s*(shop now|learn more).*$',
        r'^\s*\d+\s+(items?|products?)\s*',
    ]
    for pattern in remove_patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    
    # Clean up punctuation
    text = re.sub(r'\s+([.,!])', r'\1', text)
    text = re.sub(r'([.,!])\s*\1+', r'\1', text)
    
    # Trim
    text = text.strip(' .-|•')
    
    return text


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
    """Extract promo code from text - ONLY when explicitly marked as a code"""
    
    # Only extract codes that are explicitly called out as codes
    patterns = [
        r'(?:code|coupon|promo)[:\s]+([A-Z0-9]{4,20})\b',
        r'(?:use|enter|apply)\s+code\s+([A-Z0-9]{4,20})\b',
        r'with\s+code\s+([A-Z0-9]{4,20})\b',
        r'\bcode\s+([A-Z0-9]{4,20})\s+(?:for|at|to)\b',
    ]
    
    text_upper = text.upper()
    
    # Minimal blacklist - only things that are definitely not codes
    blacklist = ['DEFAULT', 'TRUE', 'FALSE', 'NULL', 'UNDEFINED', 'FUNCTION', 
                 'RETURN', 'CONST', 'VAR', 'HTTP', 'HTTPS', 'HTML', 'CSS']
    
    for pattern in patterns:
        matches = re.findall(pattern, text_upper, re.IGNORECASE)
        for match in matches:
            code = match.strip()
            
            if code in blacklist:
                continue
            
            if len(code) < 4 or len(code) > 15:
                continue
            
            # Skip hex color codes (6 chars, all hex valid like FAFAF9)
            if len(code) == 6 and re.match(r'^[A-F0-9]+$', code):
                continue
            
            return code
    
    return None


def extract_popup_codes_from_scripts(soup):
    """
    Extract promo codes from inline JavaScript - catches popup/modal codes
    that are loaded dynamically (Klaviyo, Privy, Justuno, etc.)
    """
    codes_found = []
    
    # Common patterns for codes in JavaScript popup configs
    js_patterns = [
        # Direct code assignments
        r'(?:discount|promo|coupon)(?:_)?(?:c|C)ode["\']?\s*[:=]\s*["\']([A-Z0-9]{4,20})["\']',
        # Klaviyo-style
        r'coupon["\']?\s*:\s*["\']([A-Z0-9]{4,20})["\']',
        # Generic popup config
        r'code["\']?\s*:\s*["\']([A-Z0-9]{4,20})["\']',
        # Welcome popup patterns
        r'welcome(?:_)?(?:c|C)ode["\']?\s*[:=]\s*["\']([A-Z0-9]{4,20})["\']',
        # First order patterns
        r'first(?:_)?(?:o|O)rder(?:_)?(?:c|C)ode["\']?\s*[:=]\s*["\']([A-Z0-9]{4,20})["\']',
        # Spin wheel patterns
        r'prize["\']?\s*:\s*["\']([A-Z0-9]{4,20})["\']',
        # Exit intent patterns  
        r'exit(?:_)?(?:c|C)ode["\']?\s*[:=]\s*["\']([A-Z0-9]{4,20})["\']',
        # Shopify discount patterns
        r'discount["\']?\s*:\s*\{[^}]*code["\']?\s*:\s*["\']([A-Z0-9]{4,20})["\']',
        # Wheelio/spin-to-win
        r'slice["\']?\s*:\s*\{[^}]*code["\']?\s*:\s*["\']([A-Z0-9]{4,20})["\']',
        r'reward["\']?\s*:\s*["\']([A-Z0-9]{4,20})["\']',
        # Generic "offer" configs
        r'offer(?:_)?(?:c|C)ode["\']?\s*[:=]\s*["\']([A-Z0-9]{4,20})["\']',
        # Data attributes that might have codes
        r'data-(?:coupon|code|promo)["\']?\s*[:=]\s*["\']([A-Z0-9]{4,20})["\']',
        # Privy specific
        r'privy[^{]*\{[^}]*code["\']?\s*:\s*["\']([A-Z0-9]{4,20})["\']',
        # Common discount variables
        r'(?:DISCOUNT|PROMO|COUPON)_CODE\s*[:=]\s*["\']([A-Z0-9]{4,20})["\']',
        # Catch codes like WELCOME15, SAVE20 in config objects
        r'["\']([A-Z]+\d{1,3})["\'].*?(?:discount|percent|off)',
    ]
    
    blacklist = ['HTTP', 'HTTPS', 'HTML', 'CSS', 'USD', 'OFF', 'NEW', 
                'SALE', 'SHOP', 'FREE', 'BOGO', 'SIZE', 'VIEW', 'ITEM',
                'ITEMS', 'CART', 'HERE', 'WITH', 'YOUR', 'THIS', 'THAT',
                'MORE', 'LESS', 'ONLY', 'JUST', 'BEST', 'GIFT', 'NONE',
                'TRUE', 'FALSE', 'NULL', 'UNDEFINED', 'FUNCTION', 'RETURN',
                'CONST', 'VAR', 'LET', 'CLASS', 'SCRIPT', 'TYPE', 'TEXT',
                'AUTO', 'BLOCK', 'FLEX', 'GRID', 'FIXED', 'STATIC']
    
    try:
        scripts = soup.find_all('script')
        for script in scripts:
            if script.string:
                script_text = script.string
                
                # Skip if too short
                if len(script_text) < 50:
                    continue
                
                # Check for popup-related keywords first (expanded list)
                script_lower = script_text.lower()
                if not any(kw in script_lower for kw in ['popup', 'modal', 'klaviyo', 'privy', 
                                                          'justuno', 'optinmonster', 'discount',
                                                          'coupon', 'promo', 'welcome', 'signup',
                                                          'wheelio', 'spin', 'exit', 'subscribe',
                                                          'newsletter', 'offer', 'reward', 'first']):
                    continue
                
                for pattern in js_patterns:
                    matches = re.findall(pattern, script_text, re.IGNORECASE)
                    for match in matches:
                        code = match.upper()
                        if code not in blacklist and len(code) >= 4 and len(code) <= 20 and code not in codes_found:
                            # Should have at least one letter
                            if re.search(r'[A-Z]', code):
                                # Prefer codes with numbers, but accept letter-only if 6+ chars
                                if re.search(r'[0-9]', code) or len(code) >= 6:
                                    codes_found.append(code)
        
        # Also check for codes in data attributes on elements
        for el in soup.find_all(attrs={"data-coupon": True}):
            code = el.get("data-coupon", "").upper()
            if code and code not in blacklist and len(code) >= 4 and code not in codes_found:
                codes_found.append(code)
        
        for el in soup.find_all(attrs={"data-code": True}):
            code = el.get("data-code", "").upper()
            if code and code not in blacklist and len(code) >= 4 and code not in codes_found:
                codes_found.append(code)
                
    except:
        pass
    
    return codes_found


def clean_text(text, max_len=150):
    """Clean and truncate text"""
    text = clean_promo_text(text)
    return text[:max_len] + "..." if len(text) > max_len else text


def extract_image(soup, base_url):
    """Extract brand logo from page"""
    
    def normalize_url(img_url):
        if not img_url:
            return None
        if img_url.startswith('//'):
            return 'https:' + img_url
        elif img_url.startswith('/'):
            return urljoin(base_url, img_url)
        elif img_url.startswith('data:'):
            return None  # Skip data URIs
        return img_url
    
    # Priority 1: Logo-specific selectors
    logo_selectors = [
        '[class*="logo"] img',
        '[class*="Logo"] img',
        '[id*="logo"] img',
        '[id*="Logo"] img',
        'a[class*="logo"] img',
        'header a img',  # First image in header link is usually logo
        '.header img',
        '.site-header img',
        '[class*="brand"] img',
    ]
    
    for selector in logo_selectors:
        try:
            imgs = soup.select(selector)
            for img in imgs[:2]:
                src = img.get('src') or img.get('data-src') or img.get('srcset', '').split()[0]
                src = normalize_url(src)
                if src and 'data:image' not in src:
                    return src
        except:
            pass
    
    # Priority 2: SVG logo in header (common pattern)
    try:
        header = soup.select_one('header') or soup.select_one('[class*="header"]')
        if header:
            svg = header.find('svg')
            # Can't return SVG inline, so skip to next option
            
            # Try img in header
            img = header.find('img')
            if img:
                src = img.get('src') or img.get('data-src')
                src = normalize_url(src)
                if src:
                    return src
    except:
        pass
    
    # Priority 3: Apple touch icon (usually a clean logo)
    apple_icon = soup.find('link', rel='apple-touch-icon')
    if apple_icon and apple_icon.get('href'):
        src = normalize_url(apple_icon['href'])
        if src:
            return src
    
    # Priority 4: Large favicon
    for rel in ['icon', 'shortcut icon']:
        icon = soup.find('link', rel=rel)
        if icon and icon.get('href'):
            href = icon['href']
            # Skip tiny favicons, prefer larger ones
            if '32x32' not in href and '16x16' not in href:
                src = normalize_url(href)
                if src and '.ico' not in src.lower():
                    return src
    
    # Priority 5: OG image as fallback (better than nothing)
    og_image = soup.find('meta', property='og:image')
    if og_image and og_image.get('content'):
        src = normalize_url(og_image['content'])
        if src:
            return src
    
    return None


def scrape_brand(brand):
    """Scrape a single brand using requests"""
    result = {
        "brand": brand["name"],
        "url": brand["url"],
        "affiliate_url": brand.get("affiliate_url"),
        "category": brand.get("category", "apparel"),
        "tags": brand.get("tags", []),
        "promo": None,
        "code": None,
        "email_offer": None,
        "image": None,
        "error": None
    }
    
    try:
        response = requests.get(brand["url"], headers=HEADERS, timeout=15, allow_redirects=True)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract hero/product image BEFORE decomposing elements
        result["image"] = extract_image(soup, brand["url"])
        
        # =================================================================
        # CHECK FOR EMAIL SIGNUP OFFERS BEFORE REMOVING FOOTER
        # =================================================================
        email_selectors = [
            # Footer newsletter sections
            'footer [class*="newsletter"]',
            'footer [class*="signup"]',
            'footer [class*="subscribe"]',
            'footer [class*="email"]',
            '[class*="footer"] [class*="newsletter"]',
            '[class*="footer"] [class*="signup"]',
            
            # Popup/modal selectors (often contain email offers)
            '[class*="popup"]',
            '[class*="modal"]',
            '[class*="klaviyo"]',  # Popular email popup tool
            '[class*="privy"]',    # Another popular one
            '[class*="justuno"]',
            '[class*="optinmonster"]',
            '[class*="wheelio"]',
            '[class*="spin"]',     # Spin-to-win popups
            
            # General newsletter/signup areas
            '[class*="newsletter"]',
            '[class*="signup"]',
            '[class*="subscribe"]',
            '[class*="email-capture"]',
            '[class*="email-signup"]',
            '[class*="join"]',
            '[id*="newsletter"]',
            '[id*="signup"]',
            '[id*="subscribe"]',
            
            # Form areas that might have offers
            'form[action*="subscribe"]',
            'form[action*="newsletter"]',
            'form[class*="email"]',
        ]
        
        for selector in email_selectors:
            if result.get("email_offer"):
                break
            try:
                elements = soup.select(selector)[:3]
                for el in elements:
                    text = el.get_text(separator=' ', strip=True)
                    if text and len(text) > 10:
                        text_lower = text.lower()
                        # Look for email offer patterns
                        if any(word in text_lower for word in ['%', 'off', 'discount', 'save', 'free shipping']):
                            if any(word in text_lower for word in ['sign', 'join', 'subscribe', 'email', 'newsletter', 'first order', 'welcome']):
                                # Extract the offer
                                patterns = [
                                    r'(\d+%\s*off[^.!]*)',
                                    r'(save\s*\d+%[^.!]*)',
                                    r'(\d+%\s*(?:discount|savings)[^.!]*)',
                                    r'(get\s*\d+%[^.!]*)',
                                    r'(free shipping[^.!]*)',
                                    r'(\$\d+\s*off[^.!]*)',
                                ]
                                for pattern in patterns:
                                    match = re.search(pattern, text, re.IGNORECASE)
                                    if match:
                                        offer = match.group(1).strip()
                                        if 10 < len(offer) < 100:
                                            result["email_offer"] = clean_text(offer, 80)
                                            # Also try to extract code from this text
                                            if not result.get("code"):
                                                code = extract_code(text)
                                                if code:
                                                    result["code"] = code
                                            break
                                if result.get("email_offer"):
                                    break
            except:
                pass
        
        # =================================================================
        # EXTRACT CODES FROM VISIBLE POPUP/MODAL HTML
        # =================================================================
        try:
            popup_selectors = [
                '[class*="popup"]',
                '[class*="modal"]',
                '[class*="klaviyo"]',
                '[class*="privy"]',
                '[class*="justuno"]',
                '[class*="optinmonster"]',
                '[class*="wheelio"]',
                '[class*="spin-to-win"]',
                '[class*="discount-popup"]',
                '[class*="newsletter-popup"]',
                '[class*="exit-intent"]',
                '[class*="welcome-popup"]',
                '[id*="popup"]',
                '[id*="modal"]',
                '[data-popup]',
                '[data-modal]',
            ]
            
            for selector in popup_selectors:
                if result.get("code"):
                    break
                try:
                    elements = soup.select(selector)[:3]
                    for el in elements:
                        text = el.get_text(separator=' ', strip=True)
                        if text and len(text) > 5:
                            # Look for code patterns
                            code = extract_code(text)
                            if code and not result.get("code"):
                                result["code"] = code
                                # Create promo text if none exists
                                if not result.get("promo"):
                                    discount_match = re.search(r'(\d+)%', text)
                                    if discount_match:
                                        result["promo"] = f"Use code {code} for {discount_match.group(1)}% off"
                                    else:
                                        result["promo"] = f"Use code {code} for discount"
                                break
                except:
                    pass
        except:
            pass
        
        # Also check meta tags and JSON-LD for promo info
        try:
            # Some sites put promo info in meta description
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            if meta_desc and meta_desc.get('content'):
                desc = meta_desc['content']
                if re.search(r'sign.{0,10}up.{0,20}\d+%', desc, re.IGNORECASE):
                    match = re.search(r'(\d+%\s*off[^.]*)', desc, re.IGNORECASE)
                    if match and not result.get("email_offer"):
                        result["email_offer"] = clean_text(match.group(1), 80)
        except:
            pass
        
        # =================================================================
        # PARSE JSON-LD STRUCTURED DATA FOR OFFERS
        # =================================================================
        try:
            json_ld_scripts = soup.find_all('script', type='application/ld+json')
            for script in json_ld_scripts:
                try:
                    data = json.loads(script.string)
                    # Handle both single objects and arrays
                    items = data if isinstance(data, list) else [data]
                    
                    for item in items:
                        # Check for Offer schema
                        if item.get('@type') == 'Offer' or item.get('@type') == 'AggregateOffer':
                            if item.get('description') and not result.get("promo"):
                                offer_desc = item['description']
                                if matches_promo(offer_desc):
                                    result["promo"] = clean_text(offer_desc, 150)
                            if item.get('priceValidUntil'):
                                # Could track expiration from this
                                pass
                        
                        # Check for Product with offers
                        if item.get('@type') == 'Product':
                            offers = item.get('offers', {})
                            if isinstance(offers, dict):
                                offers = [offers]
                            for offer in offers:
                                if offer.get('description') and not result.get("promo"):
                                    if matches_promo(offer['description']):
                                        result["promo"] = clean_text(offer['description'], 150)
                        
                        # Check for Sale or DiscountOffer
                        if item.get('@type') in ['Sale', 'DiscountOffer', 'SpecialOffer']:
                            desc = item.get('description') or item.get('name', '')
                            if desc and matches_promo(desc) and not result.get("promo"):
                                result["promo"] = clean_text(desc, 150)
                        
                        # Check for WebSite with potentialAction containing offers
                        if item.get('@type') == 'WebSite':
                            if item.get('potentialAction'):
                                actions = item['potentialAction']
                                if isinstance(actions, dict):
                                    actions = [actions]
                                for action in actions:
                                    if action.get('description') and matches_promo(action['description']):
                                        if not result.get("promo"):
                                            result["promo"] = clean_text(action['description'], 150)
                        
                        # Check nested @graph structure (common in Shopify/WooCommerce)
                        if '@graph' in item:
                            for node in item['@graph']:
                                if node.get('@type') in ['Offer', 'AggregateOffer', 'Sale']:
                                    desc = node.get('description', '')
                                    if desc and matches_promo(desc) and not result.get("promo"):
                                        result["promo"] = clean_text(desc, 150)
                except json.JSONDecodeError:
                    pass
                except:
                    pass
        except:
            pass
        
        # =================================================================
        # PARSE OG META TAGS FOR OFFERS
        # =================================================================
        try:
            # OpenGraph tags sometimes have promo info
            og_desc = soup.find('meta', property='og:description')
            if og_desc and og_desc.get('content'):
                desc = og_desc['content']
                if matches_promo(desc) and not result.get("promo"):
                    # Only use if it looks like a real promo, not just product description
                    if re.search(r'\d+%\s*off|\bsale\b|free shipping', desc, re.IGNORECASE):
                        result["promo"] = clean_text(desc, 150)
            
            # Some sites use custom meta tags
            for meta in soup.find_all('meta'):
                name = meta.get('name', '').lower()
                prop = meta.get('property', '').lower()
                content = meta.get('content', '')
                
                if any(x in name or x in prop for x in ['promo', 'offer', 'discount', 'sale']):
                    if content and matches_promo(content) and not result.get("promo"):
                        result["promo"] = clean_text(content, 150)
                        break
        except:
            pass
        
        # =================================================================
        # EXTRACT POPUP CODES FROM JAVASCRIPT (before removing scripts)
        # =================================================================
        try:
            popup_codes = extract_popup_codes_from_scripts(soup)
            if popup_codes and not result.get("code"):
                # Use the first valid code found
                result["code"] = popup_codes[0]
                # If we found a code but no promo text yet, create a generic one
                if not result.get("promo"):
                    result["promo"] = f"Use code {popup_codes[0]} for discount"
                if not result.get("email_offer"):
                    result["email_offer"] = f"Use code {popup_codes[0]} for first order discount"
        except:
            pass
        
        # =================================================================
        # EXTRACT CODES FROM COPY BUTTONS AND DATA ATTRIBUTES
        # =================================================================
        if not result.get("code"):
            try:
                # Look for copy-to-clipboard elements
                copy_selectors = [
                    '[data-clipboard-text]',
                    '[data-copy]',
                    '[data-code]',
                    '[data-coupon]',
                    '[data-promo-code]',
                    '[class*="copy-code"]',
                    '[class*="coupon-code"]',
                    '[class*="promo-code"]',
                    '[class*="discount-code"]',
                    'button[class*="copy"]',
                    '[onclick*="copy"]',
                ]
                
                blacklist = ['HTTP', 'HTTPS', 'USD', 'OFF', 'NEW', 'SALE', 'SHOP', 'FREE']
                
                for selector in copy_selectors:
                    try:
                        elements = soup.select(selector)[:5]
                        for el in elements:
                            # Check data attributes
                            code = (el.get('data-clipboard-text') or 
                                   el.get('data-copy') or 
                                   el.get('data-code') or 
                                   el.get('data-coupon') or 
                                   el.get('data-promo-code') or '').strip().upper()
                            
                            if not code:
                                # Check element text
                                code = el.get_text(strip=True).upper()
                            
                            if code and len(code) >= 4 and len(code) <= 20 and code not in blacklist:
                                # Validate it looks like a code
                                if re.match(r'^[A-Z0-9]+$', code) and re.search(r'[A-Z]', code):
                                    result["code"] = code
                                    if not result.get("promo"):
                                        result["promo"] = f"Use code {code} for discount"
                                    break
                        if result.get("code"):
                            break
                    except:
                        pass
            except:
                pass
        
        # =================================================================
        # NOW REMOVE SCRIPT/STYLE/NAV/FOOTER FOR MAIN PROMO SCANNING
        # =================================================================
        for element in soup(['script', 'style', 'noscript', 'nav', 'footer']):
            element.decompose()
        
        # Remove currency/country selectors (Shopify sites have huge lists)
        currency_selectors = [
            '[class*="currency"]',
            '[class*="country-selector"]',
            '[class*="locale-selector"]',
            '[class*="localization"]',
            '[id*="currency"]',
            '[id*="country"]',
            '.disclosure',  # Shopify disclosure menus
        ]
        for selector in currency_selectors:
            try:
                for el in soup.select(selector):
                    el.decompose()
            except:
                pass
        
        # Collect all candidate promo texts with scores
        candidates = []
        
        # Priority 1: Announcement bars (most likely to have promos)
        announcement_selectors = [
            '[class*="announcement"]',
            '[class*="promo-bar"]',
            '[class*="top-bar"]',
            '[class*="topbar"]',
            '[class*="header-message"]',
            '[class*="site-message"]',
            '[class*="marquee"]',
            '[class*="ticker"]',
            '[id*="announcement"]',
            '[id*="promo"]',
            '[data-section-type="announcement"]',
            '.announcement-bar',
            '.promo-banner',
        ]
        
        for selector in announcement_selectors:
            try:
                elements = soup.select(selector)[:3]
                for el in elements:
                    # Try to get just the text content, not nested navs
                    for nav in el.find_all(['nav', 'ul', 'select']):
                        nav.decompose()
                    text = el.get_text(separator=' ', strip=True)
                    if text and matches_promo(text) and not is_junk_text(text):
                        score = score_promo_text(text) + 20  # Bonus for announcement bar
                        candidates.append((text, score, 'announcement'))
                        
                        # IMMEDIATELY try to extract code from announcement bar
                        if not result.get("code"):
                            code = extract_code(text)
                            if code:
                                result["code"] = code
            except:
                pass
        
        # Priority 2: Banner/hero sections
        banner_selectors = [
            '[class*="banner"]',
            '[class*="hero"]',
            '[class*="sale"]',
            '[class*="offer"]',
            '[class*="discount"]',
            '[class*="promo"]',
        ]
        
        for selector in banner_selectors:
            try:
                elements = soup.select(selector)[:3]
                for el in elements:
                    # Skip if it's a nav or has too many links
                    if el.name == 'nav' or len(el.find_all('a')) > 5:
                        continue
                    text = el.get_text(separator=' ', strip=True)
                    if text and matches_promo(text) and not is_junk_text(text):
                        score = score_promo_text(text)
                        candidates.append((text, score, 'banner'))
            except:
                pass
        
        # Priority 3: Look for specific promo text patterns anywhere
        # Find elements with percentage discounts
        all_text_elements = soup.find_all(string=re.compile(r'\d+%\s*(off|sale|discount|save)', re.IGNORECASE))
        for text_el in all_text_elements[:10]:
            parent = text_el.find_parent()
            if parent:
                text = parent.get_text(separator=' ', strip=True)
                if text and 15 < len(text) < 200 and not is_junk_text(text):
                    score = score_promo_text(text)
                    candidates.append((text, score, 'text_match'))
        
        # Select best candidate
        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            best_text, best_score, source = candidates[0]
            
            # Only use if score is decent
            if best_score > 10:
                result["promo"] = clean_text(best_text)
                code = extract_code(best_text)
                if code:
                    result["code"] = code
        
        # Fallback: Check remaining body for email offers if not found yet
        if not result.get("email_offer"):
            for selector in ['[class*="newsletter"]', '[class*="signup"]', '[class*="subscribe"]']:
                try:
                    elements = soup.select(selector)[:2]
                    for el in elements:
                        text = el.get_text(separator=' ', strip=True)
                        if text and '%' in text:
                            match = re.search(r'(\d+%\s*off[^.!]*)', text, re.IGNORECASE)
                            if match:
                                result["email_offer"] = clean_text(match.group(1), 80)
                                break
                    if result.get("email_offer"):
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
    print(f"\n{'='*60}")
    print(f"🔄 SKRATCH RADAR - Starting scan at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📡 Scanning {len(BRANDS)} brands...")
    print(f"{'='*60}")
    
    results = []
    clearance_results = []
    impact_deals = []
    success_count = 0
    error_count = 0
    
    for i, brand in enumerate(BRANDS, 1):
        print(f"  [{i}/{len(BRANDS)}] {brand['name']}...", end=" ", flush=True)
        result = scrape_brand(brand)
        
        if result["error"]:
            print(f"❌ {result['error'][:30]}")
            error_count += 1
        elif result["promo"]:
            code_str = f" (code: {result['code']})" if result['code'] else ""
            print(f"✓ Found promo{code_str}")
            success_count += 1
            results.append(result)
        else:
            print("○ No promo")
            results.append(result)
    
    # Now scan sale pages (wrapped in try/except so it doesn't break main scan)
    print(f"\n{'='*60}")
    print(f"🏷️  Scanning sale pages...")
    print(f"{'='*60}")
    
    try:
        clearance_results = scan_sale_pages(BRANDS)
    except Exception as e:
        print(f"⚠️  Sale page scan failed: {e}")
        clearance_results = []
    
    # Fetch Impact deals
    print(f"\n{'='*60}")
    print(f"🔗 Fetching Impact Radius deals...")
    print(f"{'='*60}")
    
    try:
        if impact_api:
            impact_deals = impact_api.get_all_deals()
            print(f"✅ Found {len(impact_deals)} deals from Impact")
        else:
            print("⚠️  Impact API not available")
    except Exception as e:
        print(f"⚠️  Impact deals fetch failed: {e}")
        impact_deals = []
    
    print(f"\n{'='*60}")
    print(f"✅ Scan complete: {success_count} promos, {len(clearance_results)} clearance, {len(impact_deals)} impact deals, {error_count} errors")
    print(f"{'='*60}\n")
    
    # Always save main results even if clearance/impact fails
    if results:
        save_data(results, clearance_results, impact_deals)
    
    return results


def mine_sitemap_for_sale_urls(base_url, max_urls=5):
    """Parse sitemap.xml to find sale/clearance/outlet URLs we might be missing"""
    parsed = urlparse(base_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    
    sale_keywords = ['sale', 'clearance', 'outlet', 'markdown', 'discount', 'deals', 
                     'last-chance', 'final-sale', 'special', 'promo', 'offers']
    
    found_urls = []
    
    try:
        # Try common sitemap locations
        sitemap_urls = [
            f"{base}/sitemap.xml",
            f"{base}/sitemap_index.xml",
            f"{base}/sitemap-index.xml",
            f"{base}/sitemaps/sitemap.xml",
        ]
        
        sitemap_content = None
        for sitemap_url in sitemap_urls:
            try:
                response = requests.get(sitemap_url, headers=HEADERS, timeout=10)
                if response.status_code == 200 and '<?xml' in response.text[:100]:
                    sitemap_content = response.text
                    break
            except:
                continue
        
        if not sitemap_content:
            return []
        
        # Parse sitemap XML
        soup = BeautifulSoup(sitemap_content, 'xml')
        
        # Check if this is a sitemap index (contains other sitemaps)
        sitemap_tags = soup.find_all('sitemap')
        if sitemap_tags:
            # This is an index, look for collection/page sitemaps
            for sitemap in sitemap_tags:
                loc = sitemap.find('loc')
                if loc:
                    sitemap_child_url = loc.text
                    # Look for collection or page sitemaps
                    if any(x in sitemap_child_url.lower() for x in ['collection', 'page', 'categor']):
                        try:
                            child_response = requests.get(sitemap_child_url, headers=HEADERS, timeout=10)
                            if child_response.status_code == 200:
                                child_soup = BeautifulSoup(child_response.text, 'xml')
                                for url_tag in child_soup.find_all('url'):
                                    loc = url_tag.find('loc')
                                    if loc:
                                        url_text = loc.text.lower()
                                        if any(kw in url_text for kw in sale_keywords):
                                            found_urls.append(loc.text)
                        except:
                            continue
        
        # Also check direct URLs in sitemap
        for url_tag in soup.find_all('url'):
            loc = url_tag.find('loc')
            if loc:
                url_text = loc.text.lower()
                if any(kw in url_text for kw in sale_keywords):
                    found_urls.append(loc.text)
        
        # Dedupe and limit
        found_urls = list(set(found_urls))[:max_urls]
        
        if found_urls:
            print(f"  📍 Sitemap: Found {len(found_urls)} sale URLs")
        
        return found_urls
        
    except Exception as e:
        return []


def get_sale_urls(base_url):
    """Generate possible sale page URLs from a base URL"""
    parsed = urlparse(base_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    
    # Common sale page patterns
    patterns = [
        '/collections/sale',
        '/sale',
        '/clearance',
        '/collections/clearance',
        '/outlet',
        '/collections/outlet',
        '/markdown',
        '/collections/markdown',
        '/last-chance',
        '/collections/last-chance',
        '/final-sale',
        '/collections/final-sale',
    ]
    
    return [base + p for p in patterns]


def scrape_sale_page(brand, sale_url):
    """Scrape a sale page for banner/headline text"""
    try:
        response = requests.get(sale_url, headers=HEADERS, timeout=10, allow_redirects=True)
        
        # Check if page exists (not 404, not redirect to homepage)
        if response.status_code != 200:
            return None
        
        # Check if we got redirected to homepage (common for non-existent sale pages)
        final_url = response.url
        original_parsed = urlparse(sale_url)
        final_parsed = urlparse(final_url)
        
        # If redirected to root or very different page, skip
        if final_parsed.path in ['/', ''] and original_parsed.path not in ['/', '']:
            return None
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Look for sale banners/headlines
        sale_selectors = [
            '[class*="collection-header"] h1',
            '[class*="collection-title"]',
            '[class*="page-title"]',
            '[class*="hero"] h1',
            '[class*="hero"] h2',
            '[class*="banner"] h1',
            '[class*="banner"] h2',
            '[class*="sale"] h1',
            '[class*="sale"] h2',
            'h1[class*="title"]',
            '.collection-hero__title',
            '.page-header h1',
            'main h1',
        ]
        
        promo_text = None
        
        for selector in sale_selectors:
            try:
                el = soup.select_one(selector)
                if el:
                    text = el.get_text(strip=True)
                    if text and len(text) > 3 and len(text) < 150:
                        # Skip generic titles
                        if text.lower() not in ['sale', 'shop', 'products', 'all', 'collection']:
                            promo_text = text
                            break
            except:
                pass
        
        # Also look for discount text in the page
        if not promo_text or 'sale' in promo_text.lower() and '%' not in promo_text:
            discount_patterns = [
                r'up to (\d+)% off',
                r'save (\d+)%',
                r'(\d+)% off',
            ]
            page_text = soup.get_text()
            for pattern in discount_patterns:
                match = re.search(pattern, page_text, re.IGNORECASE)
                if match:
                    pct = int(match.group(1))
                    # Sanity check - ignore absurd percentages
                    if pct > 0 and pct <= 90:
                        if promo_text:
                            promo_text = f"{promo_text} - Up to {pct}% off"
                        else:
                            promo_text = f"Up to {pct}% off"
                        break
        
        if promo_text:
            # Clean up ugly prefixes
            promo_text = re.sub(r'^Collection[:\s]*', '', promo_text, flags=re.IGNORECASE)
            promo_text = re.sub(r'^Sale[:\s]*Collection[:\s]*', 'Sale - ', promo_text, flags=re.IGNORECASE)
            promo_text = promo_text.strip(' -:')
            
            # Extract discount percentage if present
            discount_match = re.search(r'(\d+)%', promo_text)
            discount = None
            if discount_match:
                pct = int(discount_match.group(1))
                # Only use if reasonable (1-90%)
                if 0 < pct <= 90:
                    discount = pct
            
            return {
                "brand": brand["name"],
                "url": sale_url,
                "affiliate_url": brand.get("affiliate_url"),
                "category": brand.get("category", "apparel"),
                "promo": clean_text(promo_text, 100),
                "discount": discount,
                "type": "clearance"
            }
            
    except:
        pass
    
    return None


def scan_sale_pages(brands):
    """Scan sale pages for all brands"""
    clearance = []
    
    # Skip these for sale page scanning - too noisy or structured differently
    skip_domains = ['amazon.com', 'golf.com/gear', 'dickssportinggoods.com', 'pgatoursuperstore.com', 'golfgalaxy.com']
    
    for brand in brands:
        # Skip big retailers
        if any(domain in brand["url"] for domain in skip_domains):
            continue
        
        # Get standard sale URLs + any found in sitemap
        sale_urls = get_sale_urls(brand["url"])
        sitemap_urls = mine_sitemap_for_sale_urls(brand["url"], max_urls=3)
        
        # Combine and dedupe
        all_sale_urls = list(set(sale_urls + sitemap_urls))
        
        for sale_url in all_sale_urls[:5]:  # Check up to 5 URLs per brand
            result = scrape_sale_page(brand, sale_url)
            if result:
                print(f"  🏷️  {brand['name']}: {result['promo'][:50]}")
                clearance.append(result)
                break  # Found one, move to next brand
    
    return clearance


def save_data(promos, clearance=None, impact_deals=None):
    """Save scraped data to file with freshness tracking"""
    active_promos = [p for p in promos if p.get("promo")]
    
    # Load deal history
    history = load_deal_history()
    
    # Update history and get fresh promos
    history, fresh_promos = update_deal_history(active_promos, history)
    
    # Also process clearance deals
    fresh_clearance = []
    if clearance:
        history, fresh_clearance = update_deal_history(clearance, history)
    
    # Also process impact deals
    fresh_impact = []
    if impact_deals:
        history, fresh_impact = update_deal_history(impact_deals, history)
    
    # Save updated history
    save_deal_history(history)
    
    # Get current critical hit index and increment
    current_data = load_data()
    critical_hit_index = current_data.get("criticalHitIndex", 0) + 1
    
    # Count new deals
    new_promos = sum(1 for p in fresh_promos if p.get("is_new"))
    new_clearance = sum(1 for c in fresh_clearance if c.get("is_new"))
    new_impact = sum(1 for d in fresh_impact if d.get("is_new"))
    
    data = {
        "lastUpdated": datetime.now().isoformat(),
        "criticalHitIndex": critical_hit_index,
        "promos": fresh_promos,
        "codes": [
            {
                "brand": p["brand"], 
                "code": p["code"], 
                "discount": p["promo"][:60], 
                "url": p.get("url"), 
                "affiliate_url": p.get("affiliate_url"),
                "is_new": p.get("is_new", False),
                "first_seen": p.get("first_seen"),
                "expires": p.get("expires")
            }
            for p in fresh_promos if p.get("code")
        ],
        "emailOffers": [
            {
                "brand": p["brand"], 
                "offer": p["email_offer"], 
                "method": "Website", 
                "url": p.get("url"), 
                "affiliate_url": p.get("affiliate_url")
            }
            for p in promos if p.get("email_offer")
        ],
        "clearance": fresh_clearance,
        "impactDeals": fresh_impact
    }
    
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)
    
    print(f"💾 Saved: {len(fresh_promos)} promos ({new_promos} new), {len(data['codes'])} codes, {len(data['emailOffers'])} email offers, {len(fresh_clearance)} clearance ({new_clearance} new), {len(fresh_impact)} impact deals ({new_impact} new)")


def load_data():
    """Load data from file or return defaults"""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE) as f:
                data = json.load(f)
                # Ensure keys exist (for backward compatibility)
                if "impactDeals" not in data:
                    data["impactDeals"] = []
                if "criticalHitIndex" not in data:
                    data["criticalHitIndex"] = 0
                return data
        except:
            pass
    
    return {
        "lastUpdated": datetime.now().isoformat(),
        "criticalHitIndex": 0,
        "promos": [],
        "codes": [],
        "emailOffers": [],
        "clearance": [],
        "impactDeals": []
    }


# =============================================================================
# FLASK APP
# =============================================================================
app = Flask(__name__, static_folder='.')
app.secret_key = os.environ.get("SECRET_KEY", "skratch-radar-secret-key-change-me")
CORS(app)

@app.route('/')
def index():
    return send_from_directory('.', 'golf_promo_radar.html')


@app.route('/widget')
def widget():
    return send_from_directory('.', 'widget.html')

@app.route('/api/promos')
def get_promos():
    return jsonify(load_data())

@app.route('/api/refresh', methods=['POST'])
def trigger_refresh():
    thread = threading.Thread(target=run_scraper)
    thread.start()
    return jsonify({"status": "refresh_started", "brand_count": len(BRANDS)})

@app.route('/api/status')
def status():
    return jsonify({
        "status": "ok",
        "data_file_exists": os.path.exists(DATA_FILE),
        "brand_count": len(BRANDS),
        "refresh_interval_minutes": REFRESH_INTERVAL_MINUTES
    })


# =============================================================================
# EMBED WIDGET
# =============================================================================
@app.route('/embed.js')
def embed_js():
    """Embeddable widget script for Skratch/GolfWRX articles"""
    js = '''
(function() {
    const container = document.getElementById('skratch-radar-widget');
    if (!container) return;
    
    const style = document.createElement('style');
    style.textContent = `
        .sr-widget { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0a0a0a; border: 1px solid #1a1a1a; border-radius: 8px; padding: 16px; max-width: 400px; }
        .sr-widget * { box-sizing: border-box; }
        .sr-header { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; padding-bottom: 12px; border-bottom: 1px solid #1a1a1a; }
        .sr-logo { width: 24px; height: 24px; background: #00ff41; border-radius: 50%; display: flex; align-items: center; justify-content: center; }
        .sr-logo svg { width: 14px; height: 14px; }
        .sr-title { color: #fff; font-weight: 700; font-size: 14px; }
        .sr-live { color: #00ff41; font-size: 10px; font-weight: 600; margin-left: auto; }
        .sr-deal { display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid #1a1a1a; text-decoration: none; }
        .sr-deal:last-child { border-bottom: none; }
        .sr-deal:hover .sr-brand { color: #00ff41; }
        .sr-badge { background: #00ff41; color: #000; font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 3px; }
        .sr-info { flex: 1; min-width: 0; }
        .sr-brand { color: #fff; font-weight: 600; font-size: 13px; transition: color 0.2s; }
        .sr-promo { color: #888; font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .sr-cta { text-align: center; margin-top: 12px; }
        .sr-cta a { color: #00ff41; font-size: 12px; text-decoration: none; font-weight: 600; }
        .sr-cta a:hover { text-decoration: underline; }
    `;
    document.head.appendChild(style);
    
    fetch('WIDGET_URL/api/promos')
        .then(r => r.json())
        .then(data => {
            const deals = (data.promos || [])
                .filter(p => p.promo && p.promo.match(/\\d+%/))
                .sort((a, b) => {
                    const aDiscount = parseInt(a.promo.match(/(\\d+)%/)?.[1] || 0);
                    const bDiscount = parseInt(b.promo.match(/(\\d+)%/)?.[1] || 0);
                    return bDiscount - aDiscount;
                })
                .slice(0, 5);
            
            container.innerHTML = `
                <div class="sr-widget">
                    <div class="sr-header">
                        <div class="sr-logo"><svg viewBox="0 0 24 24" fill="none" stroke="#000" stroke-width="3"><circle cx="12" cy="12" r="10"/><line x1="12" y1="2" x2="12" y2="12"/></svg></div>
                        <span class="sr-title">GOLF DEALS RADAR</span>
                        <span class="sr-live">● LIVE</span>
                    </div>
                    ${deals.map(d => {
                        const discount = d.promo.match(/(\\d+)%/)?.[1] || '';
                        const link = d.affiliate_url || d.url;
                        return `<a href="${link}" target="_blank" rel="noopener" class="sr-deal">
                            <span class="sr-badge">${discount}%</span>
                            <div class="sr-info">
                                <div class="sr-brand">${d.brand}</div>
                                <div class="sr-promo">${d.promo}</div>
                            </div>
                        </a>`;
                    }).join('')}
                    <div class="sr-cta"><a href="WIDGET_URL" target="_blank">View All Deals →</a></div>
                </div>
            `;
        })
        .catch(e => console.error('Radar widget error:', e));
})();
'''
    # Replace WIDGET_URL with actual URL
    base_url = request.url_root.rstrip('/')
    js = js.replace('WIDGET_URL', base_url)
    
    return Response(js, mimetype='application/javascript')


@app.route('/embed')
def embed_demo():
    """Demo page showing how to embed the widget"""
    base_url = request.url_root.rstrip('/')
    return f'''<!DOCTYPE html>
<html>
<head>
    <title>Skratch Radar - Embed Widget</title>
    <style>
        body {{ font-family: -apple-system, sans-serif; max-width: 800px; margin: 50px auto; padding: 20px; background: #f5f5f5; }}
        h1 {{ color: #333; }}
        pre {{ background: #1a1a1a; color: #00ff41; padding: 20px; border-radius: 8px; overflow-x: auto; }}
        .demo {{ margin: 40px 0; padding: 40px; background: #fff; border-radius: 8px; }}
    </style>
</head>
<body>
    <h1>Embed Golf Deals Radar</h1>
    <p>Add this widget to any Skratch or GolfWRX article:</p>
    
    <pre>&lt;div id="skratch-radar-widget"&gt;&lt;/div&gt;
&lt;script src="{base_url}/embed.js"&gt;&lt;/script&gt;</pre>
    
    <h2>Live Preview:</h2>
    <div class="demo">
        <div id="skratch-radar-widget"></div>
        <script src="{base_url}/embed.js"></script>
    </div>
</body>
</html>'''


# =============================================================================
# SEO LANDING PAGES
# =============================================================================
@app.route('/deals')
def deals_index():
    """SEO index page listing all brands"""
    return send_from_directory('.', 'deals_index.html')


@app.route('/deals/<brand_slug>')
def brand_deals_page(brand_slug):
    """SEO landing page for specific brand deals"""
    return send_from_directory('.', 'brand_deals.html')


@app.route('/api/brands')
def get_brands():
    """Get list of all brands for SEO pages"""
    brand_list = []
    for brand in BRANDS:
        slug = brand["name"].lower().replace(" ", "-").replace("/", "-").replace(".", "")
        brand_list.append({
            "name": brand["name"],
            "slug": slug,
            "url": brand["url"],
            "category": brand.get("category", ""),
            "affiliate_url": brand.get("affiliate_url", "")
        })
    return jsonify({"brands": brand_list})


@app.route('/api/deals/<brand_slug>')
def get_brand_deals(brand_slug):
    """Get deals for a specific brand"""
    data = load_data()
    
    # Find matching brand
    brand_name = None
    brand_info = None
    for brand in BRANDS:
        slug = brand["name"].lower().replace(" ", "-").replace("/", "-").replace(".", "")
        if slug == brand_slug:
            brand_name = brand["name"]
            brand_info = brand
            break
    
    if not brand_name:
        return jsonify({"error": "Brand not found"}), 404
    
    # Find all deals for this brand
    promos = [p for p in data.get("promos", []) if p.get("brand") == brand_name]
    codes = [c for c in data.get("codes", []) if c.get("brand") == brand_name]
    clearance = [c for c in data.get("clearance", []) if c.get("brand") == brand_name]
    email_offers = [e for e in data.get("emailOffers", []) if e.get("brand") == brand_name]
    impact_deals = [i for i in data.get("impactDeals", []) if brand_name.lower() in i.get("brand", "").lower()]
    
    return jsonify({
        "brand": brand_name,
        "slug": brand_slug,
        "url": brand_info.get("url", ""),
        "affiliate_url": brand_info.get("affiliate_url", ""),
        "category": brand_info.get("category", ""),
        "promos": promos,
        "codes": codes,
        "clearance": clearance,
        "email_offers": email_offers,
        "impact_deals": impact_deals,
        "last_updated": data.get("lastUpdated")
    })


# =============================================================================
# ADMIN DASHBOARD ROUTES
# =============================================================================
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "skratch2024")  # Set in Railway env vars

def check_admin_auth():
    """Check if request has valid admin auth"""
    # Check session
    if session.get('admin_authenticated'):
        return True
    # Check header (for API calls)
    auth_header = request.headers.get('X-Admin-Password')
    if auth_header == ADMIN_PASSWORD:
        return True
    return False


@app.route('/admin')
def admin_dashboard():
    if not session.get('admin_authenticated'):
        return send_from_directory('.', 'admin_login.html')
    return send_from_directory('.', 'admin_dashboard.html')


@app.route('/admin/login', methods=['POST'])
def admin_login():
    data = request.get_json() or {}
    password = data.get('password', '')
    
    if password == ADMIN_PASSWORD:
        session['admin_authenticated'] = True
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Invalid password"}), 401


@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_authenticated', None)
    return jsonify({"success": True})


@app.route('/api/admin/stats')
def admin_stats():
    """Get performance stats from Impact"""
    if not check_admin_auth():
        return jsonify({"error": "Unauthorized"}), 401
    if not impact_api:
        return jsonify({"error": "Impact API not configured"}), 500
    
    try:
        report = impact_api.get_performance_report(days=30)
        return jsonify(report)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/campaigns')
def admin_campaigns():
    """Get all campaigns with tracking links"""
    if not check_admin_auth():
        return jsonify({"error": "Unauthorized"}), 401
    if not impact_api:
        return jsonify({"error": "Impact API not configured"}), 500
    
    try:
        campaigns = impact_api.get_campaigns(force_refresh=True)
        return jsonify({"campaigns": campaigns})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/actions')
def admin_actions():
    """Get recent conversion actions"""
    if not check_admin_auth():
        return jsonify({"error": "Unauthorized"}), 401
    if not impact_api:
        return jsonify({"error": "Impact API not configured"}), 500
    
    try:
        actions = impact_api.get_actions()
        return jsonify({"actions": actions, "count": len(actions)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/radar-stats')
def radar_stats():
    """Get Radar-specific stats"""
    if not check_admin_auth():
        return jsonify({"error": "Unauthorized"}), 401
    
    data = load_data()
    
    # Count by category
    category_counts = {}
    for promo in data.get("promos", []):
        cat = promo.get("category", "unknown")
        category_counts[cat] = category_counts.get(cat, 0) + 1
    
    # Count affiliate-linked vs not
    with_affiliate = sum(1 for p in data.get("promos", []) if p.get("affiliate_url"))
    without_affiliate = len(data.get("promos", [])) - with_affiliate
    
    return jsonify({
        "total_promos": len(data.get("promos", [])),
        "total_codes": len(data.get("codes", [])),
        "total_email_offers": len(data.get("emailOffers", [])),
        "total_clearance": len(data.get("clearance", [])),
        "total_impact_deals": len(data.get("impactDeals", [])),
        "with_affiliate_link": with_affiliate,
        "without_affiliate_link": without_affiliate,
        "by_category": category_counts,
        "last_updated": data.get("lastUpdated"),
        "total_brands_monitored": len(BRANDS)
    })


# =============================================================================
# MAIN
# =============================================================================
if __name__ == "__main__":
    print("\n" + "="*60)
    print("⛳ SKRATCH RADAR - Golf Promo Intelligence")
    print(f"📡 Monitoring {len(BRANDS)} brands")
    print("="*60)
    
    # Run initial scrape in background
    print(f"\n🔄 Starting initial scan...")
    thread = threading.Thread(target=run_scraper)
    thread.start()
    
    # Set up scheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(run_scraper, 'interval', minutes=REFRESH_INTERVAL_MINUTES)
    scheduler.start()
    print(f"⏰ Auto-refresh every {REFRESH_INTERVAL_MINUTES} minutes")
    
    print(f"\n🌐 Server starting at http://localhost:{PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False)
