"""
Wallpaper Fetcher - local web tool (universal: any colour, any free/open API source)
Run:  python wallpaper_fetcher.py   (or double-click start-wallpaper-tool.bat)
Then: open http://127.0.0.1:8765 in your browser

Sources (free / open, no API key needed - 6 total):
  1. Wallhaven API             https://wallhaven.cc/api-docs
  2. Openverse API             https://api.openverse.org/  (CC-licensed aggregator)
  3. Wikimedia Commons API     https://www.mediawiki.org/wiki/API:Main_page
  4. Art Institute of Chicago  https://api.artic.edu/docs/   (public-domain, IIIF)
  5. Cleveland Museum of Art   https://github.com/clevelandart/api  (CC0)
  6. NASA Image Library        https://images.nasa.gov/       (public domain)
Every wallpaper's source URL and licence are written to manifest.csv.
"""
import io, os, re, csv, json, time, math, colorsys, shutil, threading, uuid, webbrowser, hashlib, base64, subprocess, sys
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from PIL import Image, ImageFilter
from flask import Flask, request, jsonify, send_file, render_template_string

WH_API = "https://wallhaven.cc/api/v1/search"
WH_W = "https://wallhaven.cc/api/v1/w"
OV_API = "https://api.openverse.org/v1/images/"
WM_API = "https://commons.wikimedia.org/w/api.php"
ART_API = "https://api.artic.edu/api/v1/artworks/search"   # Art Institute of Chicago
ART_IIIF = "https://www.artic.edu/iiif/2"
ART_LAND = "https://www.artic.edu/artworks"
CMA_API = "https://openaccess-api.clevelandart.org/api/artworks/"   # Cleveland Museum
NASA_API = "https://images-api.nasa.gov/search"                     # NASA library
PIN_SEARCH = "https://api.pinterest.com/v5/search/partner/pins"     # Pinterest (beta)
PIN_TOKEN = "https://api.pinterest.com/v5/oauth/token"
# client_id / client_secret / access_token go here (inside the tool folder)
PIN_CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pinterest.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
UA_WM = {"User-Agent": "WallpaperFetcher/1.0 (local personal tool; python-requests)"}
# ARTIC's API docs require an AIC-User-Agent header
UA_ART = {"User-Agent": UA["User-Agent"],
          "AIC-User-Agent": "WallpaperFetcher/1.0 (local personal tool)"}
ROOT = r"C:\Users\hp\Pictures"
PORT = 8765

TEXT_EXCL = (" -quote -quotes -text -typography -typo -lettering -font -lyric -poem "
             "-saying -calligraphy -words")

# ---------- color system: the user can pick any colour ----------
# hue ranges (degrees) - matching must be wrap-aware
COLOUR_RANGES = {
    "yellow": (45, 70), "gold": (34, 50), "orange": (18, 40), "red": (345, 15),
    "pink": (300, 345), "purple": (255, 300), "blue": (195, 255), "teal": (160, 195),
    "green": (75, 160),
}
ACHROMATIC = ("white", "grey", "black")
ALL_COLOURS = ["any"] + list(COLOUR_RANGES.keys()) + list(ACHROMATIC)
# every source is free/open; Pinterest only ships a free *trial* tier (needs a key)
ALL_SOURCES = ["wallhaven", "openverse", "commons", "artic", "cma", "nasa", "pinterest"]

# content-based deny (text/logo/IP/food/interiors/vehicles/people ...). NOTE: no
# colour words here - colour is entirely the user's choice.
DENY = [
    "text", "typography", "quote", "quotes", "lettering", "font", "lyric", "watermark",
    "logo", "brand", "meme", "rage", "troll",
    "mario", "nintendo", "pokemon", "spider", "marvel", "batman", "superhero", "minecraft",
    "starwars", "star wars", "disney", "minion", "minions", "zelda", "dc comics",
    "family guy", "stewie griffin", "powerpuff", "cookie monster", "movie characters",
    "universal pictures", "fallout", "pirates", "jedi", "suits", "tv series", "video game",
    "album cover", "band", "singer",
    "salmon", "steak", "burger", "fastfood", "recipe", "cooking", "coffee", "beverage",
    "cocktail", "juice", "sushi", "meat", "milk", "drink", "lemonade", "honey", "honeycomb",
    "eggs", "cereal", "cup", "spoon", "fork", "breakfast", "berries", "dessert", "cake",
    "tableware", "orange (fruit)", "bananas", "food",
    "advertisement", "mockup", "screenshot", "interface", "clock", "money", "dollar",
    "cash", "banknote", "server", "room", "interior", "indoors", "desk", "computer",
    "monitor", "technology", "chair", "workspace", "macos", "windows (operating system)",
    "aircraft", "airplane", "airport", "nasa", "space shuttle", "spaceship", "boeing",
    "tractors", "farmers", "christmas", "sports car", "lamborghini", "vehicle",
    "car", "truck", "racing", "motorcycle",
    "skull", "skeleton", "zombie", "gore", "blood", "smoking", "abandoned", "decay",
    "ruins", "war", "photo manipulation", "photoshopped", "manipulation",
    "numbers", "hands", "men", "women", "lion", "big cats", "crabs", "sea life", "vector",
]
DENY_RE = [re.compile(r"\b" + re.escape(d) + r"\b") for d in DENY]

def title_denied(text, ignore=()):
    """ignore: drop a few words (e.g. 'nasa' when that source is selected)."""
    t = (text or "").lower()
    for rx in DENY_RE:
        if rx.pattern[2:-2] in ignore:      # \bXYZ\b -> XYZ
            continue
        if rx.search(t):
            return True
    return False

# ---------------- helpers ----------------
def _int(v):
    """APIs sometimes return strings (CMA width/height) - safe int."""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0

def hexrgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

def rgb_hue(c):
    r, g, b = c[0] / 255, c[1] / 255, c[2] / 255
    return colorsys.rgb_to_hsv(r, g, b)[0] * 360

def rgb_lum(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]

def rgb_sat(c):
    r, g, b = c
    mx, mn = max(r, g, b), min(r, g, b)
    return 0 if mx == 0 else (mx - mn) / mx

def in_range(h, lo, hi):
    h %= 360; lo %= 360; hi %= 360
    if lo <= hi:
        return lo <= h <= hi
    return h >= lo or h <= hi

def hex_ok(c, colour):
    """Wallhaven top-colours prefilter."""
    if colour is None or colour == "any":
        return True
    if colour in ACHROMATIC:
        L, S = rgb_lum(c), rgb_sat(c)
        if colour == "white":
            return L >= 175 and S <= 0.30
        if colour == "grey":
            return 75 <= L <= 185 and S <= 0.28
        return L <= 80 and S <= 0.45
    lo, hi = COLOUR_RANGES[colour]
    return in_range(rgb_hue(c), lo - 12, hi + 12)

# ---------------- image analysis ----------------
def analyze_bytes(b):
    img = Image.open(io.BytesIO(b)).convert("RGB")
    w, h = img.size
    small = img.resize((64, 64))
    px = list(small.getdata())
    n = len(px)
    R = sum(p[0] for p in px) / n
    G = sum(p[1] for p in px) / n
    B = sum(p[2] for p in px) / n
    L = 0.299 * R + 0.587 * G + 0.114 * B
    hh, ss, _ = colorsys.rgb_to_hsv(R / 255, G / 255, B / 255)
    # per-pixel hue bins (36 x 10 deg), chromatic pixels only
    bins = [0] * 36
    chromatic = 0
    for (r, g, bl) in px:
        mx, mn = max(r, g, bl), min(r, g, bl)
        if mx == 0 or (mx - mn) / mx < 0.12:
            continue
        chromatic += 1
        if mx == r:
            hue = (60 * ((g - bl) / (mx - mn))) % 360
        elif mx == g:
            hue = 60 * ((bl - r) / (mx - mn)) + 120
        else:
            hue = 60 * ((r - g) / (mx - mn)) + 240
        bins[int(hue % 360 // 10)] += 1
    g2 = img.convert("L")
    g2.thumbnail((256, 256))
    edges = g2.filter(ImageFilter.FIND_EDGES)
    ep = list(edges.getdata())
    k = len(ep)
    edge = sum(1 for v in ep if v > 45) / k
    dark = sum(1 for v in g2.getdata() if v < 70) / k
    return {"w": w, "h": h, "L": round(L, 1), "hue": round(hh * 360, 1),
            "sat": round(ss, 2), "edge": round(edge, 4), "dark": round(dark, 4),
            "bins": bins, "chrom": chromatic / n}

def match_frac(m, colour):
    """Fraction of chromatic pixels whose hue falls in the selected colour range (+/-10 tol)."""
    lo, hi = COLOUR_RANGES[colour]
    idxs = [i for i in range(36) if in_range(i * 10 + 5, lo - 10, hi + 10)]
    if m["chrom"] < 0.20:
        return 0.0
    return sum(m["bins"][i] for i in idxs) / max(1, sum(m["bins"]))

def metrics_ok(m, colour, style, relaxed=False, orient="landscape"):
    # ---- orientation gate: a wallpaper should be wide (landscape) ----
    if orient != "any" and m["w"] and m["h"]:
        if orient == "landscape" and m["w"] < m["h"]:
            return False
        if orient == "portrait" and m["h"] < m["w"]:
            return False
    # ---- style gate ----
    if style == "soft":
        if m["L"] < (150 if relaxed else 155) or m["sat"] > (0.70 if relaxed else 0.62):
            return False
        if m["dark"] > (0.30 if relaxed else 0.25) or m["edge"] > (0.24 if relaxed else 0.20):
            return False
    elif style == "vivid":
        if m["L"] < 110 or m["sat"] < (0.30 if relaxed else 0.35):
            return False
        if m["dark"] > 0.40 or m["edge"] > (0.30 if relaxed else 0.25):
            return False
    else:  # any
        if m["edge"] > 0.30 or m["dark"] > 0.50:
            return False
    # ---- colour gate ----
    if colour is None or colour == "any":
        return True
    if colour == "white":
        return m["L"] >= (170 if relaxed else 178) and m["sat"] <= (0.35 if relaxed else 0.28)
    if colour == "grey":
        return 75 <= m["L"] <= 185 and m["sat"] <= (0.32 if relaxed else 0.26)
    if colour == "black":
        return m["L"] <= (85 if relaxed else 72) and m["sat"] <= 0.45
    need = 0.32 if relaxed else 0.45
    return match_frac(m, colour) >= need and m["chrom"] >= (0.15 if relaxed else 0.22)

def score_of(m, colour, style):
    def cl(v, a=0, b=1): return max(a, min(b, v))
    base = (cl(1 - m["edge"] / 0.25) * 30 + cl(1 - m["dark"] / 0.40) * 10
            + cl(m["L"] / 210) * 20 + (10 if 0.15 <= m["sat"] <= 0.60 else 0))
    if colour and colour != "any":
        if colour in COLOUR_RANGES:
            base += match_frac(m, colour) * 30
        elif colour == "white":
            base += cl((m["L"] - 150) / 60) * 30
        elif colour == "grey":
            base += cl(1 - abs(m["L"] - 130) / 60) * 20 + cl(1 - m["sat"] / 0.3) * 10
        else:  # black
            base += cl((85 - m["L"]) / 85) * 30
    return round(base, 2)

def res_bonus(it):
    """Quality score: bigger resolution = higher score -> ranks higher."""
    big = max(it.get("w") or 0, it.get("h") or 0)
    if big >= 3840:
        return 14
    if big >= 2560:
        return 9
    if big >= 1920:
        return 4
    return 0

def ahash(img):
    g = img.convert("L").resize((8, 8))
    px = list(g.getdata())
    avg = sum(px) / 64
    return sum((1 << i) for i, v in enumerate(px) if v > avg)

def src_headers(src):
    """ARTIC's host requires the AIC-User-Agent header, otherwise 403."""
    return UA_ART if src == "artic" else UA

WM_W = (3840, 2560, 1920, 1280)     # thumb.wikimedia.org serves these reliably

def alt_urls(u):
    """Wikimedia originals (upload host) may 429 -> thumb-host fallback ladder.
    Pinterest originals may 403 (not cached) -> size ladder. Thumb URLs stay
    small (downloading full-res just to analyse it would be a waste)."""
    if "i.pinimg.com/" in u:
        try:
            rest = u.split("i.pinimg.com/", 1)[1]
            _sz, hsh = rest.split("/", 1)
            ladder = ("originals", "1200x", "736x") if _sz == "originals" \
                     else ("1200x", "736x", "474x")
            out = [u]
            for px in ladder:
                cand = f"https://i.pinimg.com/{px}/{hsh}"
                if cand not in out:
                    out.append(cand)
            return out
        except Exception:
            return [u]
    if "upload.wikimedia.org/" not in u:
        return [u]
    try:
        clean = u.split("?")[0]
        head, fname = clean.rsplit("/wikipedia/commons/", 1)   # "a/ab/Name.jpg"
        name = fname.split("/")[-1]
        out = [u]
        for px in WM_W:
            out.append(f"https://thumb.wikimedia.org/wikipedia/commons/thumb/"
                       f"{fname}/{px}px-{name}")
        return out
    except Exception:
        return [u]

def dl_first(urls, timeout, tries, headers, backoff=True):
    """Return the first URL in the ladder that answers 200."""
    for i, u in enumerate(urls):
        last = i == len(urls) - 1
        r = http_get(u, timeout=timeout, tries=tries if last else 1,
                     headers=headers, backoff=backoff if last else False)
        if r:
            return r
    return None

def http_get(url, timeout=30, tries=3, params=None, headers=None, backoff=True):
    for i in range(tries):
        try:
            r = requests.get(url, headers=headers or UA, timeout=timeout, params=params)
            if r.status_code == 200:
                return r
            if r.status_code == 429:
                if not backoff:
                    return None        # try the fallback URL (fast-fail, do not wait)
                time.sleep(10 + i * 5)
                continue
            return None
        except Exception:
            if not backoff:
                return None
            time.sleep(1 + i)
    return None

# ---------------- sources (all free / open APIs) ----------------
def search_wallhaven(kw, cat, min_res, colour, smart, pages, seen, out,
                     pacing=0.3, start_page=1, max_add=None):
    """https://wallhaven.cc/api-docs - free, no key. colours prefilter + text exclusions.
    Returns the next page number (for resuming) or None when there are no pages left.
    max_add: only add this many new candidates (fair-share quota)."""
    q = kw + (TEXT_EXCL if smart else "")
    page = start_page
    end = start_page + pages - 1
    added = 0
    while page <= end:
        r = http_get(WH_API, params={"q": q, "categories": cat, "purity": "100",
                                     "sorting": "relevance", "page": page})
        if not r:
            return page            # rate-limit/error: resume from here later
        data = r.json().get("data", [])
        capped = False
        for it in data:
            if max_add is not None and added >= max_add:
                capped = True
                break
            wid = "wh_" + it["id"]
            if wid in seen:
                continue
            if max(it["dimension_x"], it["dimension_y"]) < min_res:
                continue
            if it.get("file_size", 0) > 15_000_000:
                continue
            cols = [hexrgb(c) for c in it.get("colors", [])]
            if cols and not any(hex_ok(c, colour) for c in cols):
                continue
            seen.add(wid)
            out.append({
                "id": wid, "path": it["path"],
                "thumb": it["thumbs"]["small"],
                "ext": ".png" if "png" in it.get("file_type", "") else ".jpg",
                "landing": it["url"], "src": "wallhaven",
                "license": it.get("license", "cc0"),
                "title": it.get("name") or "", "q": kw,
                "w": it["dimension_x"], "h": it["dimension_y"],
                "size": it.get("file_size", 0), "colors": cols, "raw": it,
            })
            added += 1
        if capped:
            return page            # quota filled - resume after this page
        if page >= r.json().get("meta", {}).get("last_page", 1) or len(data) < 24:
            return None             # no more pages
        page += 1
        time.sleep(pacing)
    return page                    # limit reached - later pages kept for resuming

def search_openverse(kw, min_res, colour, seen, out, pages=6, max_add=None):
    """https://api.openverse.org/ - CC-licensed open source. Anon limit 20/min, 200/day."""
    added = 0
    for page in range(1, pages + 1):
        r = http_get(OV_API, params={"q": kw, "page": page, "page_size": 20,
                                     "license_type": "all", "size": "large"}, timeout=35)
        if not r:
            break
        try:
            j = r.json()
        except Exception:
            break
        results = j.get("results", [])
        capped = False
        for it in results:
            if max_add is not None and added >= max_add:
                capped = True
                break
            oid = "ov_" + str(it.get("id"))
            if oid in seen:
                continue
            w, h = it.get("width") or 0, it.get("height") or 0
            if w and max(w, h) < min_res:
                continue
            url = it.get("url") or ""
            if not url:
                continue
            title = it.get("title") or ""
            ext = os.path.splitext(url.split("?")[0])[1].lower()
            ext = ext if ext in (".png", ".jpg", ".jpeg", ".webp") else ".jpg"
            seen.add(oid)
            out.append({
                "id": oid, "path": url,
                "thumb": it.get("thumbnail") or f"{OV_API}{oid[3:]}/thumb/",
                "ext": ".jpg" if ext == ".jpeg" else ext,
                "landing": it.get("foreign_landing_url") or url, "src": "openverse",
                "license": it.get("license") or "", "title": title, "q": kw,
                "w": w, "h": h, "size": 0, "colors": [],
            })
            added += 1
        if capped or not results or page * 20 >= (j.get("result_count") or 0):
            break
        time.sleep(3.2)      # respect the anonymous burst limit of 20/min
    time.sleep(1.0)

def search_commons(kw, min_res, colour, seen, out, pages=3, max_add=None):
    """https://www.mediawiki.org/wiki/API:Main_page - Wikimedia Commons, free licences."""
    added = 0
    for page_i in range(pages):
        r = http_get(WM_API, headers=UA_WM, timeout=30, params={
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": f"{kw} filetype:bitmap", "gsrnamespace": "6",
            "gsrlimit": "50", "gsroffset": str(page_i * 50),
            "prop": "imageinfo", "iiprop": "url|size|mime", "iiurlwidth": "1280"})
        if not r:
            break
        pages_d = (r.json().get("query") or {}).get("pages", {})
        if not pages_d:
            break
        capped = False
        for p in pages_d.values():
            if max_add is not None and added >= max_add:
                capped = True
                break
            ii = (p.get("imageinfo") or [{}])[0]
            cid = "wm_" + str(p.get("pageid"))
            if cid in seen or not ii.get("url"):
                continue
            mime = ii.get("mime", "")
            if mime not in ("image/jpeg", "image/png"):
                continue
            w, h = ii.get("width") or 0, ii.get("height") or 0
            if max(w, h) < min_res or min(w, h) < 640:
                continue
            if (ii.get("size") or 0) > 15_000_000:
                continue
            title = p.get("title", "").replace("File:", "")
            if title_denied(title):
                continue
            seen.add(cid)
            out.append({
                "id": cid, "path": ii["url"],
                "thumb": ii.get("thumburl") or ii["url"],
                "ext": ".png" if mime == "image/png" else ".jpg",
                "landing": ii.get("descriptionurl") or ii["url"], "src": "commons",
                "license": "wikimedia-free", "title": title, "q": kw,
                "w": w, "h": h, "size": ii.get("size") or 0, "colors": [],
            })
            added += 1
        if capped:
            break
        time.sleep(0.4)

def search_artic(kw, min_res, seen, out, pages=3, max_add=None):
    """https://api.artic.edu/docs/ - Art Institute of Chicago open access.
    Public-domain art, IIIF full-res (up to 4000px), no API key. Needs the AIC-User-Agent header."""
    added = 0
    for page in range(1, pages + 1):
        r = http_get(ART_API, headers=UA_ART, timeout=40, params={
            "q": kw, "page": page, "limit": 100,
            "fields": "id,title,image_id,artist_title,is_public_domain"})
        if not r:
            break
        try:
            j = r.json()
        except Exception:
            break
        rows = j.get("data", [])
        capped = False
        for a in rows:
            if max_add is not None and added >= max_add:
                capped = True
                break
            iid = a.get("image_id")
            if not iid or not a.get("is_public_domain"):
                continue                      # public domain only = fully legal
            aid = "ac_" + str(a.get("id"))
            if aid in seen:
                continue
            title = (a.get("title") or "").strip()
            if title_denied(title):
                continue
            seen.add(aid)
            out.append({
                "id": aid,
                "path": f"{ART_IIIF}/{iid}/full/4000,/0/default.jpg",
                "thumb": f"{ART_IIIF}/{iid}/full/300,/0/default.jpg",
                "ext": ".jpg",
                "landing": f"{ART_LAND}/{a.get('id')}",
                "src": "artic", "license": "public-domain",
                "title": title, "q": kw, "w": 0, "h": 0,
                "size": 0, "colors": [],
            })
            added += 1
        total = (j.get("pagination") or {}).get("total") or 0
        if capped or page * 100 >= total or not rows:
            break
        time.sleep(0.3)

def search_cma(kw, min_res, seen, out, pages=3, max_add=None):
    """https://github.com/clevelandart/api - Cleveland Museum of Art open access.
    CC0 (public domain), print-resolution JPGs, no API key."""
    added = 0
    for page in range(1, pages + 1):
        r = http_get(CMA_API, timeout=40, params={
            "q": kw, "has_image": 1, "cc0": 1, "limit": 50, "skip": (page - 1) * 50})
        if not r:
            break
        try:
            j = r.json()
        except Exception:
            break
        rows = j.get("data", [])
        capped = False
        for a in rows:
            if max_add is not None and added >= max_add:
                capped = True
                break
            imgs = a.get("images") or {}
            print_im = imgs.get("print") or {}
            web_im = imgs.get("web") or {}
            path = print_im.get("url") or web_im.get("url") or ""
            if not path:
                continue
            cid = "cma_" + str(a.get("id"))
            if cid in seen:
                continue
            w = _int(print_im.get("width"))
            h = _int(print_im.get("height"))
            if max(w, h) < min_res:
                continue
            title = (a.get("title") or "").strip()
            if title_denied(title):
                continue
            seen.add(cid)
            out.append({
                "id": cid, "path": path,
                "thumb": web_im.get("url") or path, "ext": ".jpg",
                "landing": f"https://www.clevelandart.org/artworks/"
                           f"{a.get('accession_number') or a.get('id')}",
                "src": "cma", "license": "cc0",
                "title": title, "q": kw, "w": w, "h": h,
                "size": 0, "colors": [],
            })
            added += 1
        total = (j.get("info") or {}).get("total") or 0
        if capped or page * 50 >= total or not rows:
            break
        time.sleep(0.3)

def search_nasa(kw, min_res, seen, out, pages=3, max_add=None):
    """https://images-api.nasa.gov/ - NASA image library, public domain, no API key.
    Space/earth/science photos - up to 4K (search metadata has no resolution,
    so min_res is checked against the actual pixels at save() time)."""
    added = 0
    for page in range(1, pages + 1):
        r = http_get(NASA_API, timeout=40,
                     params={"q": kw, "media_type": "image", "page": page})
        if not r:
            break
        try:
            j = r.json()
        except Exception:
            break
        rows = (j.get("collection") or {}).get("items", [])
        capped = False
        for it in rows:
            if max_add is not None and added >= max_add:
                capped = True
                break
            d = (it.get("data") or [{}])[0]
            nid = d.get("nasa_id")
            if not nid:
                continue
            wid = "nasa_" + nid
            if wid in seen:
                continue
            title = (d.get("title") or "").strip()
            if title_denied(title, ignore=("nasa",)):
                continue
            thumb = ((it.get("links") or [{}])[0].get("href") or "").replace("http://", "https://")
            if not thumb:
                continue
            seen.add(wid)
            out.append({
                "id": wid, "path": f"https://images-assets.nasa.gov/image/{nid}/{nid}~orig.jpg",
                "thumb": thumb, "ext": ".jpg",
                "landing": f"https://images.nasa.gov/details/{nid}",
                "src": "nasa", "license": "nasa-public-domain",
                "title": title, "q": kw, "w": 0, "h": 0,
                "size": 0, "colors": [],
            })
            added += 1
        if capped or not rows:
            break
        time.sleep(0.3)

# ---------------- Pinterest (free *trial* tier - needs a token) ----------------
_PIN = {"tok": "", "exp": 0.0}

def pin_load_cfg():
    try:
        with open(PIN_CFG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def pin_token():
    """Token cache -> otherwise fetch one via client_credentials (valid 30 days, needs 2FA)."""
    now = time.time()
    if _PIN["tok"] and now < _PIN["exp"] - 120:
        return _PIN["tok"]
    cfg = pin_load_cfg()
    tok = str(cfg.get("access_token") or "").strip()
    if tok and not cfg.get("client_id"):
        _PIN.update(tok=tok, exp=now + 86400)     # manually paste kiya - use as-is
        return tok
    if tok and float(cfg.get("expires_at") or 0) > now + 120:
        _PIN.update(tok=tok, exp=float(cfg["expires_at"]))
        return tok
    cid, csec = str(cfg.get("client_id") or ""), str(cfg.get("client_secret") or "")
    if not (cid and csec):
        return tok or ""
    try:
        r = requests.post(
            PIN_TOKEN, timeout=30,
            headers={"Authorization": "Basic " +
                     base64.b64encode(f"{cid}:{csec}".encode()).decode(),
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "scope": "boards:read pins:read"})
        if r.status_code == 200:
            j = r.json()
            t = str(j.get("access_token") or "")
            exp = now + float(j.get("expires_in") or 2592000)
            cfg.update(access_token=t, expires_at=exp)
            with open(PIN_CFG, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            _PIN.update(tok=t, exp=exp)
            return t
        print(f"[pinterest] token failed {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[pinterest] token error: {e}")
    return tok or ""

# -------- Pinterest: images from the PUBLIC FEED (no login, no API key) --------
# Official v5 search (`/search/partner/pins`) = "restricted feature: pin_search" +
# app "trial access pending" (code 3) - both only come from Pinterest support.
# But the public pages (/wallpapers, /nature, ...) emit pinimg URLs in a server-side
# <link rel="preload"> tag, and i.pinimg.com/originals/<hash> gives full res.
# Pin ID/title load client-side (not in the HTML) -> landing = the page URL,
# and the title/tag deny-lists (food/text/logo) do not apply on Pinterest.
PIN_FEEDS = [
    "https://www.pinterest.com/wallpapers/",
    "https://www.pinterest.com/nature/",
    "https://www.pinterest.com/photography/",
    "https://www.pinterest.com/travel/",
    "https://www.pinterest.com/design/",
    "https://www.pinterest.com/fashion/",
    "https://www.pinterest.com/tattoos/",
    "https://www.pinterest.com/ideas/",
    "https://www.pinterest.com/today/",
    "https://www.pinterest.com/explore/",
]
_PIN_RE = re.compile(r"i\.pinimg\.com/[^/\"\s]+/"
                     r"([0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{8,}\.[A-Za-z]{3,4})",
                     re.I)
# keyword -> which feed to try first (search is keyless, hence this ordering)
_PIN_HINTS = [
    ("wallpaper desktop background screen", ("wallpapers", "ideas", "explore")),
    ("flower tree mountain forest sky nature", ("nature", "travel", "ideas")),
    ("photography photo camera lens", ("photography", "ideas")),
    ("travel beach ocean sea lake", ("travel", "nature")),
    ("design abstract pattern art", ("design", "ideas")),
    ("fashion dress outfit style", ("fashion", "ideas")),
    ("tattoo ink", ("tattoos", "design")),
]
_PIN_API_BLOCKED = False          # stop retrying once a restricted/trial error appears

def _pin_feeds_for(kw):
    """Relevant feed for the keyword first, then all the others."""
    k = (kw or "").lower()
    slug2url = {u.rstrip("/").rsplit("/", 1)[-1]: u for u in PIN_FEEDS}
    ranked, used = [], set()
    for hay, order in _PIN_HINTS:
        if any(w in k for w in hay.split()):
            for s in order:
                if s in slug2url and s not in used:
                    used.add(s); ranked.append(slug2url[s])
    for u in PIN_FEEDS:
        if u not in used:
            used.add(u); ranked.append(u)
    return ranked

def _pin_from_feed(kw, seen, out, feeds, max_add, note=None):
    """Fetch each feed's HTML -> unique hashes -> /originals/ candidates."""
    got = 0
    for u in feeds:
        if max_add is not None and got >= max_add:
            break
        rr = http_get(u, timeout=35, tries=2, headers=UA)
        if not rr:
            continue
        try:
            hashes = list(dict.fromkeys(_PIN_RE.findall(rr.text.replace("\\/", "/"))))
        except Exception:
            continue
        if not hashes and note is not None:
            note(f"pinterest: nothing found on {u.rstrip('/').rsplit('/', 1)[-1]}")
        for hsh in hashes:
            if max_add is not None and got >= max_add:
                break
            pid = "pin_" + hashlib.sha1(("feed:" + hsh).encode()).hexdigest()[:18]
            if pid in seen:
                continue
            ext = (os.path.splitext(hsh)[1].lower() or ".jpg")
            if ext == ".jpeg":
                ext = ".jpg"
            seen.add(pid)
            out.append({
                "id": pid,
                "path": f"https://i.pinimg.com/originals/{hsh}",   # full-res
                "thumb": f"https://i.pinimg.com/1200x/{hsh}",      # for analysis
                "ext": ext,
                "landing": u,                    # pin ID is not in the HTML
                "src": "pinterest",
                "license": "pinterest-content-license",   # user-generated!
                "title": "", "q": kw, "w": 0, "h": 0,
                "size": 0, "colors": [],
            })
            got += 1
        time.sleep(0.4)
    return got

PIN_SCRAPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pin_home_feed.py")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

def _pin_urls_for(kw):
    """URL list for Playwright.
    #1 = keyword SEARCH (works without login - the official API is blocked!)
    #2 = ideas (sabse rich logged-out page)
    #3 = HOME (your real personalized home feed when logged in)
    then the relevant category feeds."""
    try:
        from urllib.parse import quote as _q
    except Exception:
        _q = lambda s: s.replace(" ", "%20")
    base = "https://www.pinterest.com"
    q = (kw or "").strip() or "wallpaper"
    urls = [
        f"{base}/search/pins/?q={_q(q)}",
        f"{base}/ideas/",
        f"{base}/",
    ]
    for u in _pin_feeds_for(kw):
        if u not in urls:
            urls.append(u)
    return urls

def _pin_home_feed(kw, seen, out, max_add, note=None, scroll=10, wlog=150,
                   login=False):
    """Pinterest via Playwright (browser render + infinite scroll).
    login=False -> --allow-anon: every public page works without a login
    login=True  -> headed browser, you log in (real personalized home feed)"""
    if not os.path.exists(PIN_SCRAPER):
        return 0
    urls = _pin_urls_for(kw)
    cmd = [sys.executable, PIN_SCRAPER, "--scroll", str(scroll), "--urls"] + urls
    if login:
        cmd += ["--wait-login", str(wlog)]          # headed -> login possible
    else:
        cmd += ["--headless", "--allow-anon"]       # never blocks waiting for the user
    try:
        rw = subprocess.run(cmd, capture_output=True, text=True, timeout=520,
                            creationflags=_NO_WINDOW,
                            encoding="utf-8", errors="replace")
    except Exception as e:
        if note is not None:
            note(f"pinterest playwright failed: {e}")
        return 0
    try:
        j = json.loads((rw.stdout or "").strip().splitlines()[-1])
    except Exception:
        j = {"error": "no-json", "detail": (rw.stderr or rw.stdout or "")[-300:]}
    if not isinstance(j, dict) or not j.get("ok"):
        if note is not None:
            note(f"pinterest playwright skip: {str(j.get('error'))[:40]} "
                 f"{str(j.get('detail',''))[:120]} -> static feed fallback")
        return 0
    if not j.get("logged_in") and note is not None:
        note("Pinterest: running on public pages. For your REAL home feed log in "
             "once:  python pin_home_feed.py  (optional)")

    added = 0
    for p in j.get("pins") or []:
        if max_add is not None and added >= max_add:
            break
        raw = str(p.get("id") or "")
        if not raw:
            continue
        hsh = p.get("hash")
        if not hsh:
            continue
        pid = "pin_" + raw
        if pid in seen:
            continue
        ext = os.path.splitext(hsh)[1].lower() or ".jpg"
        if ext == ".jpeg":
            ext = ".jpg"
        seen.add(pid)
        out.append({
            "id": pid,
            "path": p.get("path") or f"https://i.pinimg.com/originals/{hsh}",
            "thumb": p.get("thumb") or f"https://i.pinimg.com/1200x/{hsh}",
            "ext": ext,
            "landing": p.get("landing") or "https://www.pinterest.com/",
            "src": "pinterest",
            "license": "pinterest-content-license",
            "title": (p.get("alt") or "").strip(),
            "q": kw, "w": 0, "h": 0,
            "size": 0, "colors": [],
        })
        added += 1
    if note is not None:
        note(f"pinterest playwright: {len(j.get('pins') or [])} pins found, "
             f"{added} new candidates")
    return added

def search_pinterest(kw, min_res, colour, seen, out, pages=2, max_add=None, note=None):
    """Pinterest = Playwright (keyword search + ideas + home) -> static feeds -> API."""
    global _PIN_API_BLOCKED
    # 1) Playwright: keyword search + ideas + home (no login)
    added = 0
    scroll = 10 if not max_add else max(6, min(26, max_add // 5 + 6))
    try:
        added = _pin_home_feed(kw, seen, out, max_add, note=note, scroll=scroll)
    except Exception as e:
        if note is not None:
            note(f"pinterest playwright error: {e}")
    # 2) static public feeds (fallback / when the quota is not filled yet)
    room = None if max_add is None else max_add - added
    if room is None or room > 0:
        feeds = _pin_feeds_for(kw)
        added += _pin_from_feed(kw, seen, out,
                                feeds[:max(2, min(len(feeds), max(pages, 4)))],
                                room, note=note)
    # 3) official API (until it stops being blocked)
    if not _PIN_API_BLOCKED:
        room = None if max_add is None else max_add - added
        if room is None or room > 0:
            _search_pinterest_api(kw, min_res, colour, seen, out,
                                  pages=pages, max_add=room, note=note)
    return added

def _search_pinterest_api(kw, min_res, colour, seen, out, pages=2, max_add=None, note=None):
    """https://developers.pinterest.com/docs/api/v5/search_partner_pins/
    Restricted feature - apps do not get access. Licence: pinterest-content-license."""
    global _PIN_API_BLOCKED
    tok = pin_token()
    if not tok:
        return
    if max_add is not None and max_add <= 0:
        return
    added = 0
    hdrs = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    for page in range(1, pages + 1):
        params = {"term": kw, "country_code": "US", "limit": 50}
        if page > 1:
            params["bookmark"] = ""      # bookmark comes from the previous page; handled below
        r = http_get(PIN_SEARCH, headers=hdrs, timeout=40, params=params)
        if not r:
            _PIN_API_BLOCKED = True   # restricted feature / trial pending -> the feed still works
            if note is not None:
                note("pinterest official API blocked (restricted feature: pin_search) "
                     "-> fetching from the public feed instead")
            return
        try:
            j = r.json()
        except Exception:
            return
        rows = j.get("items") or []
        bm = j.get("bookmark")
        capped = False
        for p in rows:
            if max_add is not None and added >= max_add:
                capped = True
                break
            media = p.get("media") or {}
            imgs = media.get("images") or {}
            # pick the largest available size
            cand = None
            for k in ("original", "800x", "600x", "400x", "prefilled"):
                if imgs.get(k, {}).get("url"):
                    cand = imgs[k]
                    break
            if not cand and imgs:
                cand = sorted(imgs.values(),
                              key=lambda z: (z.get("width") or 0) * (z.get("height") or 0),
                              reverse=True)[0]
            url = (cand or {}).get("url") or ""
            if not url:
                continue
            pid = "pin_" + str(p.get("id") or hashlib.md5(url.encode()).hexdigest()[:16])
            if pid in seen:
                continue
            w, h = int(cand.get("width") or 0), int(cand.get("height") or 0)
            if w and max(w, h) < min_res:
                continue
            title = str(p.get("title") or p.get("alt_text") or "").strip()
            if title_denied(title):
                continue
            seen.add(pid)
            out.append({
                "id": pid, "path": url, "thumb": url,
                "ext": ".jpg" if ".jpg" in url.lower() or ".jpeg" in url.lower()
                      else (".png" if ".png" in url.lower() else ".jpg"),
                "landing": f"https://www.pinterest.com/pin/{(p.get('id') or '')}/"
                           if p.get("id") else (p.get("link") or url),
                "src": "pinterest", "license": "pinterest-content-license",
                "title": title, "q": kw, "w": w, "h": h,
                "size": 0, "colors": [],
            })
            added += 1
        if capped or not bm or not rows:
            break
        params["bookmark"] = bm
        time.sleep(0.5)
    time.sleep(0.4)

# ---------------- job engine ----------------
JOBS = {}
JOB_QUEUE = threading.Lock()      # one job at a time -> the APIs never 429

class Job:
    def __init__(self, opts):
        self.id = uuid.uuid4().hex[:10]
        self.opts = opts
        self.state = "queued"
        self.log = deque(maxlen=60)
        self.counts = {"searched": 0, "analysed": 0, "matched": 0, "saved": 0,
                       "target": opts["count"]}
        self.files = []
        self.folder = ""
        self.error = ""
        self.started = time.time()

    def say(self, msg):
        self.log.append(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def status(self):
        return {"job": self.id, "state": self.state, "counts": self.counts,
                "log": list(self.log), "files": self.files[-240:], "folder": self.folder,
                "error": self.error, "colour": self.opts.get("colour"),
                "source": self.opts.get("source"),
                "elapsed": round(time.time() - self.started, 1)}

def slugify(s):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return (s[:60] or "wallpapers")

def analyse_items(job, items, colour, style, relaxed=False, cap=None, min_res=0,
                  orient="landscape"):
    """Thumb download + pixel analysis -> sorted matched list."""
    def work(it):
        rr = dl_first(alt_urls(it["thumb"]), timeout=25, tries=1,
                      headers=src_headers(it["src"]))
        if not rr:
            return None            # openverse 424 thumb = broken origin, skip (keeps it fast)
        try:
            m = analyze_bytes(rr.content)
        except Exception:
            return None
        return (it, m)

    ok = []
    checked = 0
    todo = items if cap is None else items[:cap]
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = [ex.submit(work, it) for it in todo]
        for f in as_completed(futs):
            res = f.result()
            checked += 1
            job.counts["analysed"] += 1
            if not res:
                continue
            it, m = res
            if metrics_ok(m, colour, style, relaxed, orient):
                it["_m"] = m
                it["_score"] = round(score_of(m, colour, style) + res_bonus(it), 2)
                ok.append(it)
            if checked % 100 == 0:
                job.say(f"analysed {checked}/{len(todo)}, matched {len(ok)}")
    ok.sort(key=lambda x: -x["_score"])
    return ok

def run_job(job):
    o = job.opts
    colour = o.get("colour") or "any"
    if colour not in ALL_COLOURS:
        colour = "any"
    style = o.get("style") or "soft"
    if style not in ("soft", "vivid", "any"):
        style = "soft"
    orient = o.get("orientation") or "landscape"   # wallpaper = chauri tasveer
    if orient not in ("landscape", "portrait", "any"):
        orient = "landscape"
    # contradictory combos -> auto-fix
    if colour == "black" and style == "soft":
        style = "any"
        job.say("black + soft contradictory -> style=any")
    if colour == "white" and style == "vivid":
        style = "any"
        job.say("white + vivid contradictory -> style=any")
    target = int(o["count"])
    min_res = int(o["min_res"])
    smart = bool(o.get("smart", True))   # default ON - do not crash on a missing key
    cat = "100" if o.get("category") == "general" else "111"
    source = o.get("source") or "wallhaven"
    if source not in ALL_SOURCES and source != "all":
        source = "wallhaven"
    colourfn = None if colour == "any" else colour
    words = [w.strip() for w in re.split(r"[,\n]+", o["keywords"]) if w.strip()]
    if not words:
        job.error = "Keywords khaali hain"
        job.state = "error"
        return

    folder_name = o.get("folder", "").strip() or slugify("-".join(words))
    folder = os.path.join(ROOT, folder_name)
    i = 2
    while os.path.exists(folder) and o.get("newfolder"):
        folder = os.path.join(ROOT, f"{folder_name}-{i}")
        i += 1
    os.makedirs(folder, exist_ok=True)
    job.folder = folder

    seen_ids, hashes = set(), set()
    saved_ids = set()               # what got saved (excluded during the relaxed re-check)
    mf = os.path.join(folder, "manifest.csv")
    old_rows = []
    if os.path.exists(mf):
        with open(mf, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                old_rows.append(r)
                if r.get("id"):
                    seen_ids.add(r["id"])
                # visual dedupe: seed the ahashes of previous runs, otherwise filling
                # one folder repeatedly brings the same image back (from another source/id)
                if r.get("ahash"):
                    try:
                        hashes.add(int(r["ahash"], 16))
                    except Exception:
                        pass
    if old_rows:
        job.say(f"Existing folder: {len(old_rows)} images -> their IDs + hashes will be skipped "
                f"(new downloads only, nothing is overwritten)")

    srcs = list(ALL_SOURCES) if source == "all" else [source]
    job.state = "searching"
    job.say(f"Searching: {', '.join(words)}  [{colour} / {style}] sources={'+'.join(srcs)}")
    if "pinterest" in srcs:
        if orient == "landscape":
            job.say("NOTE: Pinterest is ~90% PORTRAIT (phone wallpapers) "
                    "-> its yield in landscape mode is close to 0.")
        elif orient == "any":
            job.say("Pinterest: portrait (phone) wallpapers dominate the results.")
    # Big target + landscape = very few matches (sources hold mostly portrait art/photos)
    if target >= 300 and orient == "landscape":
        job.say(f"HEADS-UP: target={target} + Orientation=Landscape. Museum art "
                "(ARTIC/CMA) and Pinterest are mostly PORTRAIT -> most of their "
                "results will be rejected. For a higher yield pick Orientation=Any "
                "(or Portrait, if you want phone wallpapers).")

    min_pool = max(target * 5, 180)
    # fair-share quota per source - otherwise Wallhaven fills the whole pool
    # and the other sources never get used
    per_src = max(min_pool // len(srcs), 40)

    # ---- min_res during search: keep it EXACT, only relax it for high-res ----
    # (a) up to 2560 the sources fill their quota at the exact min_res anyway (A/B test:
    #     at 1920 wallhaven/commons/cma/nasa/artic all return 500) - relaxing would let
    #     1024-1920 px candidates through, only for save() to reject them = wasted time.
    # (b) at 4K (3840) the sources return 0 immediately (test: cma=0, wallhaven=16/87),
    #     so relaxing there is what grows the pool. save() still enforces the exact min_res.
    if min_res > 2560:
        search_min = max(1920, min_res // 2)
        job.say(f"High-res {min_res}px -> the search phase uses {search_min}px "
                f"(exactly {min_res}px is enforced in save(), otherwise some sources return 0)")
    else:
        search_min = min_res

    # ---- keyword expansion (only for big targets) ----
    # "sunflower field" -> "sunflower field", "sunflower", "field"
    # Only for big counts, so small runs do not waste time.
    terms = list(words)
    if target >= 300:
        for w in list(words):
            for p in re.split(r"\s+", w.strip()):
                p = p.lower()
                if len(p) > 2 and p not in terms:
                    terms.append(p)
        # common synonyms / broader forms
        EXTRA = {"arts": ["art", "artistic"], "photos": ["photo", "photography"],
                 "pictures": ["picture", "photo"], "wallpapers": ["wallpaper"],
                 "images": ["image"], "philosophical": ["philosophy"],
                 "crazy": ["insane", "wild"], "whimsy": ["whimsical"]}
        for w in list(terms):
            for syn in EXTRA.get(w, []):
                if syn not in terms:
                    terms.append(syn)
        if len(terms) != len(words):
            job.say(f"Keywords expand: {', '.join(terms)}")

    cands = []
    resume = {}                    # word -> next wallhaven page (phase-5 continuation)
    # search time budget - otherwise a big target (7000) would run for hours
    # without the user realising. Whatever was found by then gets analysed.
    search_deadline = time.time() + min(900, 120 + int(target) // 5)

    def have(s):
        return sum(1 for c in cands if c["src"] == s)

    # ---- TWO-PASS search ----
    # FIRST: fast sources (wallhaven/commons/artic/cma/nasa) for every term.
    # LATER: slow sources (pinterest = ~6 min browser, openverse = rate-limited).
    # Otherwise Pinterest eats the whole time budget on term 1 and keyword
    # expansion (11 terms) never runs - only the plain phrase search happens
    # tha jisme wallhaven/commons/cma/nasa = 0 aate the.
    fast_srcs = [s for s in ("wallhaven", "commons", "artic", "cma", "nasa") if s in srcs]
    slow_srcs = [s for s in ("pinterest", "openverse") if s in srcs]

    def search_fast(w):
        if "wallhaven" in fast_srcs and have("wallhaven") < per_src:
            room = per_src - have("wallhaven")
            nxt = search_wallhaven(w, cat, search_min, colourfn, smart,
                                   max(2, -(-room // 24)), seen_ids, cands,
                                   max_add=room)
            if nxt and w in words:
                resume[w] = nxt
            job.counts["searched"] = len(cands)
        if "commons" in fast_srcs and have("commons") < per_src:
            room = per_src - have("commons")
            search_commons(w, search_min, None, seen_ids, cands,     # colour -> pixel analysis
                           pages=max(2, -(-room // 45)), max_add=room)
            job.counts["searched"] = len(cands)
        if "artic" in fast_srcs and have("artic") < per_src:
            room = per_src - have("artic")
            search_artic(w, search_min, seen_ids, cands,
                         pages=max(2, -(-room // 45)), max_add=room)
            job.counts["searched"] = len(cands)
        if "cma" in fast_srcs and have("cma") < per_src:
            room = per_src - have("cma")
            search_cma(w, search_min, seen_ids, cands,
                       pages=max(2, -(-room // 45)), max_add=room)
            job.counts["searched"] = len(cands)
        if "nasa" in fast_srcs and have("nasa") < per_src:
            room = per_src - have("nasa")
            search_nasa(w, search_min, seen_ids, cands,
                        pages=max(2, -(-room // 45)), max_add=room)
            job.counts["searched"] = len(cands)

    def search_slow(w):
        if "pinterest" in slow_srcs and have("pinterest") < per_src:
            room = per_src - have("pinterest")
            search_pinterest(w, search_min, None, seen_ids, cands,
                             pages=max(2, -(-room // 45)), max_add=room,
                             note=job.say)
            job.counts["searched"] = len(cands)
        # openverse LAST + page cap. It is rate limited (20/min, 200/day), so
        # big page counts take hours. Max 2 pages per term - take what you can;
        # the fast sources already filled the rest of the budget.
        if "openverse" in slow_srcs and have("openverse") < per_src:
            room = per_src - have("openverse")
            search_openverse(w, search_min, None, seen_ids, cands,
                             pages=2, max_add=min(room, 60))
            job.counts["searched"] = len(cands)

    def pool_full():
        return all(have(s) >= per_src for s in srcs)

    def budget_over():
        if target >= 300 and len(cands) >= min_pool:
            job.say(f"Pool full: {len(cands)} candidates -> starting analysis")
            return True
        if time.time() >= search_deadline:
            job.say(f"Search budget exhausted -> analysing {len(cands)} candidates "
                    f"(target {target})")
            return True
        return False

    # PASS 1: fast sources - all terms (everything comes back within ~30s)
    for w in terms:
        search_fast(w)
        if pool_full() or budget_over():
            break
    # PASS 2: slow sources (pinterest browser + openverse rate limit) - only
    # when there are still not enough candidates.
    if have("pinterest") < per_src or have("openverse") < per_src:
        for w in terms:
            search_slow(w)
            if pool_full() or budget_over():
                break
    by_src = {}
    for c in cands:
        by_src[c["src"]] = by_src.get(c["src"], 0) + 1
    job.say(f"Candidates: {len(cands)}  ({', '.join(f'{k}={v}' for k, v in sorted(by_src.items()))})")
    if not cands:
        job.error = "No candidates found - try different keywords (or a different source)"
        job.state = "error"
        return

    # ---------- phase 2: pixel analysis ----------
    job.state = "analysing"
    ok = analyse_items(job, cands, colour, style, min_res=min_res, orient=orient)
    job.counts["matched"] = len(ok)
    job.say(f"Matched {len(ok)} (target {target})")
    if not ok:
        job.error = "Nothing matched the color/style filters - try another style or colour"
        job.state = "error"
        return

    # ---------- phase 3: smart tag filter (wallhaven tags + title deny-list) ----------
    top = ok[:min(len(ok), target * 3)]
    if smart:
        wh_top = [it for it in top if it["src"] == "wallhaven"]
        if wh_top:
            job.say(f"Checking tags of {len(wh_top)} wallhaven candidates...")
            tags = {}
            for n, it in enumerate(wh_top):
                rr = http_get(f"{WH_W}/{it['id'][3:]}", timeout=25)
                if rr:
                    try:
                        tags[it["id"]] = [t["name"].lower()
                                          for t in rr.json().get("data", {}).get("tags", [])]
                    except Exception:
                        pass
                time.sleep(0.2)
                if n and n % 50 == 0:
                    job.say(f"  tags {n}/{len(wh_top)}")
            for it in wh_top:
                it["_tags"] = tags.get(it["id"], [])
        banned = 0
        keep = []
        for it in top:
            denied = title_denied(it.get("title"))
            if not denied and it["src"] == "wallhaven" and it.get("_tags"):
                denied = any(any(rx.search(t) for rx in DENY_RE) for t in it["_tags"])
            if it["src"] == "openverse" and not denied:
                denied = title_denied(it.get("landing", "")) or title_denied(it.get("license", ""))
            if denied:
                banned += 1
                continue
            keep.append(it)
        rest = [it for it in ok if it["id"] not in {x["id"] for x in top}]
        ok = keep + rest
        job.say(f"Removed {banned} text/logo/food/brand/title matches")

    # ---------- phase 4: download ----------
    job.state = "downloading"
    manifest = list(old_rows)
    seq = len(old_rows)
    lock = threading.Lock()
    failed_dl = set()              # ids whose download failed (retried in phase 5)

    def save(it, relaxed=True):
        nonlocal seq
        if job.counts["saved"] >= target:
            return None
        rr = dl_first(alt_urls(it["path"]), timeout=90, tries=3,
                      headers=src_headers(it["src"]))
        if not rr:
            with lock:
                failed_dl.add(it["id"])
            return None
        try:
            m = analyze_bytes(rr.content)
        except Exception:
            return None
        if max(m["w"], m["h"]) < min_res:
            return None
        if not metrics_ok(m, colour, style, relaxed, orient):
            return None
        try:
            img = Image.open(io.BytesIO(rr.content))
            h = ahash(img)
        except Exception:
            return None
        with lock:
            if job.counts["saved"] >= target:
                return None
            for hh in hashes:
                if (hh ^ h).bit_count() <= 6:
                    return None
            # resolution bonus from actual pixels (ARTIC/NASA do not report resolution)
            sc = round(it["_score"] - res_bonus(it)
                       + res_bonus({"w": m["w"], "h": m["h"]}), 2)
            seq += 1
            base = slugify(it["q"]) if len(words) > 1 else "wp"
            name = f"{base}-{seq:04d}{it['ext']}"
            path = os.path.join(folder, name)
            with open(path, "wb") as f:
                f.write(rr.content)
            hashes.add(h)
            failed_dl.discard(it["id"])
            saved_ids.add(it["id"])
            row = {"file": name, "resolution": f"{m['w']}x{m['h']}", "brightness_L": m["L"],
                   "hue": m["hue"], "tint": round(match_frac(m, colour) * 100, 1)
                   if colour in COLOUR_RANGES else round(m["chrom"] * 100, 1),
                   "complexity_edge": m["edge"], "dark_frac": m["dark"], "saturation": m["sat"],
                   "score": sc, "keywords": it["q"], "source": it["src"],
                   "license": it.get("license", ""), "landing": it["landing"], "id": it["id"],
                   # ahash -> visual cross-run dedupe when the same folder is filled again
                   "ahash": format(h, "x")}
            manifest.append(row)
            job.files.append({"name": name, "url": f"/img/{job.id}/{name}",
                              "res": f"{m['w']}x{m['h']}", "score": sc,
                              "src": it["src"], "license": it.get("license", "")})
            job.counts["saved"] = len(job.files)
        return True

    with ThreadPoolExecutor(max_workers=8) as ex:
        for f in as_completed([ex.submit(save, it) for it in ok[:target * 3]]):
            f.result()
            if job.counts["saved"] >= target:
                break
            if job.counts["saved"] and job.counts["saved"] % 10 == 0:
                job.say(f"saved {job.counts['saved']}/{target}")

    # ---------- phase 5: shortfall? relaxed wave (resume + failed-download retry) ----------
    if job.counts["saved"] < target:
        # (a) retry the downloads that failed earlier (429 / timeout) first
        with lock:
            retry = [it for it in ok if it["id"] in failed_dl]
        if retry:
            job.say(f"Retrying {len(retry)} failed downloads...")
            with ThreadPoolExecutor(max_workers=6) as exr:
                for f in as_completed([exr.submit(save, it) for it in retry]):
                    f.result()
                    if job.counts["saved"] >= target:
                        break
    if job.counts["saved"] < target:
        job.say(f"Shortfall ({job.counts['saved']}/{target}) -> relaxed round...")
        # (b1) old candidates rejected by the strict gate - re-check them under relaxed rules
        ok_ids = {it["id"] for it in ok} | saved_ids
        old_rej = [it for it in cands if it["id"] not in ok_ids]
        # keyword broadening: "sunflower field" -> also try "sunflower", "field"
        terms = []
        for w in words:
            terms.append(w)
            parts = w.split()
            if len(parts) > 1:
                terms.extend(parts)
        extra = []
        # fair-share quota (same as phase 1) - otherwise one source eats everything
        per2 = max(target, 30)

        def have2(s, bag=None):
            b = extra if bag is None else bag
            return sum(1 for c in b if c["src"] == s)

        # the relaxed search gets a budget too - otherwise 7000 targets run for hours
        r_deadline = time.time() + min(600, 90 + int(target) // 6)

        def r_over():
            if time.time() >= r_deadline:
                job.say(f"Relaxed search budget exhausted -> {len(extra)} new candidates")
                return True
            return all(have2(s) >= per2 for s in srcs)

        # PASS 1: fast sources first (all terms) - pinterest takes ~6 min,
        # otherwise it used up the budget on term 1.
        for w in terms:
            if "wallhaven" in srcs and have2("wallhaven") < per2:
                sp = resume.get(w, 1)
                nxt = search_wallhaven(w, cat, search_min, None, smart,
                                       max(2, -(-(per2 - have2("wallhaven")) // 24)),
                                       seen_ids, extra, pacing=0.25, start_page=sp,
                                       max_add=per2 - have2("wallhaven"))
                if nxt:
                    resume[w] = nxt
            if "commons" in srcs and have2("commons") < per2:
                search_commons(w, search_min, None, seen_ids, extra,
                               pages=max(2, -(-(per2 - have2("commons")) // 45)),
                               max_add=per2 - have2("commons"))
            if "artic" in srcs and have2("artic") < per2:
                search_artic(w, search_min, seen_ids, extra,
                             pages=max(2, -(-(per2 - have2("artic")) // 45)),
                             max_add=per2 - have2("artic"))
            if "cma" in srcs and have2("cma") < per2:
                search_cma(w, search_min, seen_ids, extra,
                           pages=max(2, -(-(per2 - have2("cma")) // 45)),
                           max_add=per2 - have2("cma"))
            if "nasa" in srcs and have2("nasa") < per2:
                search_nasa(w, search_min, seen_ids, extra,
                            pages=max(2, -(-(per2 - have2("nasa")) // 45)),
                            max_add=per2 - have2("nasa"))
            if r_over():
                break
        # PASS 2: slow sources (pinterest browser + openverse rate-limit)
        if (have2("pinterest") < per2 or have2("openverse") < per2) and not r_over():
            for w in terms:
                if "pinterest" in srcs and have2("pinterest") < per2:
                    search_pinterest(w, search_min, None, seen_ids, extra,
                                     pages=max(2, -(-(per2 - have2("pinterest")) // 45)),
                                     max_add=per2 - have2("pinterest"), note=job.say)
                if "openverse" in srcs and have2("openverse") < per2:
                    search_openverse(w, search_min, None, seen_ids, extra,
                                     pages=2, max_add=min(per2 - have2("openverse"), 60))
                if r_over():
                    break
        job.say(f"relaxed: {len(old_rej)} previous rejects + {len(extra)} new candidates")
        ok2 = analyse_items(job, old_rej[:target * 4] + extra, colour, style,
                            relaxed=True, cap=target * 20, min_res=min_res,
                            orient=orient)
        job.say(f"relaxed matched: {len(ok2)}")
        with ThreadPoolExecutor(max_workers=8) as ex2:
            for f in as_completed([ex2.submit(save, it) for it in ok2]):
                f.result()
                if job.counts["saved"] >= target:
                    break
    if job.counts["saved"] < target:
        job.say(f"Note: only {job.counts['saved']}/{target} found - try different keywords / colour / source")

    if manifest:
        # fieldnames = UNION of every row. Older rows have no 'ahash', newer ones
        # do -> taking only manifest[0].keys() would make DictWriter crash.
        fields = list(manifest[0].keys())
        for row in manifest[1:]:
            for k in row:
                if k not in fields:
                    fields.append(k)
        with open(mf, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(manifest)
    job.say(f"DONE -> {folder}  ({job.counts['saved']} wallpapers)")
    job.state = "done"

# ---------------- web app ----------------
app = Flask(__name__)
PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wallpaper Fetcher</title>
<style>
:root{
  --bg:#0a0c10; --card:#12161c; --card2:#171c23; --in:#0c0f14;
  --line:#212932; --line2:#2b343f;
  --txt:#eef1f6; --txt2:#b8c1cd; --mut:#8a94a2;
  --acc:#ffd84d; --acc2:#ffb020; --ok:#4ade80; --warn:#fbbf24; --err:#f87171;
}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;
  background:
    radial-gradient(1200px 560px at 88% -10%, rgba(255,216,77,.10), transparent 62%),
    radial-gradient(900px 480px at -10% 8%, rgba(120,150,255,.07), transparent 62%),
    var(--bg);
  color:var(--txt);font:15.5px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,Roboto,sans-serif;
  -webkit-font-smoothing:antialiased}
code{font-family:Consolas,'Cascadia Code',monospace;background:#232a33;padding:1px 5px;border-radius:5px;font-size:.9em}

/* ---------- top ---------- */
.top{position:sticky;top:0;z-index:40;backdrop-filter:blur(16px);
  background:rgba(10,12,16,.8);border-bottom:1px solid var(--line)}
.top .in{max-width:1500px;margin:0 auto;padding:12px 22px;display:flex;align-items:center;gap:13px}
.logo{width:34px;height:34px;border-radius:10px;display:grid;place-items:center;font-size:18px;
  background:linear-gradient(140deg,var(--acc),var(--acc2));box-shadow:0 4px 18px rgba(255,216,77,.28)}
.brand{font-size:17px;font-weight:700;letter-spacing:-.3px}
.brand span{background:linear-gradient(100deg,var(--acc),var(--acc2));
  -webkit-background-clip:text;background-clip:text;color:transparent}
.pill{font-size:12px;color:var(--txt2);background:var(--card2);border:1px solid var(--line);
  padding:4px 11px;border-radius:99px;white-space:nowrap}
.sf{flex:1}
.ghost{background:var(--card2);border:1px solid var(--line2);color:var(--txt);font-weight:600;
  padding:8px 14px;border-radius:9px;cursor:pointer;font-size:13.5px;transition:.14s;font-family:inherit}
.ghost:hover{background:#232b35;border-color:#3c4654}
.ghost.sm{padding:5px 11px;font-size:12.5px;font-weight:550}

/* ---------- layout ---------- */
.layout{max-width:1500px;margin:0 auto;padding:18px 22px 56px;
  display:grid;grid-template-columns:366px minmax(0,1fr);gap:16px;align-items:start}
@media(max-width:1020px){.layout{grid-template-columns:1fr}}
.panel{background:var(--card);border:1px solid var(--line);border-radius:16px}
.ctrl{position:sticky;top:62px;padding:17px}
.stage{overflow:hidden}

h2{font-size:10.5px;letter-spacing:1px;text-transform:uppercase;color:var(--mut);margin:0 0 7px;font-weight:700}
.grp{margin-bottom:15px}
.grp:last-child{margin-bottom:0}
.row{display:grid;gap:11px}
.row2{grid-template-columns:1fr 1fr}

textarea,input[type=text],input[type=password],select{
  width:100%;background:var(--in);border:1px solid var(--line2);color:var(--txt);
  padding:10px 12px;border-radius:10px;font-size:14.5px;font-family:inherit;transition:.15s}
textarea{min-height:70px;resize:vertical;line-height:1.5}
select{cursor:pointer;appearance:none;
  background-image:linear-gradient(45deg,transparent 50%,#79838f 50%),linear-gradient(135deg,#79838f 50%,transparent 50%);
  background-position:calc(100% - 16px) 17px,calc(100% - 11px) 17px;background-size:5px 5px,5px 5px;background-repeat:no-repeat}
textarea:focus,input:focus,select:focus{outline:none;border-color:var(--acc);box-shadow:0 0 0 3px rgba(255,216,77,.14)}
::placeholder{color:#626c7a}

.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.chip{background:transparent;border:1px dashed #2f3844;color:var(--mut);font-size:12px;
  padding:3px 9px;border-radius:99px;cursor:pointer;font-family:inherit;transition:.13s}
.chip:hover{border-color:var(--acc);color:var(--acc);border-style:solid}

.dots{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.dot{width:24px;height:24px;border-radius:50%;border:2px solid rgba(255,255,255,.16);cursor:pointer;
  padding:0;transition:.13s;flex:none}
.dot:hover{transform:scale(1.18)}
.dot.on{border-color:#fff;box-shadow:0 0 0 3px rgba(255,216,77,.4);transform:scale(1.18)}
.hrow{display:flex;justify-content:space-between;align-items:baseline}
.dotName{font-size:12px;color:var(--acc);font-weight:700;letter-spacing:.3px}

.chk{display:flex;align-items:center;gap:8px;font-size:13.5px;color:var(--txt2);cursor:pointer;user-select:none}
.chk input{accent-color:var(--acc);width:15px;height:15px;cursor:pointer;margin:0}

.go{width:100%;margin-top:17px;background:linear-gradient(135deg,var(--acc),var(--acc2));border:0;color:#171300;
  font-weight:800;font-size:15.5px;padding:14px;border-radius:12px;cursor:pointer;font-family:inherit;
  box-shadow:0 6px 24px rgba(255,216,77,.24);transition:.15s;letter-spacing:.2px}
.go:hover{transform:translateY(-1px);box-shadow:0 10px 30px rgba(255,216,77,.34)}
.go:active{transform:translateY(0)}
.go:disabled{opacity:.4;cursor:not-allowed;transform:none;box-shadow:none}
.hint{text-align:center;font-size:12.5px;color:var(--mut);margin-top:9px}
#warn{display:block;color:var(--warn);margin-top:6px;font-size:12.5px;line-height:1.45}

/* ---------- hero (idle) ---------- */
.hero{position:relative}
.heroIn{padding:30px 30px 20px;max-width:760px}
.eyebrow{font-size:11.5px;letter-spacing:1.4px;text-transform:uppercase;color:var(--acc);
  font-weight:700;margin-bottom:11px}
h1{font-size:clamp(27px,3.4vw,40px);line-height:1.1;margin:0 0 12px;letter-spacing:-1.1px;font-weight:800}
.grad{background:linear-gradient(100deg,#fff 8%,var(--acc) 58%,var(--acc2));
  -webkit-background-clip:text;background-clip:text;color:transparent}
.lede{color:var(--txt2);font-size:15.5px;margin:0 0 15px;max-width:640px}
.masonry{padding:4px 30px 0;column-count:5;column-gap:9px}
@media(max-width:1300px){.masonry{column-count:4}}
@media(max-width:1020px){.masonry{column-count:3}.heroIn{padding:24px 20px 16px}.masonry{padding:4px 20px 0}}
.masonry img{width:100%;display:block;border-radius:11px;margin-bottom:9px;
  background:linear-gradient(135deg,#1b212a,#242c37);min-height:70px}
.fade{position:absolute;inset:auto 0 0 0;height:130px;pointer-events:none;
  background:linear-gradient(transparent,var(--card))}
.shim{padding:22px 30px;color:var(--mut);font-size:13.5px}

/* ---------- progress ---------- */
.prog{padding:15px 18px;border-bottom:1px solid var(--line);background:var(--card2)}
.steps{display:flex;gap:5px;align-items:center;margin-bottom:12px;flex-wrap:wrap}
.step{font-size:12px;color:var(--mut);background:var(--in);border:1px solid var(--line);
  padding:4px 11px;border-radius:99px;display:flex;align-items:center;gap:6px;transition:.2s}
.step i{width:6px;height:6px;border-radius:50%;background:#3d4653;display:block}
.step.on{color:#171300;background:var(--acc);border-color:var(--acc);font-weight:700}
.step.on i{background:#171300}
.step.done{color:var(--ok);border-color:#2a6242;background:#11241a}
.step.done i{background:var(--ok)}
.arw{color:#3d4653;font-size:12px}
.bar{height:7px;background:var(--in);border-radius:99px;overflow:hidden;border:1px solid var(--line)}
.bar i{display:block;height:100%;width:0;border-radius:99px;
  background:linear-gradient(90deg,var(--acc),var(--acc2));transition:width .5s}
.bar i.ok{background:linear-gradient(90deg,#34d399,#4ade80)}
.srow{display:flex;gap:16px;flex-wrap:wrap;margin-top:12px;font-size:13.5px;color:var(--mut);align-items:center}
.srow b{color:var(--txt);font-weight:700;font-variant-numeric:tabular-nums}
.srow b.hl{color:var(--acc);font-size:15px}
.path{font:12px Consolas,'Cascadia Code',monospace;color:var(--ok);word-break:break-all;
  display:flex;gap:9px;align-items:center;margin-top:10px}
.path span{flex:1;min-width:0}

/* ---------- log ---------- */
.logwrap{border-bottom:1px solid var(--line);padding:12px 18px}
.logHead{display:flex;align-items:center;gap:9px;margin-bottom:7px}
.logHead h2{margin:0;flex:1}
.log{font:11.8px/1.6 Consolas,'Cascadia Code',monospace;color:#9aa6b3;background:var(--in);
  border:1px solid var(--line);border-radius:10px;padding:9px 11px;height:128px;overflow:auto;
  white-space:pre-wrap;word-break:break-word}
.log:empty:after{content:"live progress will show up here…";color:#4e5765}
.log .err{color:var(--err)}
.log .ok{color:var(--ok)}

/* ---------- results ---------- */
.rhead{display:flex;align-items:center;gap:11px;padding:14px 18px;flex-wrap:wrap}
.rhead b{font-size:15px}
.cnt{font-size:12.5px;color:var(--acc);font-weight:700;background:rgba(255,216,77,.1);
  border:1px solid rgba(255,216,77,.24);padding:2px 9px;border-radius:99px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:11px;padding:0 18px 16px}
@media(max-width:640px){.grid{grid-template-columns:repeat(auto-fill,minmax(135px,1fr));gap:8px}}
.t{position:relative;background:#000;border:1px solid var(--line);border-radius:12px;overflow:hidden;
  cursor:pointer;aspect-ratio:16/10;transition:.16s;display:block}
.t img{width:100%;height:100%;object-fit:cover;display:block;transition:.3s}
.t:hover{border-color:var(--acc);transform:translateY(-2px);box-shadow:0 12px 28px rgba(0,0,0,.55)}
.t:hover img{transform:scale(1.06)}
.ov{position:absolute;inset:auto 0 0 0;padding:24px 10px 9px;
  background:linear-gradient(transparent,rgba(0,0,0,.92));opacity:0;transition:.16s}
.t:hover .ov{opacity:1}
.ov b{display:block;font-size:11.5px;color:#fff;font-weight:600;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.ov em{font-style:normal;font-size:10.5px;color:#c0c9d4}
.bg{position:absolute;top:7px;left:7px;background:rgba(0,0,0,.74);backdrop-filter:blur(6px);
  border:1px solid rgba(255,255,255,.16);border-radius:6px;padding:1px 6px;font-size:10px;
  color:#d5e4f7;font-weight:700}
.rfoot{padding:0 18px 16px;display:flex;justify-content:center}
.empty{padding:34px 18px;text-align:center;color:var(--mut);font-size:13.5px}

/* ---------- details ---------- */
details{border-top:1px solid var(--line);background:var(--card)}
summary{padding:12px 18px;cursor:pointer;font-size:13.5px;color:var(--txt2);list-style:none;
  display:flex;align-items:center;gap:8px;user-select:none}
summary::-webkit-details-marker{display:none}
summary:hover{color:var(--txt)}
summary:before{content:"▸";color:var(--mut);font-size:11px;transition:.15s;display:inline-block}
details[open] summary:before{transform:rotate(90deg)}
.dbody{padding:0 18px 15px;font-size:13.5px;color:var(--txt2);line-height:1.7}
.dbody p{margin:0 0 10px}.dbody p:last-child{margin:0}
.note{color:var(--warn)}
.dbody a{color:#9ecbff}

footer{max-width:1500px;margin:0 auto;padding:0 22px 38px;color:var(--mut);font-size:12.5px}
.ffin{border:1px solid var(--line);border-radius:16px;background:var(--card)}

#toast{position:fixed;bottom:22px;left:50%;transform:translate(-50%,150%);z-index:90;
  background:#1b2129;border:1px solid var(--line2);color:var(--txt);padding:11px 18px;border-radius:12px;
  font-size:13.5px;box-shadow:0 16px 44px rgba(0,0,0,.6);transition:transform .28s cubic-bezier(.2,.9,.3,1.4);
  max-width:min(580px,92vw)}
#toast.show{transform:translate(-50%,0)}
#toast.e{border-color:#6b2f2f;color:#ffb4b4}
.hidden{display:none!important}
.ctrl::-webkit-scrollbar,.log::-webkit-scrollbar{width:9px}
.ctrl::-webkit-scrollbar-thumb,.log::-webkit-scrollbar-thumb{background:#2b333d;border-radius:9px}
@media(max-width:1020px){.ctrl{position:static}}
</style></head><body>

<div class="top"><div class="in">
  <div class="logo">🖼️</div>
  <div class="brand">Wallpaper <span>Fetcher</span></div>
  <span class="pill">7 free sources · no API keys</span>
  <span class="sf"></span>
  <button class="ghost sm" onclick="document.getElementById('srcCard').open=true;
    document.getElementById('srcCard').scrollIntoView({behavior:'smooth'})">Sources</button>
</div></div>

<div class="layout">
<aside class="panel ctrl">

  <div class="grp">
    <h2>Keywords</h2>
    <textarea id="kw" placeholder="sunflower field, minimal pattern, ocean waves"></textarea>
    <div class="chips" id="chips">
      <button class="chip" data-k="sunflower field">sunflower field</button>
      <button class="chip" data-k="minimal pattern">minimal pattern</button>
      <button class="chip" data-k="ocean waves">ocean waves</button>
      <button class="chip" data-k="abstract 3d">abstract 3d</button>
      <button class="chip" data-k="mountain fog">mountain fog</button>
      <button class="chip" data-k="nebula">nebula</button>
      <button class="chip" data-k="forest light">forest light</button>
    </div>
  </div>

  <div class="grp">
    <h2 class="hrow">Color <span class="dotName" id="dotName">Any</span></h2>
    <div class="dots" id="swatches">
      <button class="dot on" data-c="any" title="Any" style="background:conic-gradient(#ff5f6d,#ffc371,#7ee8fa,#a78bfa,#ff5f6d)"></button>
      <button class="dot" data-c="yellow" title="Yellow" style="background:#ffd84d"></button>
      <button class="dot" data-c="gold" title="Gold" style="background:#e0a527"></button>
      <button class="dot" data-c="orange" title="Orange" style="background:#ff8c1a"></button>
      <button class="dot" data-c="red" title="Red" style="background:#e5484d"></button>
      <button class="dot" data-c="pink" title="Pink" style="background:#ff7ab6"></button>
      <button class="dot" data-c="purple" title="Purple" style="background:#a78bfa"></button>
      <button class="dot" data-c="blue" title="Blue" style="background:#4d9fff"></button>
      <button class="dot" data-c="teal" title="Teal" style="background:#2ec4b6"></button>
      <button class="dot" data-c="green" title="Green" style="background:#4caf50"></button>
      <button class="dot" data-c="white" title="White" style="background:#f5f5f0"></button>
      <button class="dot" data-c="grey" title="Grey" style="background:#9aa0a6"></button>
      <button class="dot" data-c="black" title="Black" style="background:#242424"></button>
    </div>
  </div>

  <div class="grp row row2">
    <div><h2>Style</h2><select id="style">
      <option value="soft">Light &amp; soft</option>
      <option value="vivid">Vivid</option>
      <option value="any">No filter</option></select></div>
    <div><h2>Orientation</h2><select id="orient">
      <option value="landscape">Landscape</option>
      <option value="any">Any</option>
      <option value="portrait">Portrait</option></select></div>
    <div><h2>Count</h2><input type="text" id="count" value="50" inputmode="numeric"></div>
    <div><h2>Min resolution</h2><select id="minres">
      <option value="1920">1920 px</option><option value="2560">2560 px</option>
      <option value="3840">3840 px</option><option value="1280">1280 px</option></select></div>
  </div>

  <div class="grp">
    <h2>Source</h2>
    <select id="source">
      <option value="all">All 7 sources</option>
      <option value="wallhaven">Wallhaven — best wallpapers</option>
      <option value="openverse">Openverse — CC-licensed</option>
      <option value="commons">Wikimedia Commons</option>
      <option value="artic">Art Institute of Chicago</option>
      <option value="cma">Cleveland Museum of Art</option>
      <option value="nasa">NASA Image Library</option>
      <option value="pinterest">Pinterest — no key needed</option>
    </select>
  </div>

  <div class="grp row row2">
    <div><h2>Category</h2><select id="cat">
      <option value="general">General</option><option value="all">General + Anime</option></select></div>
    <div style="display:flex;flex-direction:column;justify-content:flex-end;gap:9px;padding-top:20px">
      <label class="chk"><input type="checkbox" id="smart" checked> Smart filter</label>
      <label class="chk"><input type="checkbox" id="newfolder" checked> New folder</label>
    </div>
  </div>

  <div class="grp">
    <h2>Folder name <span style="text-transform:none;letter-spacing:0;color:#626c7a;font-weight:500">(optional)</span></h2>
    <input type="text" id="folder" placeholder="auto: generated from keywords">
  </div>

  <button class="go" id="go" onclick="start()">⚡ Fetch wallpapers</button>
  <div class="hint">or press <b>Ctrl + Enter</b><span id="warn"></span></div>
</aside>

<section class="panel stage">

  <!-- ================= IDLE ================= -->
  <div class="hero" id="idleBox">
    <div class="heroIn">
      <div class="eyebrow">7 free sources · no API keys</div>
      <h1 class="grad">Fetch the wallpapers<br>you actually want.</h1>
      <p class="lede">Type some keywords &rarr; the tool searches <b>7 free sources</b> &rarr;
        applies color / resolution / tag filters &rarr; saves everything into a new folder with a
        <code>manifest.csv</code> (source link + licence for every image).</p>
      <div class="chips">
        <span style="font-size:12.5px;color:var(--mut)">Enter keywords on the left, or click a chip above &rarr;</span>
      </div>
    </div>
    <div class="shim" id="shim">loading sample wallpapers…</div>
    <div class="masonry" id="samples"></div>
    <div class="fade"></div>
  </div>

  <!-- ================= RUNNING ================= -->
  <div id="runBox" class="hidden">
    <div class="prog">
      <div class="steps" id="steps">
        <span class="step" data-s="searching"><i></i>Search</span><span class="arw">→</span>
        <span class="step" data-s="analysing"><i></i>Analyse</span><span class="arw">→</span>
        <span class="step" data-s="downloading"><i></i>Download</span><span class="arw">→</span>
        <span class="step" data-s="done"><i></i>Done</span>
      </div>
      <div class="bar"><i id="fill"></i></div>
      <div class="srow">
        <span>searched <b id="c1">0</b></span>
        <span>matched <b id="c3">0</b></span>
        <span>saved <b id="c4" class="hl">0</b> / <b id="c5">0</b></span>
        <span style="margin-left:auto"><span id="phase">queued</span> · <span id="elapsed">0s</span>
          <span id="rate"></span></span>
      </div>
      <div class="path"><span id="folderPath"></span>
        <button class="ghost sm" onclick="openFolder()">📂 Open folder</button>
        <button class="ghost sm" id="stopBtn" onclick="stopPoll()">Stop</button>
      </div>
    </div>

    <div class="logwrap">
      <div class="logHead"><h2>Live log</h2>
        <button class="ghost sm" onclick="clearLog()">Clear</button></div>
      <div class="log" id="log"></div>
    </div>

    <div id="res">
      <div class="rhead"><b>Saved</b><span class="cnt" id="resTitle">0</span>
        <span style="flex:1"></span>
        <button class="ghost sm" onclick="location.reload()">New fetch</button></div>
      <div class="grid" id="grid"></div>
      <div class="empty hidden" id="empty">Nothing saved yet — thumbnails appear once the download phase starts</div>
      <div class="rfoot hidden" id="moreWrap">
        <button class="ghost" id="moreBtn" onclick="renderMore()">Show more</button></div>
    </div>
  </div>

  <details id="srcCard">
    <summary>⚙️ Pinterest settings <span style="color:var(--mut)">(optional — works without a key ✅)</span></summary>
    <div class="dbody">
      <div class="row row2" style="margin-bottom:11px">
        <div><h2>Client ID</h2><input type="text" id="pinId" placeholder="123456789012"></div>
        <div><h2>Client Secret</h2><input type="password" id="pinSec" placeholder="••••••••"></div>
        <div style="grid-column:1/-1"><h2>Or paste an access token</h2><input type="text" id="pinTok" placeholder="pinc... / pina..."></div>
      </div>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <button class="ghost sm" onclick="pinSave()">💾 Save + generate token</button>
        <span id="pinMsg" style="font-size:12.5px;color:var(--mut)"></span>
      </div>
      <p style="margin-top:12px"><b>Pinterest works without this card</b> — the tool drives its own
        Chromium (Playwright) instance and pulls images from the <b>real keyword search</b>
        (<code>/search/pins/?q=…</code>), <code>/ideas/</code>, the home feed and the public category
        feeds. <b>No login, no key required.</b></p>
      <p><b>Want your personalized feed?</b> Run <code>pinterest-session.bat</code> once
        (close Chrome first &rarr; press ENTER). From then on the real home feed and each pin's
        actual link/title are picked up automatically.</p>
      <p class="note">Without a login the title/tag filters (food / text / logo deny-lists) do not
        apply, because alt text comes back empty. Licence = pinterest-content-license
        (personal use only). Pinterest returns <b>~90% portrait</b> images &rarr; combining
        <b>landscape + Pinterest ≈ 0 results</b>.</p>
      <p>Searching through the official API is a <b>restricted feature</b> — apps simply do not get
        access (401 consumer type / 402 sandbox). This card only becomes useful once Pinterest
        approves the app.</p>
    </div>
  </details>

</section>
</div>

<footer><div class="ffin">
  <details>
    <summary>🔗 Sources — 7 free options (all keyless)</summary>
    <div class="dbody">
      <p>• <a href="https://wallhaven.cc/api-docs" target="_blank">Wallhaven API</a> — free wallpaper API<br>
         • <a href="https://api.openverse.org/" target="_blank">Openverse API</a> — CC-licensed images (WordPress)<br>
         • <a href="https://commons.wikimedia.org/w/api.php" target="_blank">Wikimedia Commons API</a> — free media (Wikipedia)<br>
         • <a href="https://api.artic.edu/docs/" target="_blank">Art Institute of Chicago API</a> — public-domain art, IIIF<br>
         • <a href="https://github.com/clevelandart/api" target="_blank">Cleveland Museum of Art API</a> — CC0 collection<br>
         • <a href="https://images.nasa.gov/" target="_blank">NASA Image Library API</a> — public-domain space imagery<br>
         • <b>Pinterest</b> — real keyword search driven through a <b>Playwright browser</b> (no login/key;
           the official <a href="https://developers.pinterest.com/docs/api/v5/search_partner_pins/" target="_blank">search API</a>
           is a restricted feature)</p>
      <p>Every wallpaper's original link and licence are written to <code>manifest.csv</code>.
         Quality ranking: 4K (+14) &gt; 2K (+9) &gt; 1080p (+4), landscape by default.</p>
    </div>
  </details>
</div></footer>

<div id="toast"></div>

<script>
const $=id=>document.getElementById(id);
let job=null,timer=null,colour='any',shown=0,rendered=new Set(),lastLogLen=0;
const MAXPAGE=150;
const CN={any:'Any',yellow:'Yellow',gold:'Gold',orange:'Orange',red:'Red',pink:'Pink',
  purple:'Purple',blue:'Blue',teal:'Teal',green:'Green',white:'White',grey:'Grey',black:'Black'};
let files=[];

let tT=null;
function toast(m,e){const t=$('toast');t.textContent=m;t.className=e?'e show':'show';
  clearTimeout(tT);tT=setTimeout(()=>t.classList.remove('show'),e?5200:2600);}
function clearLog(){$('log').innerHTML='';lastLogLen=0;}

/* ---------- sample wallpapers (keeps the idle screen alive) ---------- */
async function loadSamples(){
  try{
    const j=await(await fetch('/api/samples')).json();
    const sh=$('shim');
    if(!j.items||!j.items.length){ if(sh)sh.remove(); return; }
    $('samples').innerHTML=j.items.map(s=>
      `<img loading="lazy" src="${s.url}" alt="" onerror="this.remove()">`).join('');
    if(sh)sh.remove();
  }catch(e){const sh=$('shim'); if(sh)sh.remove();}
}
loadSamples();

/* ---------- keyword chips ---------- */
$('chips').addEventListener('click',e=>{
  const b=e.target.closest('.chip'); if(!b) return;
  const cur=$('kw').value.trim();
  $('kw').value = cur ? (cur.replace(/,\s*$/,'')+', '+b.dataset.k) : b.dataset.k;
  $('kw').focus(); save(); warnCheck();
});

/* ---------- color dots ---------- */
$('swatches').addEventListener('click',e=>{
  const b=e.target.closest('.dot'); if(!b) return;
  document.querySelectorAll('.dot').forEach(x=>x.classList.remove('on'));
  b.classList.add('on'); colour=b.dataset.c;
  $('dotName').textContent=CN[colour]||colour; save();
});

/* ---------- settings persistence ---------- */
function save(){try{localStorage.setItem('wf',JSON.stringify({
  kw:$('kw').value,colour,style:$('style').value,orient:$('orient').value,count:$('count').value,
  minres:$('minres').value,source:$('source').value,cat:$('cat').value,
  smart:$('smart').checked,newfolder:$('newfolder').checked,folder:$('folder').value}))}catch(e){}}
function load(){try{const d=JSON.parse(localStorage.getItem('wf')||'{}');
  if(d.kw)$('kw').value=d.kw;
  if(d.colour){colour=d.colour;
    document.querySelectorAll('.dot').forEach(x=>x.classList.toggle('on',x.dataset.c===colour));
    $('dotName').textContent=CN[colour]||colour;}
  ['style','orient','count','minres','source','cat','folder'].forEach(k=>{if(d[k]!==undefined&&d[k]!=='')$(k).value=d[k];});
  if(d.smart!==undefined)$('smart').checked=d.smart;
  if(d.newfolder!==undefined)$('newfolder').checked=d.newfolder;
}catch(e){} warnCheck();}
['kw','style','orient','count','minres','source','cat','folder','smart','newfolder']
  .forEach(k=>$(k).addEventListener('change',()=>{save();warnCheck();}));
$('kw').addEventListener('input',()=>{save();warnCheck();});

/* ---------- live warnings for combinations that yield almost nothing ---------- */
function warnCheck(){
  const n=parseInt($('count').value)||0,o=$('orient').value,s=$('source').value;let w=[];
  if(n>=300&&o==='landscape')w.push('Landscape + a high count → portrait sources (Pinterest, museum art) will barely contribute.');
  if(n>=1000&&o!=='any')w.push('For 1000+ results, Orientation "Any" works best.');
  if(n>=500&&s==='openverse')w.push('Openverse anonymous quota is 200/day — you will not get 500+ from it.');
  if(o==='landscape'&&s==='pinterest')w.push('Pinterest returns ~90% portrait → landscape yields ~0 results.');
  $('warn').textContent=w.join(' ');
}

const ORDER=['queued','searching','analysing','downloading','done'];
function setSteps(state){let idx=ORDER.indexOf(state);if(idx<0)idx=0;
  if(state==='error')idx=1;
  document.querySelectorAll('#steps .step').forEach(el=>{const s=ORDER.indexOf(el.dataset.s);
    el.classList.toggle('on',s===idx&&state!=='done');
    el.classList.toggle('done',s<idx||state==='done');});}

async function start(){
  const kw=$('kw').value.trim();
  if(!kw){toast('Enter some keywords first',true);$('kw').focus();return;}
  const body={keywords:kw,count:parseInt($('count').value)||50,colour,style:$('style').value,
    source:$('source').value,orientation:$('orient').value,min_res:parseInt($('minres').value),
    category:$('cat').value,smart:$('smart').checked,newfolder:$('newfolder').checked,folder:$('folder').value};
  save();
  $('go').disabled=true;
  $('idleBox').classList.add('hidden');
  $('runBox').classList.remove('hidden');
  window.scrollTo({top:0,behavior:'smooth'});
  files=[];rendered=new Set();shown=0;lastLogLen=0;
  $('grid').innerHTML='';$('log').innerHTML='';$('moreWrap').classList.add('hidden');
  $('empty').classList.remove('hidden');$('resTitle').textContent='0';
  $('fill').classList.remove('ok');$('fill').style.width='0%';
  $('stopBtn').textContent='Stop';setSteps('queued');
  const r=await fetch('/api/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json();
  if(j.error){toast(j.error,true);$('go').disabled=false;return;}
  job=j.job;timer=setInterval(poll,1000);poll();
}

async function poll(){
  if(!job)return;let s;
  try{const r=await fetch('/api/status?job='+job);s=await r.json();}catch(e){return;}
  setSteps(s.state);
  $('phase').textContent=s.state;
  const c=s.counts,tgt=Math.max(c.target,1);
  $('fill').style.width=Math.min(100,100*c.saved/tgt)+'%';
  $('c1').textContent=c.searched;$('c3').textContent=c.matched;
  $('c4').textContent=c.saved;$('c5').textContent=c.target;
  $('elapsed').textContent=s.elapsed+'s';
  const mins=s.elapsed/60;
  $('rate').textContent=(s.state==='downloading'&&mins>0.3)?' · '+(c.saved/mins).toFixed(0)+' img/min':'';
  $('folderPath').textContent=s.folder||'';$('folderPath').title=s.folder||'';

  const L=s.log||[];
  if(L.length!==lastLogLen){
    const lg=$('log');
    if(L.length<lastLogLen){lg.innerHTML='';lastLogLen=0;}
    const near=lg.scrollHeight-lg.scrollTop-lg.clientHeight<40;
    for(let i=lastLogLen;i<L.length;i++){const d=document.createElement('div');
      d.className=/error|fail|skip|removed/i.test(L[i])?'err':/done|saved|match/i.test(L[i])?'ok':'';
      d.textContent=L[i];lg.appendChild(d);}
    lastLogLen=L.length;
    while(lg.children.length>400)lg.removeChild(lg.firstChild);
    if(near)lg.scrollTop=lg.scrollHeight;
  }

  if(s.files&&s.files.length){let got=false;
    for(const f of s.files){if(!rendered.has(f.name)){rendered.add(f.name);files.push(f);got=true;}}
    if(got){$('resTitle').textContent=files.length;$('empty').classList.add('hidden');renderMore();}}

  if(s.state==='done'||s.state==='error'){
    clearInterval(timer);timer=null;$('go').disabled=false;
    if(s.state==='error')toast('Error: '+s.error,true);
    else{$('fill').style.width='100%';$('fill').classList.add('ok');
      toast('Done — '+c.saved+' wallpapers saved · '+(s.folder||''));}
  }
}

function renderMore(){
  const g=$('grid'),end=Math.min(files.length,shown+MAXPAGE);let html='';
  for(let i=shown;i<end;i++){const f=files[i];
    html+=`<a class="t" href="${f.url}" target="_blank" title="${f.name}">
      <img loading="lazy" src="${f.url}" alt="">
      <span class="bg">${f.src||''}</span>
      <span class="ov"><b>${f.name}</b><em>${f.res||''} · score ${f.score??''} · ${f.license||''}</em></span></a>`;}
  g.insertAdjacentHTML('beforeend',html);shown=end;
  $('moreWrap').classList.toggle('hidden',shown>=files.length);
  if(files.length>shown)$('moreBtn').textContent='Show more ('+(files.length-shown)+' left)';
}

function stopPoll(){if(timer){clearInterval(timer);timer=null;}
  $('go').disabled=false;$('stopBtn').textContent='Stopped';
  toast('Preview polling stopped — the job keeps running on the server');}
async function openFolder(){if(job)await fetch('/api/open',{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify({job})});}

async function pinSave(){const m=$('pinMsg');m.textContent='saving…';m.style.color='var(--mut)';
  const r=await fetch('/api/pinterest',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({client_id:$('pinId').value.trim(),client_secret:$('pinSec').value.trim(),
      access_token:$('pinTok').value.trim()})});
  const j=await r.json();m.style.color=j.error?'var(--err)':'var(--ok)';
  m.textContent=j.error?j.error:(j.token?'OK — token created ('+j.token.slice(0,12)+'…)':j.msg);}
async function pinLoad(){try{const j=await(await fetch('/api/pinterest')).json();
  $('pinId').value=j.client_id||'';$('pinTok').value=j.access_token?'(saved)':'';
  if(j.token)$('pinMsg').textContent='token active: '+j.token.slice(0,14)+'…';}catch(e){}}
$('kw').addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();start();}});
load();pinLoad();
</script></body></html>"""



@app.route("/")
def index():
    return render_template_string(PAGE)

@app.route("/api/start", methods=["POST"])
def api_start():
    o = request.get_json(force=True)
    try:
        # There used to be a hard 500 cap here -> asking for 7000 silently returned
        # 500, which is what caused the "127 out of 7000" confusion. 10000 is allowed now.
        # (Realistic yield still depends on keywords + source + filters - see the log.)
        o["count"] = max(1, min(10000, int(o.get("count", 50))))
        o["min_res"] = int(o.get("min_res", 1920))
    except Exception:
        return jsonify({"error": "invalid count / resolution"}), 400
    job = Job(o)
    JOBS[job.id] = job

    def _run(j):
        try:
            if JOB_QUEUE.locked():
                j.say("Another job is running - waiting in queue...")
                j.state = "queued"
            with JOB_QUEUE:                 # one at a time -> no rate limits
                run_job(j)
        except Exception as e:          # any crash must surface on the page
            j.error = f"{type(e).__name__}: {e}"
            j.state = "error"

    threading.Thread(target=_run, args=(job,), daemon=True).start()
    return jsonify({"job": job.id})

@app.route("/api/status")
def api_status():
    j = JOBS.get(request.args.get("job"))
    if not j:
        return jsonify({"error": "job not found"}), 404
    return jsonify(j.status())

@app.route("/img/<jobid>/<name>")
def img(jobid, name):
    j = JOBS.get(jobid)
    if not j or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        return "no", 404
    path = os.path.join(j.folder, name)
    if not os.path.isfile(path):
        return "no", 404
    return send_file(path)

@app.route("/api/pinterest", methods=["GET", "POST"])
def api_pinterest():
    """Pinterest app credentials save/load (only this source asks for a key)."""
    if request.method == "POST":
        o = request.get_json(force=True) or {}
        cfg = pin_load_cfg()
        changed = False
        for k in ("client_id", "client_secret", "access_token"):
            v = str(o.get(k) or "").strip()
            if v:
                cfg[k] = v
                changed = True
        if not changed:
            return jsonify({"error": "enter something (client_id / secret / token)"}), 400
        try:
            with open(PIN_CFG, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception as e:
            return jsonify({"error": f"save fail: {e}"}), 500
        _PIN.update(tok="", exp=0.0)        # cache reset -> naya token laao
        global _PIN_API_BLOCKED
        _PIN_API_BLOCKED = False            # new token -> try the API once
        tok = pin_token()
        if tok:
            return jsonify({"token": tok[:16] + "...", "msg": "saved"})
        return jsonify({"msg": "saved (token will be created later - check 2FA / app access)"})
    cfg = pin_load_cfg()
    return jsonify({"client_id": cfg.get("client_id", ""),
                    "access_token": (cfg.get("access_token") or "")[:16] + "..."
                    if cfg.get("access_token") else "",
                    "token": _PIN["tok"][:16] + "..." if _PIN["tok"] else ""})

@app.route("/api/open", methods=["POST"])
def api_open():
    j = JOBS.get(request.get_json(force=True).get("job"))
    if j and os.path.isdir(j.folder):
        os.startfile(j.folder)
    return jsonify({"ok": True})

# --- idle-state demo grid: shows what the tool actually does ---
_SAMPLE = {"t": 0.0, "items": []}

@app.route("/api/samples")
def api_samples():
    """Keeps the UI showing wallpapers even while idle.

    Without it the right-hand side was just a black empty area. On load a few
    real wallpapers come in (26) so the user immediately sees what the tool
    will fetch. Cached for 30 min so the network is not hit every time.
    """
    now = time.time()
    if _SAMPLE["items"] and now - _SAMPLE["t"] < 1800:
        return jsonify({"items": _SAMPLE["items"]})
    kws = ["sunflower", "minimal pattern", "ocean", "abstract", "mountain",
           "nebula", "forest", "architecture"]
    out, seen = [], set()
    for kw in kws:
        if len(out) >= 26:
            break
        for fn in (
            lambda: search_wallhaven(kw, "111", 1920, "any", False, 1, seen, out,
                                     pacing=0.2, max_add=5),
            lambda: search_nasa(kw, 1920, seen, out, pages=1, max_add=2),
        ):
            try:
                fn()
            except Exception:
                pass
    items = [{"url": it["thumb"], "src": it.get("src", ""),
              "w": it.get("w", 0), "h": it.get("h", 0)}
             for it in out[:26] if it.get("thumb")]
    _SAMPLE.update(t=now, items=items)
    return jsonify({"items": items})

if __name__ == "__main__":
    threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    print(f"\n  Wallpaper Fetcher is running ->  http://127.0.0.1:{PORT}\n")
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
