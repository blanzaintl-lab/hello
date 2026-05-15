# Facebook Marketplace Auto Lister v6

10 Cookie Slots ke sath Facebook Marketplace pe auto listing tool.

## Features
- **10 Cookie Slots** - Backend me saved, restart pe bhi persist karte hain
- **Sirf Cookies se Login** - No email/password, no SessionBox, no Chrome scan
- **Jab Chaaho Update Karo** - New cookies daal ke turant kaam start karo
- **270+ American Cities** - Random rotation with ZIP codes
- **Auto Random** - Categories, prices, conditions sab auto generate
- **Parallel Posting** - Multiple accounts simultaneously
- **Open All IDs in Tabs** - Har ID alag browser tab me

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

## How to Use

1. **Cookies Add Karo**: Har slot me "Add" button dabao aur Facebook cookies paste karo
2. **Cookies Kaise Laayein**: Chrome me Cookie-Editor extension se export karo (JSON format)
3. **Select Karo**: Jo IDs use karni hain unke checkbox select karo
4. **Image Folder**: Apni images ka folder path dalo
5. **Start**: "Start Listing" dabao ya "Open All IDs in Tabs" se check karo

## Folder Structure

```
.
├── server.py           # Backend server (FastAPI)
├── static/
│   └── index.html      # Frontend UI
├── data/
│   ├── cities.json     # 270+ US cities
│   └── categories.json # Marketplace categories
├── cookies/            # Cookie storage (auto-created, gitignored)
└── browser_profiles/   # Browser data (auto-created, gitignored)
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
