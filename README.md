# Wallpaper Fetcher

Local Flask tool jo **free/open sources se wallpapers fetch karta hai** — keyword input, koi bhi colour, orientation filter, quality ranking, source links ke saath manifest. Sab kuch browser me, `http://127.0.0.1:8765` pe.

```bash
python wallpaper_fetcher.py
# -> http://127.0.0.1:8765
```

Ya double-click: `start-wallpaper-tool.bat`

---

## 7 Sources (sab free)

| Source | Key? | Notes |
|---|---|---|
| **Wallhaven** | ❌ | Highest quality wallpapers, tag-based search |
| **Openverse** | ❌ | Agg for CC images — anon **20/min, 200/day** |
| **Wikimedia Commons** | ❌ | Uses `thumb.wikimedia.org` (original host 429 deta hai) |
| **Art Institute of Chicago** | ❌ | Needs `AIC-User-Agent` |
| **Cleveland Museum of Art** | ❌ | `width`/`height` strings hote hain |
| **NASA** | ❌ | Large public-domain library |
| **Pinterest** | ❌ | **Playwright scraper** — official API nahi chalta (niche dekho) |

---

## Features

- **Fair-share quota** per source — warna Wallhaven poora pool bhar deta hai aur baaki kabhi chalte nahi
- **2-pass search**: tez sources (wallhaven/commons/artic/cma/nasa) pehle *saare* keywords, phir slow sources (Pinterest ~6 min browser, Openverse rate-limited)
- **Quality pipeline**: resolution gate → pixel analysis (hue / brightness / saturation / edge) → smart tag+title filter (text, logo, food, brand hatao) → aHash visual dedupe
- **Orientation filter**: `landscape` (default) / `portrait` / `any`
- **Cross-run dedupe**: manifest me `ahash` + `id` store hota hai, toh ek hi folder me baar baar chalane pe purani images skip hoti hain
- **Manifest** (CSV) har image ka source link, license, resolution, score deta hai
- **Time budgets** — search/relaxed phases kabhi ghanton nahi chalte

## Output

```
C:\Users\hp\Pictures\<folder>\
  ├── wp-0001.jpg ...
  └── manifest.csv      # file, resolution, source, landing URL, license, score
```

`newfolder` untick karke + `folder` do to ek hi folder me accumulate hota rahega (tab tak chalao jab tak target na mil jaye).

---

## Pinterest

### Default path — bina key chalta hai ✅

Pinterest ke **public search/ideas feeds** Playwright ke through scrape hote hain — koi API key nahi chahiye:

```
pin_home_feed.py      # Playwright scraper (--allow-anon, headless, persistent profile)
```

Koi login nahi chahiye. Results mostly **portrait** (phone wallpapers) hote hain, isliye:

> **Landscape + Pinterest ≈ 0 yield.** Phone/vertical chahiye to `Orientation = Portrait` ya `Any` chuno.

### Official API (optional) — ye blocked hai

Pinterest v5 API abhi chalta nahi:

- `401 code 3` — *"Your application consumer type is not supported"* (har v5 endpoint pe)
- `/search/pins/partner` → `401 code 3` — *restricted feature `pin_search`*
- Sandbox → `402 code 3022`

Isliye scraper hi primary raasta hai. Phir bhi token dena ho to:

```bash
cp pinterest.json.example pinterest.json   # access_token paste karo
```
> ⚠️ `pinterest.json` **gitignored** hai (isme token hota hai). Kabhi commit mat karo.

### One-time session handoff (sirf personalization ke liye)

Bina login ke generic results milte hain. Apni **personalized home feed** chahiye to Chrome session ek baar nikal lo (Chrome **band** hona chahiye — Cookies DB warna locked rehti hai):

```bash
pinterest-session.bat      # guided: Chrome band -> ENTER -> session save
pinterest-login.bat        # alternate: headed browser me khud login
```

> ⚠️ `.pin_profile/` **gitignored** hai — isme browser saved passwords + session cookies hote hain.

---

## Settings samajh lo (yield ispe depend karta hai)

Empirical numbers (same keywords, 6 sources):

| Settings | Match rate |
|---|---|
| `landscape` + `soft` | ~2% |
| `any` + `soft` | ~11% |
| **`any` + `any`** | **~75%** |

**`style=soft` sabse bada filter hai** — vivid nature/photos zyadatar reject ho jaate hain. Volume chahiye to `style=any`.

**`min_res`**: search phase me exactly enforce hota hai (≤2560), sirf high-res (>2560) pe thoda dhila kiya jaata hai — warna sources (CMA at 4K) seedha 0 dete hain. Exact `min_res` hamesha `save()` me pixels se check hota hai.

**Bade counts (1000+)**: `Orientation=Any` + `min_res=1280` chuno, warna filters pool kaat dete hain. Max count **10000**.

---

## Files

```
wallpaper_fetcher.py          # Flask app + search/analyse/save pipeline (main)
pin_home_feed.py              # Pinterest Playwright scraper
chrome_pinterest_session.py   # Chrome Cookies DB -> pin_session.json (DPAPI/AES-GCM)
fix_manifest.py               # manifest <-> disk reconcile (crash recovery)
nature-7000-chain.ps1         # example: tab tak runs jab tak folder target na bhar jaye
pinterest-session.bat         # guided session handoff
pinterest-login.bat           # headed-browser login
start-wallpaper-tool.bat      # launcher
```

---

## Notes / limits

- **Openverse** anon quota: 20/min, 200/day — bade runs me wo last me chalta hai
- **Wikimedia** original host (`upload.wikimedia.org`) 429 deta hai → thumbnail host use hota hai
- Ek waqt me ek hi job chalta hai (`JOB_QUEUE`) — rate-limits nahi hote
- Jobs in-memory hain: **server restart = chal raha job cancel**
- Pinterest content **user-generated** hai → personal use only
