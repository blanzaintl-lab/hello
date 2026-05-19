# Facebook Marketplace Auto Lister v7

5 Profiles ke sath human-like Facebook Marketplace auto listing tool.

## Key Features
- **5 Profile Slots** - Har profile ke liye alag cookies, proxy IP, aur city region
- **Human-Like Behavior** - Scrolling, random likes, news feed browsing, marketplace browsing
- **Smart Scheduling** - Har 1 ghante me sirf 1 listing, 24 ghante me maximum 3 listing per profile
- **City-Specific Listings** - Har profile apni assigned region (surrounding area) me hi listing karta hai
- **Heavy Browsing** - Listing ke beech me zyada se zyada browsing karta hai ta ke Facebook ko bot na lage
- **Browser History Building** - Har profile ka realistic browser history banta hai
- **Dedicated Proxy IPs** - Har profile ke liye alag IP (e.g., New York ka ek, California ka ek)
- **Cookie Login** - Sirf cookies se login, no email/password
- **Persistent Data** - Cookies, configs, schedule sab restart pe bhi save rehta hai

## How It Works

1. Scheduler start hone pe har enabled profile ke liye alag browser khulta hai
2. Pehle browser history build hota hai (Google, YouTube, Reddit etc.)
3. Phir Facebook pe news feed browse karta hai, random posts like karta hai
4. Marketplace browse karta hai, items dekhta hai, search karta hai
5. Phir 1 listing post karta hai apni assigned city region me
6. Listing ke baad phir browsing karta hai
7. 1 ghanta wait karta hai (browsing karte huay)
8. Next listing post karta hai
9. 3 listings ke baad sirf browsing karta hai (no more listings for the day)

## Install

```bash
pip install fastapi uvicorn playwright aiofiles
python -m playwright install chromium
```

## Run

```bash
python server.py
```

Browser me open karo: http://localhost:8000

## Setup Guide

### 1. Cookies Add Karo
- Har profile me "Add" button dabao
- Facebook cookies paste karo (Cookie-Editor extension se export)

### 2. Proxy Set Karo
- Har profile ke liye dedicated proxy IP daalo
- Format: `socks5://ip:port` ya `http://user:pass@ip:port`
- Har profile ko alag IP do (e.g., New York IP, California IP)

### 3. City Region Select Karo
- Har profile ke liye city region select karo
- 5 regions available: New York, Los Angeles, Houston, Miami, Chicago
- Profile sirf apni region ke surrounding area me listing karega

### 4. Enable Profiles
- Jo profiles use karne hain unka toggle ON karo

### 5. Start Scheduler
- Image folder path daalo
- "Start Scheduler" dabao
- Tool automatically:
  - Browse karega (heavy browsing)
  - 1 listing post karega
  - 1 ghanta wait karega (browsing ke sath)
  - Repeat (max 3/day per profile)

## Folder Structure

```
.
├── server.py              # Backend server (FastAPI)
├── static/
│   └── index.html         # Frontend UI
├── data/
│   ├── cities.json        # 270+ US cities
│   ├── categories.json    # Marketplace categories
│   └── city_groups.json   # 5 city regions with surrounding areas
├── cookies/               # Cookie storage + profile configs
└── browser_profiles/      # Browser data per profile
```

## Cookie Format

Cookie-Editor extension se export karo, ya manually is format me banao:

```json
[
  {"name": "c_user", "value": "YOUR_FB_ID", "domain": ".facebook.com"},
  {"name": "xs", "value": "YOUR_XS_VALUE", "domain": ".facebook.com"},
  {"name": "datr", "value": "YOUR_DATR", "domain": ".facebook.com"}
]
```

**Important**: `c_user` aur `xs` cookies zaroori hain login ke liye.

## Schedule Details

| Setting | Value |
|---------|-------|
| Listing Interval | 1 hour between listings |
| Max Daily Listings | 3 per profile per day |
| Browsing Duration | 2-8 minutes per session |
| Pre-listing Browse | News feed + Marketplace browse |
| Post-listing Browse | News feed browse |
| Wait Period Browse | Heavy browsing while waiting |
| Browser History | 3-6 random sites on startup |

## 5 City Regions

| Region | Main City | Surrounding Areas |
|--------|-----------|-------------------|
| New York | New York, NY | Brooklyn, Queens, Jersey City, Newark, Yonkers, etc. |
| Los Angeles | Los Angeles, CA | Long Beach, Santa Monica, Pasadena, Glendale, etc. |
| Houston | Houston, TX | Sugar Land, Pearland, Katy, The Woodlands, etc. |
| Miami | Miami, FL | Fort Lauderdale, Hollywood, Hialeah, Boca Raton, etc. |
| Chicago | Chicago, IL | Aurora, Naperville, Joliet, Evanston, etc. |
