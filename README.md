# Wallpaper Fetcher

A local Flask tool that **fetches wallpapers from free / open sources** — keyword input, any
colour, orientation filter, quality ranking, and a `manifest.csv` with the source link for
every image. Runs entirely on your machine at `http://127.0.0.1:8765`.

```bash
python wallpaper_fetcher.py
# -> http://127.0.0.1:8765
```

Or double-click: `start-wallpaper-tool.bat`

---

## 7 Sources (all free)

| Source | Key? | Notes |
|---|---|---|
| **Wallhaven** | No | Highest quality wallpapers, tag-based search |
| **Openverse** | No | Aggregator for CC images — anonymous **20/min, 200/day** |
| **Wikimedia Commons** | No | Uses `thumb.wikimedia.org` (the original host 429s) |
| **Art Institute of Chicago** | No | Requires an `AIC-User-Agent` header |
| **Cleveland Museum of Art** | No | `width`/`height` come back as strings |
| **NASA** | No | Large public-domain library |
| **Pinterest** | No | **Playwright scraper** — the official API is blocked (see below) |

---

## Features

- **Fair-share quota** per source — otherwise Wallhaven fills the entire pool and the rest
  never run
- **2-pass search**: the fast sources (wallhaven/commons/artic/cma/nasa) cover *all* keywords
  first, then the slow ones (Pinterest ≈ 6 min of browser, Openverse is rate-limited)
- **Quality pipeline**: resolution gate → pixel analysis (hue / brightness / saturation /
  edge) → smart tag + title filter (text, logo, food, brand removed) → aHash visual dedupe
- **Orientation filter**: `landscape` (default) / `portrait` / `any`
- **Cross-run dedupe**: the manifest stores `ahash` + `id`, so running repeatedly into one
  folder skips the images you already have
- **Manifest** (CSV) with the source link, licence, resolution and score of every image
- **Time budgets** on the search / relaxed phases, so a big request never runs for hours

## Output

```
C:\Users\hp\Pictures\<folder>\
  ├── wp-0001.jpg ...
  └── manifest.csv      # file, resolution, source, landing URL, licence, score
```

Untick `newfolder` and set `folder` to keep accumulating into one folder (run it as long as
you like until the target is reached).

---

## Pinterest

### Default path — works without a key ✅

Pinterest's **public search / ideas feeds** are scraped through Playwright — no API key
involved:

```
pin_home_feed.py      # Playwright scraper (--allow-anon, headless, persistent profile)
```

No login required. Results are mostly **portrait** (phone wallpapers), so:

> **Landscape + Pinterest ≈ 0 yield.** For phone / vertical wallpapers pick
> `Orientation = Portrait` or `Any`.

### Official API (optional) — currently blocked

The Pinterest v5 API does not work:

- `401 code 3` — *"Your application consumer type is not supported"* (every v5 endpoint)
- `/search/pins/partner` → `401 code 3` — *restricted feature `pin_search`*
- Sandbox → `402 code 3022`

So the scraper is the primary path. If you still want to store a token:

```bash
cp pinterest.json.example pinterest.json   # paste your access_token
```

> ⚠️ `pinterest.json` is **gitignored** (it holds your token). Never commit it.

### One-time session handoff (personalization only)

Without logging in you get generic results. For your **personalized home feed**, extract
your Chrome session once (Chrome must be **closed** — otherwise the Cookies DB is locked):

```bash
pinterest-session.bat      # guided: close Chrome -> ENTER -> session saved
pinterest-login.bat        # alternative: log in yourself in a headed browser
```

> ⚠️ `.pin_profile/` is **gitignored** — it contains the browser's saved passwords and
> session cookies.

---

## Understand the settings (yield depends on them)

Measured numbers (same keywords, 6 sources):

| Settings | Match rate |
|---|---|
| `landscape` + `soft` | ~2% |
| `any` + `soft` | ~11% |
| **`any` + `any`** | **~75%** |

**`style=soft` is the single biggest filter** — vivid nature photos get rejected almost
always. For volume use `style=any`.

**`min_res`**: enforced exactly during the search phase (≤2560) and only relaxed for very
high resolutions (>2560) — otherwise some sources (CMA at 4K) return nothing at all. The
exact `min_res` is always verified against the real pixels in `save()`.

**Big counts (1000+)**: pick `Orientation=Any` + `min_res=1280`, otherwise the filters eat
the pool. Maximum count is **10000**.

---

## Files

```
wallpaper_fetcher.py          # Flask app + search/analyse/save pipeline (main)
pin_home_feed.py              # Pinterest Playwright scraper
chrome_pinterest_session.py   # Chrome Cookies DB -> pin_session.json (DPAPI/AES-GCM)
fix_manifest.py               # manifest <-> disk reconcile (crash recovery)
nature-7000-chain.ps1         # example: keeps running until the folder hits a target
pinterest-session.bat         # guided session handoff
pinterest-login.bat           # headed-browser login
start-wallpaper-tool.bat      # launcher
```

---

## Notes / limits

- **Openverse** anonymous quota: 20/min, 200/day — on big runs it goes last
- **Wikimedia**'s original host (`upload.wikimedia.org`) 429s → the thumbnail host is used
- One job at a time (`JOB_QUEUE`) so the APIs never rate-limit you
- Jobs live in memory: **restarting the server cancels anything running**
- Pinterest content is **user-generated** → personal use only
