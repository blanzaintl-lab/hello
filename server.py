"""
Facebook Marketplace Auto Lister v6
====================================
- 10 Cookie Slots (backend me save, jab chaaho update karo)
- Sirf cookies se login (no email/password, no SessionBox, no Chrome scan)
- Persistent cookies folder - cookies delete nahi hoti restart pe
- ALL American cities (270+) with random rotation
- Random categories, conditions, prices

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

MAX_SLOTS = 10

with open(BASE_DIR / "data" / "cities.json", "r") as f:
    ALL_CITIES = json.load(f)

with open(BASE_DIR / "data" / "categories.json", "r") as f:
    ALL_CATEGORIES = json.load(f)

CONDITIONS = ["New", "Used - Like New", "Used - Good", "Used - Fair"]

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


# ── Cookie Slot Management ────────────────────────────────────────

def get_cookie_file(slot: int) -> Path:
    return COOKIES_DIR / f"slot_{slot}.json"


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


def get_all_slots() -> list[dict]:
    slots = []
    for i in range(1, MAX_SLOTS + 1):
        data = load_slot(i)
        if data:
            slots.append({"slot": i, "id": data.get("id", ""), "name": data.get("name", ""), "has_cookies": True})
        else:
            slots.append({"slot": i, "id": "", "name": "", "has_cookies": False})
    return slots


def extract_fb_id_from_cookies(cookies: list[dict]) -> str:
    for c in cookies:
        if c.get("name") == "c_user" and c.get("value"):
            return c["value"]
    return ""


# ── App ───────────────────────────────────────────────────────────

app = FastAPI(title="FB Marketplace Auto Lister v6")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Global state
listing_state = {
    "running": False,
    "total": 0,
    "completed": 0,
    "current": "",
    "logs": [],
    "error": "",
    "accounts": {},
}
connected_clients: list[WebSocket] = []

# Global state for open browser tabs
open_tabs_state = {
    "browser": None,
    "playwright_instance": None,
    "pages": {},
    "running": False,
}


class AccountConfig(BaseModel):
    cookies_json: str = ""
    account_id: str = ""
    account_name: str = ""


class ListingConfig(BaseModel):
    image_folder: str
    service_type: str = "Professional Service"
    min_price: int = 250
    max_price: int = 350
    delay_between: int = 30
    accounts: list[AccountConfig] = []


class SaveCookieRequest(BaseModel):
    slot: int
    cookies_json: str
    name: str = ""


async def broadcast(data: dict):
    dead = []
    for ws in connected_clients:
        try:
            await ws.send_json(data)
        except:
            dead.append(ws)
    for ws in dead:
        connected_clients.remove(ws)


async def log(msg: str, account_id: str = ""):
    timestamp = time.strftime("%H:%M:%S")
    prefix = f"[Account {account_id}] " if account_id else ""
    entry = f"[{timestamp}] {prefix}{msg}"
    listing_state["logs"].append(entry)
    if len(listing_state["logs"]) > 1000:
        listing_state["logs"] = listing_state["logs"][-1000:]
    await broadcast({
        "type": "log",
        "message": entry,
        "account_id": account_id,
        "state": {
            "running": listing_state["running"],
            "total": listing_state["total"],
            "completed": listing_state["completed"],
            "current": listing_state["current"],
            "accounts": listing_state["accounts"],
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
    except:
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
    except:
        pass
    return False


async def login_with_cookies(context, page, cookies_json: str, acc_id: str) -> bool:
    try:
        cookies = json.loads(cookies_json)
        if not isinstance(cookies, list):
            await log("ERROR: Cookies format galat hai - list honi chahiye", acc_id)
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
            await log("ERROR: Koi valid cookie nahi mili", acc_id)
            return False

        await log(f"Adding {len(clean_cookies)} cookies...", acc_id)
        await context.add_cookies(clean_cookies)
        await asyncio.sleep(1)

        await page.goto("https://www.facebook.com", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
        await handle_cookie_consent(page)

        if await check_logged_in(page):
            await log("Cookie login successful!", acc_id)
            return True
        else:
            await log("ERROR: Cookie login failed - cookies expired ho gayi hain", acc_id)
            return False

    except json.JSONDecodeError:
        await log("ERROR: Cookies JSON parse nahi ho raha - format check karo", acc_id)
        return False
    except Exception as e:
        await log(f"ERROR: Cookie login error: {str(e)[:60]}", acc_id)
        return False


async def scan_page_elements(page, acc_id: str):
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
        await log(f"  === PAGE ELEMENTS SCAN ({len(info)} found) ===", acc_id)
        for item in info[:25]:
            await log(f"    {json.dumps(item)}", acc_id)
        return info
    except Exception as e:
        await log(f"  Scan error: {str(e)[:50]}", acc_id)
        return []


async def react_fill_field(page, field_info: dict, value: str, acc_id: str) -> bool:
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
                await page.keyboard.type(value, delay=20)
                await asyncio.sleep(0.3)
                return True
        return False
    except:
        return False


async def find_and_click_field(page, label_keywords: list, acc_id: str) -> bool:
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

        except:
            continue
    return False


async def type_in_focused(page, value: str, clear_first: bool = True):
    if clear_first:
        await page.keyboard.press("Control+a")
        await asyncio.sleep(0.1)
        await page.keyboard.press("Backspace")
        await asyncio.sleep(0.2)
    await page.keyboard.type(value, delay=20)
    await asyncio.sleep(0.3)


async def post_to_marketplace(page, listing: dict, acc_id: str) -> bool:
    try:
        await log(f"Posting: {listing['title'][:50]}...", acc_id)

        await page.goto("https://www.facebook.com/marketplace/create/item", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(5)

        # Dismiss any popups
        try:
            for sel in ['div[aria-label="Close"]', 'div[aria-label="Dismiss"]']:
                btn = page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click()
                    await asyncio.sleep(1)
        except:
            pass

        elements = await scan_page_elements(page, acc_id)

        # Step 1: Upload image
        try:
            file_input = page.locator('input[type="file"]').first
            if await file_input.count() > 0:
                await file_input.set_input_files(listing["image_path"])
                await asyncio.sleep(4)
                await log(f"  Image uploaded", acc_id)
        except Exception as e:
            await log(f"  Image upload error: {str(e)[:50]}", acc_id)

        # Build a map of fields from scan
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

        await log(f"  Fields detected: {list(field_map.keys())}", acc_id)

        # Step 2: Fill Title
        filled = False
        if "title" in field_map:
            filled = await react_fill_field(page, field_map["title"], listing["title"], acc_id)
        if not filled:
            filled = await find_and_click_field(page, ["Title", "title", "Item name"], acc_id)
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
            await page.keyboard.type(listing["title"], delay=20)
            filled = True
        await log(f"  Title: {'OK' if filled else 'FAILED'}", acc_id)
        await asyncio.sleep(0.5)

        # Step 3: Fill Price
        filled = False
        if "price" in field_map:
            filled = await react_fill_field(page, field_map["price"], str(listing["price"]), acc_id)
        if not filled:
            filled = await find_and_click_field(page, ["Price", "price"], acc_id)
            if filled:
                await type_in_focused(page, str(listing["price"]))
        if not filled:
            await page.keyboard.press("Tab")
            await asyncio.sleep(0.3)
            await page.keyboard.type(str(listing["price"]), delay=20)
        await log(f"  Price: ${listing['price']}", acc_id)
        await asyncio.sleep(0.5)

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
                    except:
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
            await log(f"  Category error: {str(e)[:40]}", acc_id)
        await log(f"  Category: {'OK' if cat_done else 'SKIP'}", acc_id)
        await asyncio.sleep(0.5)

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
                    except:
                        continue

                if not option_found:
                    await page.keyboard.press("Escape")
                    cond_done = False
        except Exception as e:
            await log(f"  Condition error: {str(e)[:40]}", acc_id)
        await log(f"  Condition: {'OK' if cond_done else 'SKIP'}", acc_id)
        await asyncio.sleep(0.5)

        # SCROLL DOWN
        await page.evaluate("window.scrollBy(0, 500)")
        await asyncio.sleep(1)

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
                        await page.keyboard.type(listing["description"][:500], delay=10)
                        filled = True
                        break
                if not filled:
                    await page.keyboard.type(listing["description"][:500], delay=10)
                    filled = True
        except:
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
                        await page.keyboard.type(listing["description"][:500], delay=10)
                        filled = True
                        break
            except:
                pass

        if not filled:
            for sel in ['textarea[aria-label*="escription"]', 'input[aria-label*="escription"]',
                        '[role="textbox"][aria-label*="escription"]']:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0 and await el.is_visible():
                        await el.click()
                        await asyncio.sleep(0.3)
                        await page.keyboard.type(listing["description"][:500], delay=10)
                        filled = True
                        break
                except:
                    continue

        await log(f"  Description: {'OK' if filled else 'SKIP'}", acc_id)
        await asyncio.sleep(0.5)

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
                    await page.keyboard.type(location_text, delay=40)
                    await asyncio.sleep(2.5)
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
            except:
                continue

        if not loc_done:
            try:
                loc_label = page.locator('label:has(span:has-text("Location"))').first
                if await loc_label.count() > 0 and await loc_label.is_visible():
                    await loc_label.click()
                    await asyncio.sleep(0.5)
                    await page.keyboard.type(location_text, delay=40)
                    await asyncio.sleep(2.5)
                    sug = page.locator('div[role="option"]').first
                    if await sug.count() > 0:
                        await sug.click()
                        loc_done = True
            except:
                pass

        await log(f"  Location: {'OK' if loc_done else 'SKIP'}", acc_id)
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
                        await asyncio.sleep(3)
                        published = True
                        break
                if published:
                    break
            except:
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
                        await asyncio.sleep(3)
                        break
                except:
                    continue

        await log(f"  DONE: {listing['title'][:40]} | {listing['city']}, {listing['state']} | ${listing['price']}", acc_id)
        return True

    except Exception as e:
        await log(f"  Error: {str(e)[:80]}", acc_id)
        return False


async def run_account(pw, account: AccountConfig, acc_id: str, listings: list, config: ListingConfig):
    listing_state["accounts"][acc_id] = {"status": "starting", "completed": 0, "current": "", "total": len(listings)}

    try:
        user_data_dir = str(BASE_DIR / "browser_profiles" / f"account_{acc_id}")
        os.makedirs(user_data_dir, exist_ok=True)

        await log(f"Launching browser...", acc_id)
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-dev-shm-usage",
            ],
            ignore_default_args=["--enable-automation"],
        )

        page = context.pages[0] if context.pages else await context.new_page()
        await page.add_init_script(STEALTH_JS)

        await log("Checking login status...", acc_id)
        await page.goto("https://www.facebook.com", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
        await handle_cookie_consent(page)

        already_logged_in = await check_logged_in(page)

        if already_logged_in:
            await log("Already logged in (saved session)!", acc_id)
        else:
            if account.cookies_json.strip():
                success = await login_with_cookies(context, page, account.cookies_json, acc_id)
            else:
                await log("ERROR: No cookies provided!", acc_id)
                listing_state["accounts"][acc_id]["status"] = "failed"
                await context.close()
                return

            if not success:
                listing_state["accounts"][acc_id]["status"] = "login_failed"
                await context.close()
                return

        listing_state["accounts"][acc_id]["status"] = "running"
        await log(f"Starting {len(listings)} listings...", acc_id)

        for i, listing in enumerate(listings):
            if not listing_state["running"]:
                await log("Stopped by user", acc_id)
                break

            listing_state["accounts"][acc_id]["current"] = f"{listing['title'][:35]} -> {listing['city']}, {listing['state']}"
            await log(f"[{i+1}/{len(listings)}] {listing['title'][:50]}", acc_id)
            await log(f"  City: {listing['city']}, {listing['state']} | ${listing['price']} | {listing['condition']}", acc_id)

            success = await post_to_marketplace(page, listing, acc_id)

            if success:
                listing_state["accounts"][acc_id]["completed"] += 1
                listing_state["completed"] += 1

            if i < len(listings) - 1 and listing_state["running"]:
                delay = config.delay_between + random.randint(-5, 10)
                delay = max(10, delay)
                await log(f"  Waiting {delay}s...", acc_id)
                await asyncio.sleep(delay)

        await context.close()
        acc_completed = listing_state["accounts"][acc_id]["completed"]
        listing_state["accounts"][acc_id]["status"] = "done"
        await log(f"Complete! {acc_completed}/{len(listings)} listings posted", acc_id)

    except Exception as e:
        await log(f"ERROR: {str(e)[:80]}", acc_id)
        listing_state["accounts"][acc_id]["status"] = "error"


async def run_listing_engine(config: ListingConfig):
    from playwright.async_api import async_playwright

    listing_state["running"] = True
    listing_state["completed"] = 0
    listing_state["error"] = ""
    listing_state["logs"] = []
    listing_state["accounts"] = {}

    try:
        images = get_image_files(config.image_folder)
        if not images:
            images = get_image_files(os.path.join(config.image_folder, "images"))

        if not images:
            await log(f"ERROR: No images found in {config.image_folder}")
            listing_state["running"] = False
            listing_state["error"] = "No images found"
            return

        valid_accounts = [acc for acc in config.accounts if acc.cookies_json.strip()]

        if not valid_accounts:
            await log("ERROR: No valid accounts with cookies!")
            listing_state["running"] = False
            listing_state["error"] = "No valid accounts"
            return

        num_accounts = len(valid_accounts)
        listing_state["total"] = len(images)
        await log(f"Found {len(images)} images")
        await log(f"Active accounts: {num_accounts}")

        cities = ALL_CITIES.copy()
        random.shuffle(cities)

        all_listings = []
        for i, img_path in enumerate(images):
            city_data = cities[i % len(cities)]
            title = extract_title_from_filename(img_path.name)
            if not title or len(title) < 3:
                title = f"{config.service_type} #{i+1}"

            all_listings.append({
                "title": title,
                "description": generate_description(title, city_data["city"], city_data["state"]),
                "price": random.randint(config.min_price, config.max_price),
                "category": random.choice(ALL_CATEGORIES),
                "condition": random.choice(CONDITIONS),
                "city": city_data["city"],
                "state": city_data["state"],
                "zip": city_data["zip"],
                "image_path": str(img_path),
                "image_name": img_path.name,
            })

        chunks = [[] for _ in range(num_accounts)]
        for i, listing in enumerate(all_listings):
            chunks[i % num_accounts].append(listing)

        await log(f"Listings per account: {[len(c) for c in chunks]}")
        await log("")
        await log("-- Preview (first 5) --")
        for l in all_listings[:5]:
            await log(f"  {l['title'][:40]} | {l['city']}, {l['state']} | ${l['price']}")
        await log("")

        async with async_playwright() as pw:
            tasks = []
            for idx, account in enumerate(valid_accounts):
                acc_id = str(idx + 1)
                task = asyncio.create_task(run_account(pw, account, acc_id, chunks[idx], config))
                tasks.append(task)

            await asyncio.gather(*tasks, return_exceptions=True)

        await log("")
        await log(f"=== ALL COMPLETE! {listing_state['completed']}/{listing_state['total']} listings posted ===")

    except Exception as e:
        await log(f"ERROR: {str(e)}")
        listing_state["error"] = str(e)
    finally:
        listing_state["running"] = False
        await broadcast({"type": "done", "state": listing_state})


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


# ── Listing APIs ──────────────────────────────────────────────────

@app.post("/api/preview")
async def preview_listings(config: ListingConfig):
    images = get_image_files(config.image_folder)
    if not images:
        images = get_image_files(os.path.join(config.image_folder, "images"))

    if not images:
        return {"error": f"No images found in {config.image_folder}", "listings": []}

    cities = ALL_CITIES.copy()
    random.shuffle(cities)

    preview = []
    for i, img_path in enumerate(images[:20]):
        city_data = cities[i % len(cities)]
        title = extract_title_from_filename(img_path.name)
        if not title or len(title) < 3:
            title = f"{config.service_type} #{i+1}"

        preview.append({
            "number": i + 1,
            "title": title,
            "description": generate_description(title, city_data["city"], city_data["state"])[:100] + "...",
            "price": random.randint(config.min_price, config.max_price),
            "category": random.choice(ALL_CATEGORIES),
            "condition": random.choice(CONDITIONS),
            "city": f"{city_data['city']}, {city_data['state']}",
            "image": img_path.name,
        })

    return {"total_images": len(images), "listings": preview}


@app.post("/api/open-tabs")
async def open_all_tabs_api(data: dict):
    if open_tabs_state["running"]:
        return {"error": "Tabs pehle se open hain! Pehle 'Close All Tabs' karo."}

    accounts_data = data.get("accounts", [])
    if not accounts_data:
        return {"error": "Koi account select nahi hua!"}

    valid_accounts = []
    for acc in accounts_data:
        if acc.get("cookies_json", "").strip():
            valid_accounts.append(AccountConfig(
                cookies_json=acc["cookies_json"],
                account_id=acc.get("account_id", ""),
                account_name=acc.get("account_name", ""),
            ))

    if not valid_accounts:
        return {"error": "Koi valid account nahi mila cookies ke saath!"}

    asyncio.create_task(_open_tabs_worker(valid_accounts))
    return {
        "status": "opening",
        "message": f"{len(valid_accounts)} accounts ke liye tabs open ho rahi hain...",
        "count": len(valid_accounts),
    }


async def _open_tabs_worker(accounts: list[AccountConfig]):
    from playwright.async_api import async_playwright

    open_tabs_state["running"] = True
    open_tabs_state["pages"] = {}

    try:
        pw = await async_playwright().start()
        open_tabs_state["playwright_instance"] = pw

        browser = await pw.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--start-maximized",
            ],
        )
        open_tabs_state["browser"] = browser

        results = []
        for idx, account in enumerate(accounts):
            acc_id = account.account_id or str(idx + 1)
            acc_name = account.account_name or f"FB {acc_id}"

            try:
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                )

                page = await context.new_page()
                await page.add_init_script(STEALTH_JS)

                cookies = json.loads(account.cookies_json)
                if isinstance(cookies, list):
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

                    if clean_cookies:
                        await context.add_cookies(clean_cookies)

                await page.goto("https://www.facebook.com", wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(2)
                await handle_cookie_consent(page)

                logged_in = await check_logged_in(page)

                open_tabs_state["pages"][acc_id] = {
                    "page": page,
                    "context": context,
                    "name": acc_name,
                    "logged_in": logged_in,
                }

                status = "logged_in" if logged_in else "login_failed"
                results.append({"id": acc_id, "name": acc_name, "status": status})
                await log(f"Tab opened: {acc_name} ({acc_id}) - {'Logged In' if logged_in else 'Login Failed'}")

            except Exception as e:
                results.append({"id": acc_id, "name": acc_name, "status": "error", "error": str(e)[:100]})
                await log(f"Tab error: {acc_name} ({acc_id}) - {str(e)[:60]}")

        await broadcast({
            "type": "tabs_opened",
            "results": results,
            "total": len(results),
            "logged_in": sum(1 for r in results if r["status"] == "logged_in"),
        })

    except Exception as e:
        await broadcast({"type": "tabs_error", "error": str(e)})
        open_tabs_state["running"] = False


@app.post("/api/close-tabs")
async def close_all_tabs():
    try:
        for acc_id, data in open_tabs_state["pages"].items():
            try:
                if data.get("context"):
                    await data["context"].close()
            except:
                pass

        if open_tabs_state.get("browser"):
            try:
                await open_tabs_state["browser"].close()
            except:
                pass

        if open_tabs_state.get("playwright_instance"):
            try:
                await open_tabs_state["playwright_instance"].stop()
            except:
                pass

        open_tabs_state["browser"] = None
        open_tabs_state["playwright_instance"] = None
        open_tabs_state["pages"] = {}
        open_tabs_state["running"] = False

        return {"status": "closed", "message": "Sab tabs band ho gayi!"}
    except Exception as e:
        open_tabs_state["running"] = False
        return {"error": str(e)}


@app.get("/api/tabs-status")
async def get_tabs_status():
    tabs = []
    for acc_id, data in open_tabs_state["pages"].items():
        tabs.append({
            "id": acc_id,
            "name": data.get("name", ""),
            "logged_in": data.get("logged_in", False),
        })
    return {"running": open_tabs_state["running"], "tabs": tabs, "total": len(tabs)}


@app.post("/api/start")
async def start_listing(config: ListingConfig):
    if listing_state["running"]:
        return {"error": "Already running!"}
    asyncio.create_task(run_listing_engine(config))
    return {"status": "started"}


@app.post("/api/stop")
async def stop_listing():
    listing_state["running"] = False
    return {"status": "stopping"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    try:
        await websocket.send_json({
            "type": "init",
            "state": {
                "running": listing_state["running"],
                "total": listing_state["total"],
                "completed": listing_state["completed"],
                "current": listing_state["current"],
                "accounts": listing_state.get("accounts", {}),
            },
            "logs": listing_state["logs"][-100:],
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
   Facebook Marketplace Auto Lister v6
   ------------------------------------
   Open in browser: http://localhost:8000
   
   Features:
   - 10 Cookie Slots (backend me saved)
   - Sirf cookies se login
   - Jab chaaho new cookies daal ke kaam start karo
   - 270+ American cities
   - Auto random categories, prices, conditions
   
   Press Ctrl+C to stop
 ====================================================
    """)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
