#!/usr/bin/env python3
"""
SKRATCH RADAR - Golf Promo Scraper Backend
Scans 170+ golf brands for promos, codes, and email offers
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
REFRESH_INTERVAL_MINUTES = 5
DATA_FILE = "promo_data.json"
PORT = int(os.environ.get("PORT", 5000))

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
]

# Merge affiliate links into brands list
BRANDS = merge_affiliate_links(BRANDS)

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
    """Extract promo code from text"""
    patterns = [
        r'(?:code|promo|use|enter|coupon)[:\s]+([A-Z0-9]{4,20})',
        r'(?:code|promo)[:\s]*"?([A-Z0-9]{4,20})"?',
    ]
    text_upper = text.upper()
    for pattern in patterns:
        match = re.search(pattern, text_upper)
        if match:
            code = match.group(1)
            # Filter out common false positives
            blacklist = ['HTTP', 'HTTPS', 'HTML', 'CSS', 'USD', 'OFF', 'NEW', 
                        'SALE', 'SHOP', 'FREE', 'BOGO', 'SIZE', 'VIEW', 'ITEM',
                        'ITEMS', 'CART', 'HERE', 'WITH', 'YOUR', 'THIS', 'THAT',
                        'MORE', 'LESS', 'ONLY', 'JUST', 'BEST', 'GIFT', 'NONE']
            if code not in blacklist and len(code) >= 4:
                return code
    return None


def clean_text(text, max_len=150):
    """Clean and truncate text"""
    text = clean_promo_text(text)
    return text[:max_len] + "..." if len(text) > max_len else text


def extract_image(soup, base_url):
    """Extract brand logo from page"""
    from urllib.parse import urljoin
    
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
        
        # Remove script/style/nav elements
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
        
        # Check for email signup offers (separate from main promo)
        email_selectors = ['[class*="newsletter"]', '[class*="signup"]', '[class*="subscribe"]', '[class*="email-capture"]']
        for selector in email_selectors:
            try:
                elements = soup.select(selector)[:2]
                for el in elements:
                    text = el.get_text(separator=' ', strip=True)
                    if text:
                        for pattern in EMAIL_PATTERNS:
                            if re.search(pattern, text.lower()):
                                # Extract just the offer part
                                match = re.search(r'(\d+%[^.]*(?:off|discount|order)[^.]*)', text, re.IGNORECASE)
                                if match:
                                    result["email_offer"] = clean_text(match.group(1), 80)
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
    print(f"\n{'='*60}")
    print(f"🔄 SKRATCH RADAR - Starting scan at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📡 Scanning {len(BRANDS)} brands...")
    print(f"{'='*60}")
    
    results = []
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
    
    print(f"\n{'='*60}")
    print(f"✅ Scan complete: {success_count} promos, {error_count} errors")
    print(f"{'='*60}\n")
    
    if results:
        save_data(results)
    
    return results


def save_data(promos):
    """Save scraped data to file"""
    active_promos = [p for p in promos if p.get("promo")]
    
    data = {
        "lastUpdated": datetime.now().isoformat(),
        "promos": active_promos,
        "codes": [
            {"brand": p["brand"], "code": p["code"], "discount": p["promo"][:60]}
            for p in active_promos if p.get("code")
        ],
        "emailOffers": [
            {"brand": p["brand"], "offer": p["email_offer"], "method": "Website"}
            for p in promos if p.get("email_offer")
        ]
    }
    
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)
    
    print(f"💾 Saved: {len(active_promos)} promos, {len(data['codes'])} codes, {len(data['emailOffers'])} email offers")


def load_data():
    """Load data from file or return defaults"""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE) as f:
                return json.load(f)
        except:
            pass
    
    return {
        "lastUpdated": datetime.now().isoformat(),
        "promos": [],
        "codes": [],
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
