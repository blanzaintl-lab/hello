"""
Facebook Marketplace Auto Lister v7
====================================
- 5 Profile Slots with dedicated proxy IPs per profile
- Cookie-based login (no email/password)
- Human-like behavior: scrolling, random likes, news feed browsing
- Smart scheduling: 1 listing per hour, max 3 per 24 hours per profile
- City-specific listings: each profile lists in its assigned region only
- Heavy browsing between listings to appear natural
- Browser history building for each profile
- Persistent cookies and profiles

Usage:
    python server.py
    Then open http://localhost:8000

Install:
    pip install fastapi uvicorn playwright aiofiles
    python -m playwright install chromium
"""

import asyncio
import json
import os
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── Load Data ─────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
COOKIES_DIR = BASE_DIR / "cookies"
COOKIES_DIR.mkdir(exist_ok=True)
PROFILES_DIR = BASE_DIR / "browser_profiles"
PROFILES_DIR.mkdir(exist_ok=True)
SCHEDULE_DIR = BASE_DIR / "data"
SCHEDULE_DIR.mkdir(exist_ok=True)

MAX_SLOTS = 5
MAX_LISTINGS_PER_DAY = 3
LISTING_INTERVAL_MINUTES = 60

with open(BASE_DIR / "data" / "cities.json", "r") as f:
    ALL_CITIES = json.load(f)

with open(BASE_DIR / "data" / "categories.json", "r") as f:
    ALL_CATEGORIES = json.load(f)

with open(BASE_DIR / "data" / "city_groups.json", "r") as f:
    CITY_GROUPS = json.load(f)

CONDITIONS = ["New", "Used - Like New", "Used - Good", "Used - Fair"]

# ── Browsing URLs for human behavior ─────────────────────────────

BROWSE_URLS = [
    "https://www.facebook.com/",
    "https://www.facebook.com/marketplace/",
    "https://www.facebook.com/watch/",
    "https://www.facebook.com/groups/feed/",
    "https://www.facebook.com/news",
    "https://www.facebook.com/gaming/",
    "https://www.facebook.com/events/",
]

SEARCH_TERMS = [
    "furniture near me", "used cars", "electronics for sale",
    "free stuff", "apartment for rent", "bikes for sale",
    "iphone", "laptop deals", "couch", "desk for sale",
    "garden tools", "pet supplies", "kitchen appliances",
    "gaming console", "books", "clothing", "shoes",
    "home decor", "fitness equipment", "musical instruments",
]

HISTORY_URLS = [
    "https://www.google.com/search?q=weather+today",
    "https://www.google.com/search?q=news+today",
    "https://www.google.com/search?q=sports+scores",
    "https://www.google.com/search?q=recipes",
    "https://www.google.com/search?q=movie+reviews",
    "https://www.youtube.com/",
    "https://www.reddit.com/",
    "https://www.amazon.com/",
    "https://www.wikipedia.org/",
    "https://www.ebay.com/",
    "https://www.craigslist.org/",
    "https://www.google.com/maps",
    "https://www.yelp.com/",
    "https://www.linkedin.com/",
    "https://www.instagram.com/",
]

# ── Description Templates ─────────────────────────────────────────

DESC_TEMPLATES = [
    "Professional {title} available now! High-quality work delivered by experienced professionals. Contact us today for a free consultation. Serving {city}, {state} and surrounding areas.",
    "Looking for {title}? We offer top-notch services with fast turnaround and competitive pricing. Based in {city}, {state}. 100% satisfaction guaranteed!",
    "Get premium {title} right here in {city}, {state}! Our team of experts delivers exceptional results every time. Affordable rates, quick delivery. Message now!",
    "Need {title}? You've come to the right place! We specialize in delivering high-quality work that exceeds expectations. Serving the {city} area. Contact us!",
    "Expert {title} services in {city}, {state}. We combine creativity and skill to deliver outstanding results. Whether it's a simple or complex project, we've got you covered!",
    "Transform your ideas with our professional {title}. Creative solutions that help your business grow. Based in {city}, {state}. Satisfaction guaranteed!",
    "Premium {title} with a focus on quality and customer satisfaction. Years of experience serving clients in {city}, {state} and beyond. Let us help you achieve your goals!",
    "{title} - Fast, reliable, and affordable. We take pride in delivering work that meets the highest standards. Serving {city}, {state}. Contact today to get started!",
    "Discover quality {title} in {city}, {state}! We deliver professional results on time, every time. Our clients love our attention to detail and creative approach!",
    "Top-rated {title} now available in {city}, {state}. Expert professionals ready to bring your vision to life. Free estimates, fast delivery. Call or message today!",
]


def generate_description(title: str, city: str, state: str) -> str:
    clean_title = title.replace("_", " ").strip()
    clean_title = re.sub(r'\s+[a-f0-9]{6}$', '', clean_title)
    clean_title = re.sub(r'\.(jpg|jpeg|png|webp|gif)$', '', clean_title, flags=re.IGNORECASE)
    template = random.choice(DESC_TEMPLATES)
    return template.format(title=clean_title, city=city, state=state)


def extract_title_from_filename(filename: str) -> str:
    name = Path(filename).stem
    name = re.sub(r'_[a-f0-9]{6}$', '', name)
    name = name.replace('_', ' ')
    name = name.title()
    return name.strip()


# ── Profile & Cookie Management ───────────────────────────────────

def get_cookie_file(slot: int) -> Path:
    return COOKIES_DIR / f"slot_{slot}.json"


def get_profile_config_file(slot: int) -> Path:
    return COOKIES_DIR / f"profile_{slot}_config.json"


def get_schedule_file(slot: int) -> Path:
    return SCHEDULE_DIR / f"schedule_{slot}.json"


def load_slot(slot: int) -> dict | None:
    fpath = get_cookie_file(slot)
    if not fpath.exists():
        return None
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_slot(slot: int, data: dict):
    fpath = get_cookie_file(slot)
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def delete_slot(slot: int):
    fpath = get_cookie_file(slot)
    if fpath.exists():
        fpath.unlink()
    cfg = get_profile_config_file(slot)
    if cfg.exists():
        cfg.unlink()


def load_profile_config(slot: int) -> dict:
    fpath = get_profile_config_file(slot)
    defaults = {
        "proxy": "",
        "city_group": "",
        "enabled": False,
    }
    if not fpath.exists():
        return defaults
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            saved = json.load(f)
            defaults.update(saved)
            return defaults
    except (json.JSONDecodeError, OSError):
        return defaults


def save_profile_config(slot: int, config: dict):
    fpath = get_profile_config_file(slot)
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def load_schedule(slot: int) -> dict:
    fpath = get_schedule_file(slot)
    defaults = {
        "listings_today": 0,
        "last_listing_time": None,
        "last_reset_date": None,
        "history": [],
    }
    if not fpath.exists():
        return defaults
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            saved = json.load(f)
            defaults.update(saved)
            return defaults
    except (json.JSONDecodeError, OSError):
        return defaults


def save_schedule(slot: int, schedule: dict):
    fpath = get_schedule_file(slot)
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(schedule, f, indent=2, ensure_ascii=False)


def get_all_slots() -> list[dict]:
    slots = []
    for i in range(1, MAX_SLOTS + 1):
        data = load_slot(i)
        config = load_profile_config(i)
        schedule = load_schedule(i)
        if data:
            slots.append({
                "slot": i,
                "id": data.get("id", ""),
                "name": data.get("name", ""),
                "has_cookies": True,
                "proxy": config.get("proxy", ""),
                "city_group": config.get("city_group", ""),
                "enabled": config.get("enabled", False),
                "listings_today": schedule.get("listings_today", 0),
                "last_listing_time": schedule.get("last_listing_time"),
            })
        else:
            slots.append({
                "slot": i,
                "id": "",
                "name": "",
                "has_cookies": False,
                "proxy": config.get("proxy", ""),
                "city_group": config.get("city_group", ""),
                "enabled": False,
                "listings_today": 0,
                "last_listing_time": None,
            })
    return slots


def extract_fb_id_from_cookies(cookies: list[dict]) -> str:
    for c in cookies:
        if c.get("name") == "c_user" and c.get("value"):
            return c["value"]
    return ""


# ── App ───────────────────────────────────────────────────────────

app = FastAPI(title="FB Marketplace Auto Lister v7")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Global state
scheduler_state = {
    "running": False,
    "profiles": {},
    "logs": [],
    "start_time": None,
}
connected_clients: list[WebSocket] = []


class SaveCookieRequest(BaseModel):
    slot: int
    cookies_json: str
    name: str = ""


class ProfileConfigRequest(BaseModel):
    slot: int
    proxy: str = ""
    city_group: str = ""
    enabled: bool = False


class SchedulerStartRequest(BaseModel):
    image_folder: str
    service_type: str = "Professional Service"
    min_price: int = 250
    max_price: int = 350


async def broadcast(data: dict):
    dead = []
    for ws in connected_clients:
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        connected_clients.remove(ws)


async def log(msg: str, profile_id: str = ""):
    timestamp = time.strftime("%H:%M:%S")
    prefix = f"[Profile {profile_id}] " if profile_id else ""
    entry = f"[{timestamp}] {prefix}{msg}"
    scheduler_state["logs"].append(entry)
    if len(scheduler_state["logs"]) > 2000:
        scheduler_state["logs"] = scheduler_state["logs"][-2000:]
    await broadcast({
        "type": "log",
        "message": entry,
        "profile_id": profile_id,
        "state": {
            "running": scheduler_state["running"],
            "profiles": {
                k: {key: val for key, val in v.items() if key != "page" and key != "context" and key != "browser"}
                for k, v in scheduler_state["profiles"].items()
            },
        }
    })


def get_image_files(folder: str) -> list[Path]:
    folder_path = Path(folder)
    if not folder_path.exists():
        return []
    extensions = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
    images = []
    for f in sorted(folder_path.iterdir()):
        if f.is_file() and f.suffix.lower() in extensions:
            images.append(f)
    return images


# ── Stealth & Browser Setup ──────────────────────────────────────

STEALTH_JS = """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
    window.chrome = {runtime: {}};
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications' ?
        Promise.resolve({ state: Notification.permission }) :
        originalQuery(parameters)
    );
"""


async def handle_cookie_consent(page):
    try:
        for sel in [
            'button:has-text("Allow")',
            'button:has-text("Accept")',
            'button[data-cookiebanner="accept_button"]',
            'button:has-text("Only allow essential cookies")',
            'button:has-text("Decline optional cookies")',
        ]:
            btn = page.locator(sel).first
            if await btn.count() > 0:
                await btn.click()
                await asyncio.sleep(1)
                break
    except Exception:
        pass


async def check_logged_in(page) -> bool:
    try:
        for sel in [
            'div[role="navigation"]',
            'a[aria-label="Home"]',
            'svg[aria-label="Your profile"]',
            'div[aria-label="Facebook"]',
            'input[type="search"]',
        ]:
            el = page.locator(sel).first
            if await el.count() > 0:
                return True
        if "login" not in page.url.lower() and "facebook.com" in page.url.lower():
            login_form = page.locator('input#email, input#pass, form#login_form').first
            if await login_form.count() == 0:
                return True
    except Exception:
        pass
    return False


async def login_with_cookies(context, page, cookies_json: str, profile_id: str) -> bool:
    try:
        cookies = json.loads(cookies_json)
        if not isinstance(cookies, list):
            await log("ERROR: Cookies format galat hai - list honi chahiye", profile_id)
            return False

        clean_cookies = []
        for cookie in cookies:
            c = {}
            c["name"] = cookie.get("name", "")
            c["value"] = cookie.get("value", "")
            c["domain"] = cookie.get("domain", ".facebook.com")
            c["path"] = cookie.get("path", "/")

            if not c["name"] or not c["value"]:
                continue

            if "facebook.com" in c["domain"] and not c["domain"].startswith("."):
                c["domain"] = "." + c["domain"]

            if cookie.get("secure"):
                c["secure"] = True
            if cookie.get("httpOnly"):
                c["httpOnly"] = True
            if cookie.get("sameSite"):
                s = str(cookie["sameSite"]).capitalize()
                if s in ["Strict", "Lax", "None"]:
                    c["sameSite"] = s

            clean_cookies.append(c)

        if not clean_cookies:
            await log("ERROR: Koi valid cookie nahi mili", profile_id)
            return False

        await log(f"Adding {len(clean_cookies)} cookies...", profile_id)
        await context.add_cookies(clean_cookies)
        await asyncio.sleep(1)

        await page.goto("https://www.facebook.com", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
        await handle_cookie_consent(page)

        if await check_logged_in(page):
            await log("Cookie login successful!", profile_id)
            return True
        else:
            await log("ERROR: Cookie login failed - cookies expired ho gayi hain", profile_id)
            return False

    except json.JSONDecodeError:
        await log("ERROR: Cookies JSON parse nahi ho raha - format check karo", profile_id)
        return False
    except Exception as e:
        await log(f"ERROR: Cookie login error: {str(e)[:60]}", profile_id)
        return False


# ── Human Behavior Simulator ─────────────────────────────────────

class HumanBehavior:
    """Simulates human-like Facebook activity to appear natural."""

    def __init__(self, page, profile_id: str):
        self.page = page
        self.profile_id = profile_id

    async def random_delay(self, min_sec: float = 1.0, max_sec: float = 4.0):
        delay = random.uniform(min_sec, max_sec)
        await asyncio.sleep(delay)

    async def human_scroll(self, scroll_count: int = None):
        if scroll_count is None:
            scroll_count = random.randint(3, 12)
        await log(f"  Scrolling feed ({scroll_count} scrolls)...", self.profile_id)
        for i in range(scroll_count):
            scroll_amount = random.randint(200, 600)
            await self.page.evaluate(f"window.scrollBy(0, {scroll_amount})")
            await asyncio.sleep(random.uniform(1.5, 4.0))

            if random.random() < 0.3:
                await asyncio.sleep(random.uniform(2.0, 6.0))

            if random.random() < 0.15:
                scroll_up = random.randint(50, 200)
                await self.page.evaluate(f"window.scrollBy(0, -{scroll_up})")
                await asyncio.sleep(random.uniform(0.5, 1.5))

    async def random_like_posts(self, max_likes: int = None):
        if max_likes is None:
            max_likes = random.randint(1, 4)
        await log(f"  Looking for posts to like (max {max_likes})...", self.profile_id)
        likes_done = 0
        try:
            like_buttons = self.page.locator(
                'div[aria-label="Like"], '
                'div[aria-label="like"], '
                'span[aria-label="Like"]'
            )
            count = await like_buttons.count()
            if count == 0:
                return

            indices = list(range(count))
            random.shuffle(indices)

            for idx in indices[:max_likes]:
                try:
                    btn = like_buttons.nth(idx)
                    if await btn.is_visible():
                        await btn.scroll_into_view_if_needed()
                        await asyncio.sleep(random.uniform(0.5, 1.5))
                        await btn.click()
                        likes_done += 1
                        await asyncio.sleep(random.uniform(1.0, 3.0))
                except Exception:
                    continue

            if likes_done > 0:
                await log(f"  Liked {likes_done} posts", self.profile_id)
        except Exception:
            pass

    async def browse_news_feed(self, duration_minutes: float = None):
        if duration_minutes is None:
            duration_minutes = random.uniform(2, 8)
        await log(f"  Browsing news feed for ~{duration_minutes:.1f} min...", self.profile_id)

        try:
            await self.page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(2, 4))
            await handle_cookie_consent(self.page)

            end_time = time.time() + (duration_minutes * 60)
            while time.time() < end_time and scheduler_state["running"]:
                action = random.choice(["scroll", "scroll", "scroll", "pause", "like"])
                if action == "scroll":
                    scroll_amount = random.randint(200, 500)
                    await self.page.evaluate(f"window.scrollBy(0, {scroll_amount})")
                    await asyncio.sleep(random.uniform(2.0, 5.0))
                elif action == "pause":
                    await asyncio.sleep(random.uniform(3.0, 10.0))
                elif action == "like":
                    await self.random_like_posts(max_likes=1)

        except Exception as e:
            await log(f"  News feed browse error: {str(e)[:50]}", self.profile_id)

    async def browse_marketplace(self, duration_minutes: float = None):
        if duration_minutes is None:
            duration_minutes = random.uniform(2, 5)
        await log(f"  Browsing marketplace for ~{duration_minutes:.1f} min...", self.profile_id)

        try:
            await self.page.goto("https://www.facebook.com/marketplace/", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(2, 4))

            end_time = time.time() + (duration_minutes * 60)
            while time.time() < end_time and scheduler_state["running"]:
                action = random.choice(["scroll", "scroll", "click_item", "search", "pause"])
                if action == "scroll":
                    scroll_amount = random.randint(200, 500)
                    await self.page.evaluate(f"window.scrollBy(0, {scroll_amount})")
                    await asyncio.sleep(random.uniform(2.0, 5.0))
                elif action == "click_item":
                    await self._click_random_marketplace_item()
                elif action == "search":
                    await self._search_marketplace()
                elif action == "pause":
                    await asyncio.sleep(random.uniform(3.0, 8.0))

        except Exception as e:
            await log(f"  Marketplace browse error: {str(e)[:50]}", self.profile_id)

    async def _click_random_marketplace_item(self):
        try:
            items = self.page.locator('a[href*="/marketplace/item/"]')
            count = await items.count()
            if count > 0:
                idx = random.randint(0, min(count - 1, 10))
                item = items.nth(idx)
                if await item.is_visible():
                    await item.click()
                    await asyncio.sleep(random.uniform(3, 8))
                    await self.human_scroll(scroll_count=random.randint(1, 3))
                    await self.page.go_back()
                    await asyncio.sleep(random.uniform(1, 3))
        except Exception:
            pass

    async def _search_marketplace(self):
        try:
            search_term = random.choice(SEARCH_TERMS)
            search_input = self.page.locator('input[type="search"], input[aria-label*="Search"]').first
            if await search_input.count() > 0 and await search_input.is_visible():
                await search_input.click()
                await asyncio.sleep(random.uniform(0.5, 1.5))
                await self.page.keyboard.press("Control+a")
                await self.page.keyboard.press("Backspace")
                await self.page.keyboard.type(search_term, delay=random.randint(30, 80))
                await asyncio.sleep(random.uniform(0.5, 1.0))
                await self.page.keyboard.press("Enter")
                await asyncio.sleep(random.uniform(3, 6))
                await self.human_scroll(scroll_count=random.randint(2, 5))
        except Exception:
            pass

    async def browse_random_page(self):
        url = random.choice(BROWSE_URLS)
        await log(f"  Visiting: {url.split('/')[-2] or 'feed'}...", self.profile_id)
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(2, 5))
            await self.human_scroll(scroll_count=random.randint(2, 6))
        except Exception as e:
            await log(f"  Browse error: {str(e)[:40]}", self.profile_id)

    async def build_browser_history(self):
        num_sites = random.randint(3, 6)
        urls = random.sample(HISTORY_URLS, min(num_sites, len(HISTORY_URLS)))
        await log(f"  Building browser history ({num_sites} sites)...", self.profile_id)

        for url in urls:
            try:
                await self.page.goto(url, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(random.uniform(2, 5))
                scroll_count = random.randint(1, 3)
                for _ in range(scroll_count):
                    await self.page.evaluate(f"window.scrollBy(0, {random.randint(100, 400)})")
                    await asyncio.sleep(random.uniform(1, 3))
            except Exception:
                continue

    async def do_heavy_browsing(self):
        await log("Starting heavy browsing session...", self.profile_id)
        activities = [
            self.browse_news_feed,
            self.browse_marketplace,
            self.browse_random_page,
            self.browse_random_page,
        ]
        random.shuffle(activities)

        num_activities = random.randint(2, 4)
        for i in range(num_activities):
            if not scheduler_state["running"]:
                break
            activity = activities[i % len(activities)]
            await activity()
            await asyncio.sleep(random.uniform(2, 5))

        if random.random() < 0.4:
            await self.random_like_posts()

        await log("Heavy browsing session complete", self.profile_id)


# ── Marketplace Posting ──────────────────────────────────────────

async def scan_page_elements(page, profile_id: str):
    try:
        info = await page.evaluate("""() => {
            const results = [];
            document.querySelectorAll('input, textarea, [contenteditable="true"], [role="textbox"], [role="combobox"]').forEach(el => {
                if (el.offsetParent !== null) {
                    results.push({
                        tag: el.tagName,
                        type: el.type || '',
                        ariaLabel: el.getAttribute('aria-label') || '',
                        placeholder: el.placeholder || '',
                        role: el.getAttribute('role') || '',
                        name: el.name || '',
                        id: el.id || '',
                        contentEditable: el.contentEditable,
                    });
                }
            });
            document.querySelectorAll('label').forEach(el => {
                if (el.offsetParent !== null) {
                    const span = el.querySelector('span');
                    if (span) {
                        results.push({
                            tag: 'LABEL',
                            text: span.textContent.trim().substring(0, 50),
                            hasInput: !!el.querySelector('input, textarea'),
                        });
                    }
                }
            });
            return results;
        }""")
        await log(f"  === PAGE ELEMENTS SCAN ({len(info)} found) ===", profile_id)
        for item in info[:25]:
            await log(f"    {json.dumps(item)}", profile_id)
        return info
    except Exception as e:
        await log(f"  Scan error: {str(e)[:50]}", profile_id)
        return []


async def react_fill_field(page, field_info: dict, value: str, profile_id: str) -> bool:
    try:
        selector = None
        if field_info.get("ariaLabel"):
            tag = field_info.get("tag", "input").lower()
            selector = f'{tag}[aria-label="{field_info["ariaLabel"]}"]'
        elif field_info.get("id"):
            selector = f'#{field_info["id"]}'
        elif field_info.get("name"):
            tag = field_info.get("tag", "input").lower()
            selector = f'{tag}[name="{field_info["name"]}"]'

        if selector:
            el = page.locator(selector).first
            if await el.count() > 0 and await el.is_visible():
                await el.click()
                await asyncio.sleep(0.3)
                await page.keyboard.press("Control+a")
                await asyncio.sleep(0.1)
                await page.keyboard.press("Backspace")
                await asyncio.sleep(0.2)
                await page.keyboard.type(value, delay=random.randint(15, 35))
                await asyncio.sleep(0.3)
                return True
        return False
    except Exception:
        return False


async def find_and_click_field(page, label_keywords: list, profile_id: str) -> bool:
    for keyword in label_keywords:
        try:
            for tag in ['input', 'textarea']:
                el = page.locator(f'{tag}[aria-label*="{keyword}" i]').first
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    await asyncio.sleep(0.3)
                    return True

            for role in ['textbox', 'combobox']:
                el = page.locator(f'[role="{role}"][aria-label*="{keyword}" i]').first
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    await asyncio.sleep(0.3)
                    return True

            label = page.locator(f'label:has(span:has-text("{keyword}"))').first
            if await label.count() > 0 and await label.is_visible():
                await label.click()
                await asyncio.sleep(0.3)
                return True

            spans = page.locator(f'span:has-text("{keyword}")')
            count = await spans.count()
            for i in range(min(count, 3)):
                span = spans.nth(i)
                if await span.is_visible():
                    parent = span.locator("xpath=ancestor::div[contains(@class,'x')]")
                    inp = parent.locator('input, textarea, [contenteditable="true"]').first
                    if await inp.count() > 0:
                        await inp.click()
                        await asyncio.sleep(0.3)
                        return True
                    await span.click()
                    await asyncio.sleep(0.3)
                    return True

        except Exception:
            continue
    return False


async def type_in_focused(page, value: str, clear_first: bool = True):
    if clear_first:
        await page.keyboard.press("Control+a")
        await asyncio.sleep(0.1)
        await page.keyboard.press("Backspace")
        await asyncio.sleep(0.2)
    await page.keyboard.type(value, delay=random.randint(15, 35))
    await asyncio.sleep(0.3)


async def post_to_marketplace(page, listing: dict, profile_id: str) -> bool:
    try:
        await log(f"Posting: {listing['title'][:50]}...", profile_id)

        await page.goto("https://www.facebook.com/marketplace/create/item", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(4, 7))

        try:
            for sel in ['div[aria-label="Close"]', 'div[aria-label="Dismiss"]']:
                btn = page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click()
                    await asyncio.sleep(1)
        except Exception:
            pass

        elements = await scan_page_elements(page, profile_id)

        # Step 1: Upload image
        try:
            file_input = page.locator('input[type="file"]').first
            if await file_input.count() > 0:
                await file_input.set_input_files(listing["image_path"])
                await asyncio.sleep(random.uniform(3, 6))
                await log(f"  Image uploaded", profile_id)
        except Exception as e:
            await log(f"  Image upload error: {str(e)[:50]}", profile_id)

        field_map = {}
        for el in elements:
            aria = el.get("ariaLabel", "").lower()
            if "title" in aria:
                field_map["title"] = el
            elif "price" in aria:
                field_map["price"] = el
            elif "description" in aria or "descri" in aria:
                field_map["description"] = el
            elif "location" in aria or "locati" in aria:
                field_map["location"] = el
            elif "condition" in aria:
                field_map["condition"] = el
            elif "category" in aria:
                field_map["category"] = el

        await log(f"  Fields detected: {list(field_map.keys())}", profile_id)

        # Step 2: Fill Title
        filled = False
        if "title" in field_map:
            filled = await react_fill_field(page, field_map["title"], listing["title"], profile_id)
        if not filled:
            filled = await find_and_click_field(page, ["Title", "title", "Item name"], profile_id)
            if filled:
                await type_in_focused(page, listing["title"])
        if not filled:
            await page.evaluate("""() => {
                const inputs = document.querySelectorAll('input[type="text"]');
                for (const inp of inputs) {
                    if (inp.offsetParent && !inp.value) {
                        inp.focus();
                        inp.click();
                        return true;
                    }
                }
                return false;
            }""")
            await asyncio.sleep(0.3)
            await page.keyboard.type(listing["title"], delay=random.randint(15, 35))
            filled = True
        await log(f"  Title: {'OK' if filled else 'FAILED'}", profile_id)
        await asyncio.sleep(random.uniform(0.5, 1.5))

        # Step 3: Fill Price
        filled = False
        if "price" in field_map:
            filled = await react_fill_field(page, field_map["price"], str(listing["price"]), profile_id)
        if not filled:
            filled = await find_and_click_field(page, ["Price", "price"], profile_id)
            if filled:
                await type_in_focused(page, str(listing["price"]))
        if not filled:
            await page.keyboard.press("Tab")
            await asyncio.sleep(0.3)
            await page.keyboard.type(str(listing["price"]), delay=random.randint(15, 35))
        await log(f"  Price: ${listing['price']}", profile_id)
        await asyncio.sleep(random.uniform(0.5, 1.5))

        # Step 4: Category
        cat_done = False
        try:
            cat_label = page.locator('label:has(span:text-is("Category"))').first
            if await cat_label.count() > 0 and await cat_label.is_visible():
                await cat_label.click()
                await asyncio.sleep(1.5)
                cat_done = True

            if not cat_done:
                comboboxes = page.locator('label[role="combobox"]')
                count = await comboboxes.count()
                if count > 0:
                    await comboboxes.first.click()
                    await asyncio.sleep(1.5)
                    cat_done = True

            if cat_done:
                option_found = False
                for sel in [
                    f'div[role="option"] span:has-text("{listing["category"]}")',
                    f'div[role="option"]:has-text("{listing["category"]}")',
                    f'div[role="listbox"] div:has-text("{listing["category"]}")',
                    f'span:text-is("{listing["category"]}")',
                ]:
                    try:
                        opt = page.locator(sel).first
                        if await opt.count() > 0 and await opt.is_visible():
                            await opt.click()
                            option_found = True
                            await asyncio.sleep(0.5)
                            break
                    except Exception:
                        continue

                if not option_found:
                    await page.keyboard.type(listing["category"][:20], delay=40)
                    await asyncio.sleep(1.5)
                    opt = page.locator('div[role="option"]').first
                    if await opt.count() > 0 and await opt.is_visible():
                        await opt.click()
                        option_found = True
                    else:
                        await page.keyboard.press("Escape")
                        cat_done = False

                if not option_found:
                    cat_done = False
        except Exception as e:
            await log(f"  Category error: {str(e)[:40]}", profile_id)
        await log(f"  Category: {'OK' if cat_done else 'SKIP'}", profile_id)
        await asyncio.sleep(random.uniform(0.5, 1.0))

        # Step 5: Condition
        cond_done = False
        try:
            cond_label = page.locator('label:has(span:text-is("Condition"))').first
            if await cond_label.count() > 0 and await cond_label.is_visible():
                await cond_label.click()
                await asyncio.sleep(1.5)
                cond_done = True

            if not cond_done:
                comboboxes = page.locator('label[role="combobox"]')
                count = await comboboxes.count()
                if count > 1:
                    await comboboxes.nth(1).click()
                    await asyncio.sleep(1.5)
                    cond_done = True

            if cond_done:
                option_found = False
                for sel in [
                    f'div[role="option"] span:has-text("{listing["condition"]}")',
                    f'div[role="option"]:has-text("{listing["condition"]}")',
                    f'span:text-is("{listing["condition"]}")',
                ]:
                    try:
                        opt = page.locator(sel).first
                        if await opt.count() > 0 and await opt.is_visible():
                            await opt.click()
                            option_found = True
                            await asyncio.sleep(0.5)
                            break
                    except Exception:
                        continue

                if not option_found:
                    await page.keyboard.press("Escape")
                    cond_done = False
        except Exception as e:
            await log(f"  Condition error: {str(e)[:40]}", profile_id)
        await log(f"  Condition: {'OK' if cond_done else 'SKIP'}", profile_id)
        await asyncio.sleep(random.uniform(0.5, 1.0))

        # SCROLL DOWN
        await page.evaluate("window.scrollBy(0, 500)")
        await asyncio.sleep(random.uniform(1, 2))

        # Step 6: Description
        filled = False
        try:
            desc_label = page.locator('label:has(span:text-is("Description"))').first
            if await desc_label.count() > 0 and await desc_label.is_visible():
                await desc_label.click()
                await asyncio.sleep(0.3)
                for sel in ['textarea', 'input', '[contenteditable="true"]']:
                    el = desc_label.locator(sel).first
                    if await el.count() > 0:
                        await el.click()
                        await asyncio.sleep(0.2)
                        await page.keyboard.type(listing["description"][:500], delay=random.randint(8, 18))
                        filled = True
                        break
                if not filled:
                    await page.keyboard.type(listing["description"][:500], delay=random.randint(8, 18))
                    filled = True
        except Exception:
            pass

        if not filled:
            try:
                textareas = page.locator('textarea')
                count = await textareas.count()
                for i in range(count):
                    ta = textareas.nth(i)
                    if await ta.is_visible():
                        await ta.click()
                        await asyncio.sleep(0.3)
                        await page.keyboard.type(listing["description"][:500], delay=random.randint(8, 18))
                        filled = True
                        break
            except Exception:
                pass

        if not filled:
            for sel in ['textarea[aria-label*="escription"]', 'input[aria-label*="escription"]',
                        '[role="textbox"][aria-label*="escription"]']:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0 and await el.is_visible():
                        await el.click()
                        await asyncio.sleep(0.3)
                        await page.keyboard.type(listing["description"][:500], delay=random.randint(8, 18))
                        filled = True
                        break
                except Exception:
                    continue

        await log(f"  Description: {'OK' if filled else 'SKIP'}", profile_id)
        await asyncio.sleep(random.uniform(0.5, 1.0))

        # Step 7: Location
        location_text = f"{listing['city']}, {listing['state']}"
        loc_done = False

        for sel in [
            'input[aria-label*="ocation"]',
            'input[aria-label*="Location"]',
            'label:has(span:text-is("Location")) input',
            'label:has(span:has-text("Location")) input',
            'input[placeholder*="ocation"]',
        ]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    await asyncio.sleep(0.3)
                    await page.keyboard.press("Control+a")
                    await page.keyboard.press("Backspace")
                    await asyncio.sleep(0.3)
                    await page.keyboard.type(location_text, delay=random.randint(30, 60))
                    await asyncio.sleep(random.uniform(2, 4))
                    for s_sel in ['ul[role="listbox"] li', 'div[role="option"]', 'div[role="listbox"] div']:
                        sug = page.locator(s_sel).first
                        if await sug.count() > 0 and await sug.is_visible():
                            await sug.click()
                            loc_done = True
                            break
                    if not loc_done:
                        await page.keyboard.press("Enter")
                        loc_done = True
                    break
            except Exception:
                continue

        if not loc_done:
            try:
                loc_label = page.locator('label:has(span:has-text("Location"))').first
                if await loc_label.count() > 0 and await loc_label.is_visible():
                    await loc_label.click()
                    await asyncio.sleep(0.5)
                    await page.keyboard.type(location_text, delay=random.randint(30, 60))
                    await asyncio.sleep(random.uniform(2, 4))
                    sug = page.locator('div[role="option"]').first
                    if await sug.count() > 0:
                        await sug.click()
                        loc_done = True
            except Exception:
                pass

        await log(f"  Location: {'OK' if loc_done else 'SKIP'} ({location_text})", profile_id)
        await asyncio.sleep(1)

        # Step 8: Next / Publish
        published = False
        for btn_text in ["Next", "Publish", "Post"]:
            try:
                for sel in [
                    f'div[aria-label="{btn_text}"]',
                    f'div[role="button"]:has(span:text-is("{btn_text}"))',
                    f'span:text-is("{btn_text}")',
                ]:
                    btn = page.locator(sel).first
                    if await btn.count() > 0 and await btn.is_visible():
                        await btn.click()
                        await asyncio.sleep(random.uniform(2, 4))
                        published = True
                        break
                if published:
                    break
            except Exception:
                continue

        if published:
            await asyncio.sleep(2)
            for sel in [
                'div[aria-label="Publish"]',
                'div[role="button"]:has(span:text-is("Publish"))',
                'span:text-is("Publish")',
            ]:
                try:
                    btn = page.locator(sel).first
                    if await btn.count() > 0 and await btn.is_visible():
                        await btn.click()
                        await asyncio.sleep(random.uniform(2, 4))
                        break
                except Exception:
                    continue

        await log(f"  POSTED: {listing['title'][:40]} | {listing['city']}, {listing['state']} | ${listing['price']}", profile_id)
        return True

    except Exception as e:
        await log(f"  Error: {str(e)[:80]}", profile_id)
        return False


# ── Scheduler Engine ─────────────────────────────────────────────

async def run_profile_scheduler(pw, slot: int, config: dict, images: list[Path], scheduler_config: dict):
    """Run the scheduler for a single profile - posts 1 listing per hour, max 3 per day."""
    profile_id = str(slot)
    slot_data = load_slot(slot)
    profile_config = load_profile_config(slot)
    schedule = load_schedule(slot)

    today = datetime.now().strftime("%Y-%m-%d")
    if schedule.get("last_reset_date") != today:
        schedule["listings_today"] = 0
        schedule["last_reset_date"] = today
        save_schedule(slot, schedule)

    scheduler_state["profiles"][profile_id] = {
        "status": "starting",
        "slot": slot,
        "name": slot_data.get("name", f"Profile {slot}"),
        "city_group": profile_config.get("city_group", ""),
        "listings_today": schedule["listings_today"],
        "max_daily": MAX_LISTINGS_PER_DAY,
        "current_activity": "Initializing...",
        "next_listing_time": None,
    }

    try:
        proxy_str = profile_config.get("proxy", "")
        proxy_config = None
        if proxy_str:
            proxy_config = {"server": proxy_str}
            await log(f"Using proxy: {proxy_str[:30]}...", profile_id)

        user_data_dir = str(PROFILES_DIR / f"profile_{slot}")
        os.makedirs(user_data_dir, exist_ok=True)

        await log(f"Launching browser...", profile_id)
        launch_args = [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--disable-dev-shm-usage",
        ]

        context = await pw.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            proxy=proxy_config,
            args=launch_args,
            ignore_default_args=["--enable-automation"],
        )

        page = context.pages[0] if context.pages else await context.new_page()
        await page.add_init_script(STEALTH_JS)

        human = HumanBehavior(page, profile_id)

        # Login check
        scheduler_state["profiles"][profile_id]["current_activity"] = "Checking login..."
        await log("Checking login status...", profile_id)
        await page.goto("https://www.facebook.com", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
        await handle_cookie_consent(page)

        already_logged_in = await check_logged_in(page)

        if already_logged_in:
            await log("Already logged in (saved session)!", profile_id)
        else:
            cookies_json = json.dumps(slot_data.get("cookies", []))
            if cookies_json.strip() and cookies_json != "[]":
                success = await login_with_cookies(context, page, cookies_json, profile_id)
            else:
                await log("ERROR: No cookies found!", profile_id)
                scheduler_state["profiles"][profile_id]["status"] = "failed"
                await context.close()
                return

            if not success:
                scheduler_state["profiles"][profile_id]["status"] = "login_failed"
                await context.close()
                return

        scheduler_state["profiles"][profile_id]["status"] = "running"

        # Build browser history on first run
        scheduler_state["profiles"][profile_id]["current_activity"] = "Building browser history..."
        await human.build_browser_history()

        # Get city group for this profile
        city_group_key = profile_config.get("city_group", "")
        region_cities = []
        if city_group_key and city_group_key in CITY_GROUPS:
            region_cities = CITY_GROUPS[city_group_key]["cities"]
            await log(f"City region: {CITY_GROUPS[city_group_key]['label']} ({len(region_cities)} cities)", profile_id)
        else:
            region_cities = ALL_CITIES[:20]
            await log(f"No city group assigned, using default cities", profile_id)

        # Main scheduler loop
        listing_index = 0
        while scheduler_state["running"]:
            today = datetime.now().strftime("%Y-%m-%d")
            if schedule.get("last_reset_date") != today:
                schedule["listings_today"] = 0
                schedule["last_reset_date"] = today
                await log(f"New day! Resetting daily counter.", profile_id)

            if schedule["listings_today"] >= MAX_LISTINGS_PER_DAY:
                scheduler_state["profiles"][profile_id]["current_activity"] = f"Daily limit reached ({MAX_LISTINGS_PER_DAY}/{MAX_LISTINGS_PER_DAY}). Browsing..."
                await log(f"Daily limit reached ({MAX_LISTINGS_PER_DAY} listings). Browsing only...", profile_id)
                await human.do_heavy_browsing()
                await asyncio.sleep(random.uniform(300, 600))
                continue

            # Check if enough time has passed since last listing
            if schedule.get("last_listing_time"):
                last_time = datetime.fromisoformat(schedule["last_listing_time"])
                elapsed = (datetime.now() - last_time).total_seconds()
                remaining = (LISTING_INTERVAL_MINUTES * 60) - elapsed
                if remaining > 0:
                    next_time = datetime.now() + timedelta(seconds=remaining)
                    scheduler_state["profiles"][profile_id]["current_activity"] = f"Waiting until {next_time.strftime('%H:%M')} for next listing. Browsing..."
                    scheduler_state["profiles"][profile_id]["next_listing_time"] = next_time.strftime("%H:%M:%S")
                    await log(f"Next listing at {next_time.strftime('%H:%M')}. Browsing in meantime...", profile_id)

                    # Heavy browsing while waiting
                    while remaining > 0 and scheduler_state["running"]:
                        browse_duration = min(remaining, random.uniform(180, 420))
                        scheduler_state["profiles"][profile_id]["current_activity"] = f"Browsing... (next listing in {int(remaining/60)} min)"

                        await human.do_heavy_browsing()

                        remaining = remaining - browse_duration
                        if remaining > 60:
                            pause = random.uniform(30, 120)
                            scheduler_state["profiles"][profile_id]["current_activity"] = f"Idle pause... (next listing in {int(remaining/60)} min)"
                            await asyncio.sleep(pause)
                            remaining -= pause

                    if not scheduler_state["running"]:
                        break

            # Pre-listing browsing
            scheduler_state["profiles"][profile_id]["current_activity"] = "Pre-listing browsing..."
            await log("Pre-listing browsing...", profile_id)
            await human.browse_news_feed(duration_minutes=random.uniform(1, 3))
            await human.browse_marketplace(duration_minutes=random.uniform(1, 2))

            # Select image and city for listing
            if listing_index >= len(images):
                listing_index = 0
                random.shuffle(images)

            img_path = images[listing_index]
            listing_index += 1

            city_data = random.choice(region_cities)
            title = extract_title_from_filename(img_path.name)
            if not title or len(title) < 3:
                title = f"{scheduler_config['service_type']} #{listing_index}"

            listing = {
                "title": title,
                "description": generate_description(title, city_data["city"], city_data["state"]),
                "price": random.randint(scheduler_config["min_price"], scheduler_config["max_price"]),
                "category": random.choice(ALL_CATEGORIES),
                "condition": random.choice(CONDITIONS),
                "city": city_data["city"],
                "state": city_data["state"],
                "zip": city_data.get("zip", ""),
                "image_path": str(img_path),
                "image_name": img_path.name,
            }

            # Post listing
            scheduler_state["profiles"][profile_id]["current_activity"] = f"Posting: {title[:30]}..."
            success = await post_to_marketplace(page, listing, profile_id)

            if success:
                schedule["listings_today"] += 1
                schedule["last_listing_time"] = datetime.now().isoformat()
                schedule["history"].append({
                    "title": listing["title"],
                    "city": listing["city"],
                    "state": listing["state"],
                    "price": listing["price"],
                    "time": datetime.now().isoformat(),
                    "success": True,
                })
                if len(schedule["history"]) > 100:
                    schedule["history"] = schedule["history"][-100:]
                save_schedule(slot, schedule)
                scheduler_state["profiles"][profile_id]["listings_today"] = schedule["listings_today"]

                await log(f"Listing {schedule['listings_today']}/{MAX_LISTINGS_PER_DAY} done for today", profile_id)

            # Post-listing browsing
            scheduler_state["profiles"][profile_id]["current_activity"] = "Post-listing browsing..."
            await log("Post-listing browsing...", profile_id)
            await human.browse_news_feed(duration_minutes=random.uniform(1, 3))

            if schedule["listings_today"] >= MAX_LISTINGS_PER_DAY:
                await log(f"Daily limit reached! Will continue browsing only.", profile_id)

        await context.close()
        scheduler_state["profiles"][profile_id]["status"] = "stopped"
        await log(f"Profile stopped. Total listings today: {schedule['listings_today']}", profile_id)

    except Exception as e:
        await log(f"ERROR: {str(e)[:80]}", profile_id)
        scheduler_state["profiles"][profile_id]["status"] = "error"
        scheduler_state["profiles"][profile_id]["current_activity"] = f"Error: {str(e)[:50]}"


async def run_scheduler_engine(config: SchedulerStartRequest):
    from playwright.async_api import async_playwright

    scheduler_state["running"] = True
    scheduler_state["profiles"] = {}
    scheduler_state["logs"] = []
    scheduler_state["start_time"] = datetime.now().isoformat()

    try:
        images = get_image_files(config.image_folder)
        if not images:
            images = get_image_files(os.path.join(config.image_folder, "images"))

        if not images:
            await log(f"ERROR: No images found in {config.image_folder}")
            scheduler_state["running"] = False
            return

        random.shuffle(images)
        await log(f"Found {len(images)} images")

        enabled_profiles = []
        for slot in range(1, MAX_SLOTS + 1):
            slot_data = load_slot(slot)
            profile_config = load_profile_config(slot)
            if slot_data and profile_config.get("enabled", False):
                enabled_profiles.append(slot)

        if not enabled_profiles:
            await log("ERROR: No enabled profiles found! Enable at least one profile.")
            scheduler_state["running"] = False
            return

        await log(f"Active profiles: {len(enabled_profiles)} (slots: {enabled_profiles})")
        await log(f"Schedule: 1 listing per {LISTING_INTERVAL_MINUTES} min, max {MAX_LISTINGS_PER_DAY} per day")
        await log("")

        scheduler_config = {
            "service_type": config.service_type,
            "min_price": config.min_price,
            "max_price": config.max_price,
        }

        async with async_playwright() as pw:
            tasks = []
            for slot in enabled_profiles:
                profile_images = images.copy()
                random.shuffle(profile_images)
                task = asyncio.create_task(
                    run_profile_scheduler(pw, slot, {}, profile_images, scheduler_config)
                )
                tasks.append(task)

            await asyncio.gather(*tasks, return_exceptions=True)

        await log("")
        await log("=== SCHEDULER STOPPED ===")

    except Exception as e:
        await log(f"ERROR: {str(e)}")
    finally:
        scheduler_state["running"] = False
        await broadcast({"type": "done", "state": {
            "running": False,
            "profiles": {
                k: {key: val for key, val in v.items() if key not in ("page", "context", "browser")}
                for k, v in scheduler_state["profiles"].items()
            },
        }})


# ── API Routes ────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/stats")
async def get_stats():
    return {
        "cities": len(ALL_CITIES),
        "categories": len(ALL_CATEGORIES),
        "conditions": len(CONDITIONS),
        "city_groups": {k: v["label"] for k, v in CITY_GROUPS.items()},
        "max_slots": MAX_SLOTS,
        "max_daily": MAX_LISTINGS_PER_DAY,
        "interval_minutes": LISTING_INTERVAL_MINUTES,
    }


# ── Cookie Slot APIs ──────────────────────────────────────────────

@app.get("/api/slots")
async def get_slots():
    return {"slots": get_all_slots(), "max_slots": MAX_SLOTS}


@app.get("/api/slots/{slot}")
async def get_slot(slot: int):
    if slot < 1 or slot > MAX_SLOTS:
        return {"error": f"Slot 1-{MAX_SLOTS} ke beech hona chahiye"}
    data = load_slot(slot)
    if data:
        return {"slot": slot, "data": data}
    return {"slot": slot, "data": None}


@app.post("/api/slots/{slot}")
async def save_slot_api(slot: int, req: SaveCookieRequest):
    if slot < 1 or slot > MAX_SLOTS:
        return {"error": f"Slot 1-{MAX_SLOTS} ke beech hona chahiye"}

    try:
        cookies = json.loads(req.cookies_json)
        if not isinstance(cookies, list):
            return {"error": "Cookies JSON ek list (array) honi chahiye"}

        fb_id = extract_fb_id_from_cookies(cookies)
        name = req.name or (f"FB {fb_id}" if fb_id else f"Account {slot}")

        slot_data = {
            "id": fb_id,
            "name": name,
            "cookies": cookies,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        save_slot(slot, slot_data)

        return {
            "status": "saved",
            "slot": slot,
            "id": fb_id,
            "name": name,
            "cookie_count": len(cookies),
        }
    except json.JSONDecodeError:
        return {"error": "Invalid JSON format - cookies array paste karo"}


@app.delete("/api/slots/{slot}")
async def delete_slot_api(slot: int):
    if slot < 1 or slot > MAX_SLOTS:
        return {"error": f"Slot 1-{MAX_SLOTS} ke beech hona chahiye"}
    delete_slot(slot)
    return {"status": "deleted", "slot": slot}


# ── Profile Config APIs ──────────────────────────────────────────

@app.get("/api/profile/{slot}")
async def get_profile(slot: int):
    if slot < 1 or slot > MAX_SLOTS:
        return {"error": f"Slot 1-{MAX_SLOTS} ke beech hona chahiye"}
    config = load_profile_config(slot)
    return {"slot": slot, "config": config}


@app.post("/api/profile/{slot}")
async def save_profile(slot: int, req: ProfileConfigRequest):
    if slot < 1 or slot > MAX_SLOTS:
        return {"error": f"Slot 1-{MAX_SLOTS} ke beech hona chahiye"}

    config = {
        "proxy": req.proxy,
        "city_group": req.city_group,
        "enabled": req.enabled,
    }
    save_profile_config(slot, config)
    return {"status": "saved", "slot": slot, "config": config}


# ── Schedule APIs ─────────────────────────────────────────────────

@app.get("/api/schedule/{slot}")
async def get_schedule_api(slot: int):
    if slot < 1 or slot > MAX_SLOTS:
        return {"error": f"Slot 1-{MAX_SLOTS} ke beech hona chahiye"}
    schedule = load_schedule(slot)
    return {"slot": slot, "schedule": schedule}


@app.post("/api/schedule/{slot}/reset")
async def reset_schedule(slot: int):
    if slot < 1 or slot > MAX_SLOTS:
        return {"error": f"Slot 1-{MAX_SLOTS} ke beech hona chahiye"}
    schedule = {
        "listings_today": 0,
        "last_listing_time": None,
        "last_reset_date": datetime.now().strftime("%Y-%m-%d"),
        "history": [],
    }
    save_schedule(slot, schedule)
    return {"status": "reset", "slot": slot}


# ── Scheduler Control APIs ───────────────────────────────────────

@app.post("/api/scheduler/start")
async def start_scheduler(config: SchedulerStartRequest):
    if scheduler_state["running"]:
        return {"error": "Scheduler already running!"}
    asyncio.create_task(run_scheduler_engine(config))
    return {"status": "started", "message": "Scheduler started! Browsing and listing will begin."}


@app.post("/api/scheduler/stop")
async def stop_scheduler():
    scheduler_state["running"] = False
    return {"status": "stopping", "message": "Scheduler is stopping..."}


@app.get("/api/scheduler/status")
async def get_scheduler_status():
    profiles_clean = {}
    for k, v in scheduler_state["profiles"].items():
        profiles_clean[k] = {
            key: val for key, val in v.items()
            if key not in ("page", "context", "browser")
        }
    return {
        "running": scheduler_state["running"],
        "profiles": profiles_clean,
        "start_time": scheduler_state.get("start_time"),
        "log_count": len(scheduler_state["logs"]),
    }


@app.get("/api/scheduler/logs")
async def get_scheduler_logs(limit: int = 100):
    return {"logs": scheduler_state["logs"][-limit:]}


# ── WebSocket ─────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    try:
        profiles_clean = {}
        for k, v in scheduler_state["profiles"].items():
            profiles_clean[k] = {
                key: val for key, val in v.items()
                if key not in ("page", "context", "browser")
            }
        await websocket.send_json({
            "type": "init",
            "state": {
                "running": scheduler_state["running"],
                "profiles": profiles_clean,
            },
            "logs": scheduler_state["logs"][-100:],
        })
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in connected_clients:
            connected_clients.remove(websocket)


# ── Run ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("""
 ====================================================
   Facebook Marketplace Auto Lister v7
   ------------------------------------
   Open in browser: http://localhost:8000
   
   Features:
   - 5 Profile Slots with dedicated proxies
   - Human-like behavior (scrolling, likes, browsing)
   - Smart scheduling (1/hour, max 3/day)
   - City-specific listings per profile
   - Heavy browsing between listings
   - Browser history building
   
   Press Ctrl+C to stop
 ====================================================
    """)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
