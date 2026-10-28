"""
Wallpaper Fetcher - local web tool (universal: any colour, any free/open API source)
Run:  python wallpaper_fetcher.py   (or double-click start-wallpaper-tool.bat)
Then: browser me http://127.0.0.1:8765 kholo

Sources (free / open, no API key needed - 6 total):
  1. Wallhaven API             https://wallhaven.cc/api-docs
  2. Openverse API             https://api.openverse.org/  (CC-licensed aggregator)
  3. Wikimedia Commons API     https://www.mediawiki.org/wiki/API:Main_page
  4. Art Institute of Chicago  https://api.artic.edu/docs/   (public-domain, IIIF)
  5. Cleveland Museum of Art   https://github.com/clevelandart/api  (CC0)
  6. NASA Image Library        https://images.nasa.gov/       (public domain)
Har wallpaper ka source URL + licence manifest.csv me jaata hai.
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
# client_id / client_secret / access_token yahan (tool folder me)
PIN_CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pinterest.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
UA_WM = {"User-Agent": "WallpaperFetcher/1.0 (local personal tool; python-requests)"}
# ARTIC ke API docs ke mutabik AIC-User-Agent header zaroori hai
UA_ART = {"User-Agent": UA["User-Agent"],
          "AIC-User-Agent": "WallpaperFetcher/1.0 (local personal tool)"}
ROOT = r"C:\Users\hp\Pictures"
PORT = 8765

TEXT_EXCL = (" -quote -quotes -text -typography -typo -lettering -font -lyric -poem "
             "-saying -calligraphy -words")

# ---------- colour system: user koi bhi colour choose kar sakta hai ----------
# hue ranges (degrees) - wrap-aware match hona chahiye
COLOUR_RANGES = {
    "yellow": (45, 70), "gold": (34, 50), "orange": (18, 40), "red": (345, 15),
    "pink": (300, 345), "purple": (255, 300), "blue": (195, 255), "teal": (160, 195),
    "green": (75, 160),
}
ACHROMATIC = ("white", "grey", "black")
ALL_COLOURS = ["any"] + list(COLOUR_RANGES.keys()) + list(ACHROMATIC)
# sab sources free/open hain; pinterest sirf free *trial* tier deta hai (key chahiye)
ALL_SOURCES = ["wallhaven", "openverse", "commons", "artic", "cma", "nasa", "pinterest"]

# content-based deny (text/logo/IP/food/interiors/vehicles/people ...). NOTE: koi
# colour-shabd nahi - colour to user ki choice hai.
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
    """ignore: kuch shabd chhod do (jaise NASA source ke liye 'nasa')."""
    t = (text or "").lower()
    for rx in DENY_RE:
        if rx.pattern[2:-2] in ignore:      # \bXYZ\b -> XYZ
            continue
        if rx.search(t):
            return True
    return False

# ---------------- helpers ----------------
def _int(v):
    """API kabhi string deti hai (CMA width/height) - safe int."""
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
    # per-pixel hue bins (36 x 10 deg), sirf chromatic pixels
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
    """Chromatic pixels ka fraction jo selected colour ke hue range (+/-10 tol) me hai."""
    lo, hi = COLOUR_RANGES[colour]
    idxs = [i for i in range(36) if in_range(i * 10 + 5, lo - 10, hi + 10)]
    if m["chrom"] < 0.20:
        return 0.0
    return sum(m["bins"][i] for i in idxs) / max(1, sum(m["bins"]))

def metrics_ok(m, colour, style, relaxed=False, orient="landscape"):
    # ---- orientation gate: wallpaper = chauri (landscape) chahiye ----
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
    """Best quality: bada resolution = zyada score -> ranking me aage."""
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
    """ARTIC ke host ko AIC-User-Agent chahiye, warna 403."""
    return UA_ART if src == "artic" else UA

WM_W = (3840, 2560, 1920, 1280)     # thumb.wikimedia.org inhe reliably serve karta hai

def alt_urls(u):
    """Wikimedia originals (upload-host) 429 de sakta hai -> thumb-host fallback ladder.
    Pinterest: originals 403 ho sakta hai (cache nahi) -> size ladder. Thumb URL ko
    chhota hi rakhte hain (analysis me full-res download bekar hai)."""
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
    """URL ladder me se pehla jo 200 de wahi return karo."""
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
                    return None        # fallback URL try karo (fast-fail, wait mat karo)
                time.sleep(10 + i * 5)
                continue
            return None
        except Exception:
            if not backoff:
                return None
            time.sleep(1 + i)
    return None

# ---------------- sources (sab free/open APIs) ----------------
def search_wallhaven(kw, cat, min_res, colour, smart, pages, seen, out,
                     pacing=0.3, start_page=1, max_add=None):
    """https://wallhaven.cc/api-docs - free, no key. colours prefilter + text exclusions.
    Returns next page number (resume ke liye) ya None agar aur pages nahi.
    max_add: sirf itne hi naye candidates add karo (fair-share quota ke liye)."""
    q = kw + (TEXT_EXCL if smart else "")
    page = start_page
    end = start_page + pages - 1
    added = 0
    while page <= end:
        r = http_get(WH_API, params={"q": q, "categories": cat, "purity": "100",
                                     "sorting": "relevance", "page": page})
        if not r:
            return page            # rate-limit/err: yahin se aage badhna hai baad me
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
            return page            # quota poori - is page se aage baad me
        if page >= r.json().get("meta", {}).get("last_page", 1) or len(data) < 24:
            return None             # aur pages nahi
        page += 1
        time.sleep(pacing)
    return page                    # limit poori - aage ke pages resume ke liye bache

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
        time.sleep(3.2)      # anon burst limit 20/min ka respect
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
    Public-domain art, IIIF full-res (4000px tak), no API key. AIC-User-Agent header chahiye."""
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
                continue                      # sirf public domain = fully legal
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
    Space/earth/science photos - 4K tak milte hain (search metadata me res nahi hota,
    isliye min_res check save() ke waqt actual pixels par hota hai)."""
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

# ---------------- Pinterest (free *trial* tier - token chahiye) ----------------
_PIN = {"tok": "", "exp": 0.0}

def pin_load_cfg():
    try:
        with open(PIN_CFG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def pin_token():
    """Token cache -> nahi to client_credentials se khud laao (30 din valid, 2FA chahiye)."""
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

# -------- Pinterest: PUBLIC FEED se images (bina login, bina API key) --------
# Official v5 search (`/search/partner/pins`) = "restricted feature: pin_search" +
# app "trial access pending" (code 3) -> dono Pinterest support se hi hote hain.
# Lekin public pages (/wallpapers, /nature, ...) server-side <link rel="preload">
# me pinimg URLs dete hain, aur i.pinimg.com/originals/<hash> full-res deta hai.
# Pin ID/title client-side load hote hain (HTML me nahi) -> landing = page URL,
# aur title/tag based deny (food/text/logo) Pinterest pe lagu NAHI hota.
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
# keyword -> kaunsi feed pehle (search keyless hai, isliye ye ordering)
_PIN_HINTS = [
    ("wallpaper desktop background screen", ("wallpapers", "ideas", "explore")),
    ("flower tree mountain forest sky nature", ("nature", "travel", "ideas")),
    ("photography photo camera lens", ("photography", "ideas")),
    ("travel beach ocean sea lake", ("travel", "nature")),
    ("design abstract pattern art", ("design", "ideas")),
    ("fashion dress outfit style", ("fashion", "ideas")),
    ("tattoo ink", ("tattoos", "design")),
]
_PIN_API_BLOCKED = False          # restricted/trial error milte hi retry band

def _pin_feeds_for(kw):
    """Keyword se pehle relevant feed, phir baaki sab."""
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
    """Har feed ka HTML fetch -> unique hashes -> /originals/ candidates."""
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
            note(f"pinterest: {u.rstrip('/').rsplit('/', 1)[-1]} se kuch nahi mila")
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
                "thumb": f"https://i.pinimg.com/1200x/{hsh}",      # analysis ke liye
                "ext": ext,
                "landing": u,                    # pin ID HTML me nahi hota
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
    """Playwright ke liye URL list.
    #1 = keyword SEARCH (bina login chalta hai - official API me blocked tha!)
    #2 = ideas (sabse rich logged-out page)
    #3 = HOME (login ho to asli personalized feed)
    phir relevant category feeds."""
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
    login=False -> --allow-anon: bina login sab public pages chalte hain
    login=True  -> headed browser, tum login karo (asli personalized home feed)"""
    if not os.path.exists(PIN_SCRAPER):
        return 0
    urls = _pin_urls_for(kw)
    cmd = [sys.executable, PIN_SCRAPER, "--scroll", str(scroll), "--urls"] + urls
    if login:
        cmd += ["--wait-login", str(wlog)]          # headed -> login possible
    else:
        cmd += ["--headless", "--allow-anon"]       # koi interruption nahi
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
        note("Pinterest: public pages se chal raha hai. Asli HOME feed chahiye to "
             "ek baar login karo:  python pin_home_feed.py  (optional)")

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
        note(f"pinterest playwright: {len(j.get('pins') or [])} pins mile, "
             f"{added} naye candidates")
    return added

def search_pinterest(kw, min_res, colour, seen, out, pages=2, max_add=None, note=None):
    """Pinterest = Playwright (keyword search + ideas + home) -> static feeds -> API."""
    global _PIN_API_BLOCKED
    # 1) Playwright: keyword search + ideas + home (bina login)
    added = 0
    scroll = 10 if not max_add else max(6, min(26, max_add // 5 + 6))
    try:
        added = _pin_home_feed(kw, seen, out, max_add, note=note, scroll=scroll)
    except Exception as e:
        if note is not None:
            note(f"pinterest playwright error: {e}")
    # 2) static public feeds (fallback / agar quota poori nahi hui)
    room = None if max_add is None else max_add - added
    if room is None or room > 0:
        feeds = _pin_feeds_for(kw)
        added += _pin_from_feed(kw, seen, out,
                                feeds[:max(2, min(len(feeds), max(pages, 4)))],
                                room, note=note)
    # 3) official API (jab tak blocked nahi hua)
    if not _PIN_API_BLOCKED:
        room = None if max_add is None else max_add - added
        if room is None or room > 0:
            _search_pinterest_api(kw, min_res, colour, seen, out,
                                  pages=pages, max_add=room, note=note)
    return added

def _search_pinterest_api(kw, min_res, colour, seen, out, pages=2, max_add=None, note=None):
    """https://developers.pinterest.com/docs/api/v5/search_partner_pins/
    Restricted feature - abhi app ko access nahi. Licence: pinterest-content-license."""
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
            params["bookmark"] = ""      # bookmark pehle page se aata hai; niche handle
        r = http_get(PIN_SEARCH, headers=hdrs, timeout=40, params=params)
        if not r:
            _PIN_API_BLOCKED = True   # restricted feature / trial pending -> feed kaam karega
            if note is not None:
                note("pinterest official API blocked (restricted feature: pin_search) "
                     "-> public feed se fetch kar raha hoon")
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
            # sabse bada available size chuno
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
JOB_QUEUE = threading.Lock()      # ek waqt me ek hi job -> API 429 nahi hote

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
            return None            # openverse 424 thumb = broken origin, skip (fast rahega)
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
    smart = bool(o.get("smart", True))   # default ON - missing key se crash mat ho
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
    saved_ids = set()               # kya-kya save hua (relaxed re-check me exclude)
    mf = os.path.join(folder, "manifest.csv")
    old_rows = []
    if os.path.exists(mf):
        with open(mf, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                old_rows.append(r)
                if r.get("id"):
                    seen_ids.add(r["id"])
                # visual dedupe: purani runs ke ahash seed karo, warna ek hi folder
                # me baar baar chalane pe same image dobar aaati (alag source/id)
                if r.get("ahash"):
                    try:
                        hashes.add(int(r["ahash"], 16))
                    except Exception:
                        pass
    if old_rows:
        job.say(f"Purani folder: {len(old_rows)} images -> unke IDs+hash skip kiye "
                f"(nayi jagah hi save hoga)")

    srcs = list(ALL_SOURCES) if source == "all" else [source]
    job.state = "searching"
    job.say(f"Searching: {', '.join(words)}  [{colour} / {style}] sources={'+'.join(srcs)}")
    if "pinterest" in srcs:
        if orient == "landscape":
            job.say("NOTE: Pinterest ~90% PORTRAIT (phone wallpapers) hai "
                    "-> landscape me iska yield lagbhag 0 hoga.")
        elif orient == "any":
            job.say("Pinterest: portrait (phone) wallpapers dominate karte hain.")
    # Bade target + landscape = bahut kam match (sources me portrait art/photos zyada)
    if target >= 300 and orient == "landscape":
        job.say(f"HEADS-UP: target={target} + Orientation=Landscape. Museum art "
                "(ARTIC/CMA) aur Pinterest mostly PORTRAIT hain -> unka zyadatar "
                "part reject hoga. Zyada chahiye to Orientation=Any chuno "
                "(ya Portrait, phone wallpapers ke liye).")

    min_pool = max(target * 5, 180)
    # fair-share quota per source - warna Wallhaven poora pool bhar deta hai
    # aur baaki sources kabhi chalte hi nahi
    per_src = max(min_pool // len(srcs), 40)

    # ---- search me min_res: EXACT rakho, sirf high-res pe dhilao ----
    # (a) 2560 tak sources seedha exact min_res pe quota bhar lete hain (A/B test:
    #     1920 pe wallhaven/commons/cma/nasa/artic sab = 500) - dhila rakhne se
    #     1024-1920 px wale candidates aate jo save() me reject hote = time waste.
    # (b) 4K (3840) pe sources SEEDHA 0 de dete hain (test: cma=0, wallhaven=16/87)
    #     toh wahan hi pool badhane ke liye dhilao. Exact min_res save() me enforce hota hai.
    if min_res > 2560:
        search_min = max(1920, min_res // 2)
        job.say(f"High-res {min_res}px -> search me {search_min}px "
                f"(exact {min_res}px save() me enforce hoga, warna sources 0 de deti hain)")
    else:
        search_min = min_res

    # ---- keyword expansion (bade target ke liye) ----
    # "sunflower field" -> "sunflower field", "sunflower", "field"
    # Chhote target pe time waste na ho, isliye sirf bade counts pe.
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
    # search ka time budget - warna bade target pe (7000) ghanton tak chalega
    # aur user ko pata hi nahi chalta. Budget ke baad jo mila analyse hota hai.
    search_deadline = time.time() + min(900, 120 + int(target) // 5)

    def have(s):
        return sum(1 for c in cands if c["src"] == s)

    # ---- TWO-PASS search ----
    # PEHLE: tez sources (wallhaven/commons/artic/cma/nasa) = sab terms ke liye.
    # BAAD ME: slow sources (pinterest = ~6 min browser, openverse = rate-limited).
    # Warna pinterest term-1 me hi poora time budget kha leta tha aur keyword
    # expansion (11 terms) kabhi chalta hi nahi - isliye sirf phrase search hota
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
        # openverse LAST + page cap. Ye rate-limited hai (20/min, 200/day) toh
        # bade page counts pe ghanton lag jaate hain. Max 2 pages per term - jitna
        # banega; baaki budget ke andar tez sources already bhar chuke hote hain.
        if "openverse" in slow_srcs and have("openverse") < per_src:
            room = per_src - have("openverse")
            search_openverse(w, search_min, None, seen_ids, cands,
                             pages=2, max_add=min(room, 60))
            job.counts["searched"] = len(cands)

    def pool_full():
        return all(have(s) >= per_src for s in srcs)

    def budget_over():
        if target >= 300 and len(cands) >= min_pool:
            job.say(f"Pool poora: {len(cands)} candidates -> aage analyse")
            return True
        if time.time() >= search_deadline:
            job.say(f"Search budget khatam -> {len(cands)} candidates pe analyse "
                    f"kar raha hoon (target {target})")
            return True
        return False

    # PASS 1: tez sources - sab terms (fast, ~30s me sab nikal aata hai)
    for w in terms:
        search_fast(w)
        if pool_full() or budget_over():
            break
    # PASS 2: slow sources (pinterest browser + openverse rate-limit) - sirf
    # tabhi jab abhi bhi candidates ki kami ho.
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
        job.error = "Koi candidate nahi mila - keywords badal ke dekho (ya source badlo)"
        job.state = "error"
        return

    # ---------- phase 2: pixel analysis ----------
    job.state = "analysing"
    ok = analyse_items(job, cands, colour, style, min_res=min_res, orient=orient)
    job.counts["matched"] = len(ok)
    job.say(f"Matched {len(ok)} (target {target})")
    if not ok:
        job.error = "Colour/style filters ke saath kuch nahi mila - style ya colour badlo"
        job.state = "error"
        return

    # ---------- phase 3: smart tag filter (wallhaven ke tags + title deny) ----------
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
    failed_dl = set()              # download fail hue ids (phase-5 me retry)

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
            # actual pixels se resolution bonus correct (ARTIC/NASA res metadata nahi dete)
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
                   # ahash -> agle run me isi folder me cross-run dedupe (visual)
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
        # (a) jo downloads 429/waqt ki wajah se fail hue the, unhe pehle retry
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
        # (b1) purane candidates jo strict gate me reject hue the - ab relaxed se check
        ok_ids = {it["id"] for it in ok} | saved_ids
        old_rej = [it for it in cands if it["id"] not in ok_ids]
        # keyword broadening: "sunflower field" -> "sunflower", "field" bhi try
        terms = []
        for w in words:
            terms.append(w)
            parts = w.split()
            if len(parts) > 1:
                terms.extend(parts)
        extra = []
        # fair-share quota (phase-1 jaisa) - warna ek source sab kha jaata hai
        per2 = max(target, 30)

        def have2(s, bag=None):
            b = extra if bag is None else bag
            return sum(1 for c in b if c["src"] == s)

        # relaxed search ka bhi budget - warna 7000 target pe ghanton tak chalega
        r_deadline = time.time() + min(600, 90 + int(target) // 6)

        def r_over():
            if time.time() >= r_deadline:
                job.say(f"Relaxed search budget khatam -> {len(extra)} naye candidates")
                return True
            return all(have2(s) >= per2 for s in srcs)

        # PASS 1: tez sources pehle (sab terms) - pinterest ~6 min leta hai,
        # warna wo term-1 me hi budget kha jata tha.
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
        job.say(f"relaxed: {len(old_rej)} purane rejected + {len(extra)} naye candidates")
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
        job.say(f"Note: {job.counts['saved']}/{target} mile - keywords/colour/source badal ke dekho")

    if manifest:
        # fieldnames = sab rows ka UNION. Purani rows me 'ahash' nahi hota, nayi me
        # hoti hai -> sirf manifest[0].keys() lene pe DictWriter crash karta.
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
<title>Wallpaper Fetcher</title>
<style>
 :root{--bg:#111417;--card:#1a1f24;--acc:#ffd84d;--txt:#e8eaed;--mut:#8b939c}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.5 system-ui,Segoe UI,sans-serif}
 .wrap{max-width:1180px;margin:0 auto;padding:26px 18px 60px}
 h1{font-size:26px;margin:0 0 4px} h1 span{color:var(--acc)}
 .sub{color:var(--mut);margin-bottom:22px}
 .card{background:var(--card);border:1px solid #262c33;border-radius:14px;padding:18px;margin-bottom:16px}
 label{display:block;font-size:12px;letter-spacing:.4px;text-transform:uppercase;color:var(--mut);margin:0 0 6px}
 input[type=text],select,textarea{width:100%;background:#0e1114;border:1px solid #2c333a;color:var(--txt);
   padding:11px 12px;border-radius:9px;font-size:15px}
 textarea{height:74px;resize:vertical}
 .row{display:flex;gap:14px;flex-wrap:wrap} .row>div{flex:1;min-width:170px}
 .swatches{display:flex;gap:8px;flex-wrap:wrap;margin-top:4px}
 .sw{border:2px solid #2c333a;background:#0e1114;color:var(--txt);border-radius:9px;padding:7px 12px;
   cursor:pointer;font-size:14px;display:flex;align-items:center;gap:7px}
 .sw i{width:14px;height:14px;border-radius:50%;display:inline-block;border:1px solid #00000055}
 .sw.on{border-color:var(--acc);background:#23261c}
 .checks{display:flex;gap:22px;flex-wrap:wrap;margin-top:14px;color:var(--mut)}
 .checks label{text-transform:none;font-size:14px;color:var(--txt);display:flex;gap:7px;align-items:center;margin:0}
 button.go{background:var(--acc);border:0;color:#171300;font-weight:700;font-size:16px;padding:13px 26px;
   border-radius:10px;cursor:pointer;margin-top:16px}
 button:disabled{opacity:.45;cursor:not-allowed}
 button.ghost{background:#2a3138;color:var(--txt);font-weight:600;padding:9px 16px;font-size:14px;margin:0}
 .bar{height:9px;background:#0e1114;border-radius:6px;overflow:hidden;margin:10px 0}
 .bar i{display:block;height:100%;background:linear-gradient(90deg,#ffd84d,#ff9d3d);width:0;transition:width .4s}
 .phase{font-size:13px;color:var(--acc);text-transform:capitalize}
 #log{font:12.5px/1.7 Consolas,monospace;color:#9aa4ad;background:#0e1114;border-radius:9px;
   padding:10px 12px;height:150px;overflow:auto;white-space:pre-wrap}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px}
 .t{background:#0e1114;border:1px solid #262c33;border-radius:10px;overflow:hidden}
 .t img{width:100%;height:120px;object-fit:cover;display:block;background:#000}
 .t .m{padding:7px 9px;font-size:11.5px;color:var(--mut)}
 .t .m b{color:var(--txt);display:block;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .badge{display:inline-block;background:#232b33;border:1px solid #333d47;border-radius:5px;
   padding:0 5px;font-size:10.5px;color:#9ecbff;margin-right:4px}
 .stats{display:flex;gap:18px;flex-wrap:wrap;font-size:14px;color:var(--mut);margin-top:6px}
 .stats b{color:var(--acc)}
 .folder{font:13px Consolas,monospace;color:#9ee493;word-break:break-all;margin-top:8px}
 .hidden{display:none}
 footer{color:var(--mut);font-size:13px;border-top:1px solid #262c33;padding-top:14px;margin-top:26px}
 footer a{color:#9ecbff}
</style></head><body><div class="wrap">
<h1>🖼️ Wallpaper <span>Fetcher</span></h1>
<div class="sub">Keywords + colour daalo &rarr; open/free APIs se fetch &rarr; nayi folder me save (+ manifest.csv)</div>

<div class="card">
  <label>Keywords (comma ya naye line se alag)</label>
  <textarea id="kw" placeholder="sunflower field, minimal pattern, ocean waves"></textarea>

  <label style="margin-top:16px">Colour</label>
  <div class="swatches" id="swatches">
    <button class="sw on" data-c="any"><i style="background:linear-gradient(135deg,#ff5f6d,#ffc371,#7ee8fa,#a78bfa)"></i>Any</button>
    <button class="sw" data-c="yellow"><i style="background:#ffd84d"></i>Yellow</button>
    <button class="sw" data-c="gold"><i style="background:#e0a527"></i>Gold</button>
    <button class="sw" data-c="orange"><i style="background:#ff8c1a"></i>Orange</button>
    <button class="sw" data-c="red"><i style="background:#e5484d"></i>Red</button>
    <button class="sw" data-c="pink"><i style="background:#ff7ab6"></i>Pink</button>
    <button class="sw" data-c="purple"><i style="background:#a78bfa"></i>Purple</button>
    <button class="sw" data-c="blue"><i style="background:#4d9fff"></i>Blue</button>
    <button class="sw" data-c="teal"><i style="background:#2ec4b6"></i>Teal</button>
    <button class="sw" data-c="green"><i style="background:#4caf50"></i>Green</button>
    <button class="sw" data-c="white"><i style="background:#f5f5f0"></i>White</button>
    <button class="sw" data-c="grey"><i style="background:#9aa0a6"></i>Grey</button>
    <button class="sw" data-c="black"><i style="background:#242424"></i>Black</button>
  </div>

  <div class="row" style="margin-top:16px">
    <div><label>Style / tone</label><select id="style">
      <option value="soft">Light &amp; soft (gentle, bright)</option>
      <option value="vivid">Vivid &amp; punchy (saturated)</option>
      <option value="any">No style filter</option>
    </select></div>
    <div><label>Source (free / open APIs)</label><select id="source">
      <option value="all">All 7 sources (best quality + max choice)</option>
      <option value="wallhaven">Wallhaven API (best wallpapers)</option>
      <option value="openverse">Openverse API (CC-licensed)</option>
      <option value="commons">Wikimedia Commons (free licences)</option>
      <option value="artic">Art Institute of Chicago (public-domain art)</option>
      <option value="cma">Cleveland Museum of Art (CC0 art)</option>
      <option value="nasa">NASA Image Library (public domain)</option>
      <option value="pinterest">Pinterest (keyword search + home feed — bina key ✅)</option>
    </select></div>
    <div><label>Orientation</label><select id="orient">
      <option value="landscape">Landscape (chauri - recommended)</option>
      <option value="any">Any (landscape + portrait)</option>
      <option value="portrait">Portrait (lambi)</option>
    </select></div>
    <div><label>Kitne wallpapers</label><input type="text" id="count" value="50">
      <small>Max 10000. Bade number (1000+) = lamba time + <b>Orientation=Any</b> ya <b>min_res=1280</b> karo warna portrait/4K filters pool kaat dete hain.</small></div>
    <div><label>Min resolution (long side)</label><select id="minres">
      <option value="1920">1920 px</option><option value="2560">2560 px</option>
      <option value="3840">3840 px</option><option value="1280">1280 px</option></select></div>
    <div><label>Category</label><select id="cat">
      <option value="general">General (recommended)</option><option value="all">General + Anime</option></select></div>
  </div>
  <div class="checks">
    <label><input type="checkbox" id="smart" checked> Smart filter (text / logo / food / brand hatao)</label>
    <label><input type="checkbox" id="newfolder" checked> Hamesha nayi folder banao</label>
  </div>
  <div class="row" style="align-items:flex-end">
    <div><label>Folder ka naam (optional)</label><input type="text" id="folder" placeholder="auto: keywords se"></div>
  </div>
  <button class="go" id="go" onclick="start()">Fetch karo</button>
</div>

<div class="card hidden" id="prog">
  <div class="phase" id="phase">queued</div>
  <div class="bar"><i id="fill"></i></div>
  <div class="stats">
    <span>searched <b id="c1">0</b></span><span>analysed <b id="c2">0</b></span>
    <span>matched <b id="c3">0</b></span><span>saved <b id="c4">0</b> / <b id="c5">0</b></span>
    <span id="elapsed"></span>
  </div>
  <div class="folder" id="folderPath"></div>
  <div style="display:flex;gap:10px;margin-top:12px">
    <button class="ghost" onclick="openFolder()">📂 Folder kholo</button>
    <button class="ghost" onclick="stopPoll()">Rok do</button>
  </div>
  <div id="log" style="margin-top:12px"></div>
</div>

<div class="card hidden" id="res">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
    <b id="resTitle">Results</b>
    <button class="ghost" onclick="location.reload()">Naya fetch</button>
  </div>
  <div class="grid" id="grid"></div>
</div>

<div class="card">
  <b style="font-size:15px">⚙️ Pinterest Settings <span style="color:var(--mut);font-weight:400">(OPTIONAL — Pinterest bina key bhi chalta hai ✅)</span></b>
  <div class="row" style="margin-top:12px">
    <div><label>Client ID (app id)</label><input type="text" id="pinId" placeholder="123456789012"></div>
    <div><label>Client Secret</label><input type="password" id="pinSec" placeholder="••••••••"></div>
    <div><label>Ya seedha access token (optional)</label><input type="text" id="pinTok" placeholder="pinc... / pina..."></div>
  </div>
  <div style="display:flex;gap:10px;margin-top:12px;align-items:center;flex-wrap:wrap">
    <button class="ghost" onclick="pinSave()">💾 Save + token banao</button>
    <span id="pinMsg" style="font-size:13px;color:var(--mut)"></span>
  </div>
  <div style="font-size:12.5px;color:var(--mut);margin-top:10px;line-height:1.6">
    <b>Pinterest ab is card ke bina chalta hai</b> — tool apna Chromium (Playwright)
    khud chalata hai aur <b>asli keyword search</b>
    (<code>/search/pins/?q=…</code>) + <code>/ideas/</code> + <b>home feed</b> + public category feeds
    (<code>/wallpapers</code>, <code>/nature</code>, <code>/photography</code>…)
    se images nikalta hai — <b>na login chahiye, na key</b>. Phir colour + orientation + resolution
    filters lagte hain.<br>
    <b>Optional (ek baar): apna Chrome session utha lo</b> — folder me
    <code>pinterest-session.bat</code> double-click karo (Chrome band karo &rarr; ENTER).
    Uske baad <b>asli personalized home feed</b> + har pin ki <b>real link/title</b>
    automatically milte hain. Session <code>pin_session.json</code> me save ho jata hai.<br>
    (Alternative: <code>pinterest-login.bat</code> — usme browser me khud login karna padta hai.)<br>
    Ye card sirf tab kaam aayega jab Pinterest ka official API access mil jaye — tab
    <code>/search/partner/pins</code> chalu ho jayega. Uske liye
    <a href="https://developers.pinterest.com/apps/" target="_blank" style="color:#9ecbff">developers.pinterest.com/apps</a>
    &rarr; <b>Client Credentials</b> grant, scope <code>boards:read,pins:read</code>, 2FA on — phir bhi
    <b>restricted feature</b> + <b>trial approval</b> support ticket se hi milta hai.<br>
    <span style="color:#e8b96a">Note: bina login ke Pinterest pe title/tag filters (food/text/logo deny)
    lagu nahi hote (alt text khali aata hai). Licence = pinterest-content-license (sirf personal use).
    Pinterest ~90% PORTRAIT images deta hai — phone wallpaper ke liye best.</span>
  </div>
</div>

<footer>
  <b>Sources — 7 free options (sab keyless; Pinterest official API optional hai):</b><br>
  • <a href="https://wallhaven.cc/api-docs" target="_blank">Wallhaven API</a> — free wallpaper API (wallhaven.cc)<br>
  • <a href="https://api.openverse.org/" target="_blank">Openverse API</a> — open source, CC-licensed images (WordPress)<br>
  • <a href="https://commons.wikimedia.org/w/api.php" target="_blank">Wikimedia Commons API</a> — free-licensed media (Wikipedia)<br>
  • <a href="https://api.artic.edu/docs/" target="_blank">Art Institute of Chicago API</a> — public-domain museum art, IIIF full-res<br>
  • <a href="https://github.com/clevelandart/api" target="_blank">Cleveland Museum of Art API</a> — CC0 open-access collection<br>
  • <a href="https://images.nasa.gov/" target="_blank">NASA Image and Video Library API</a> — public-domain space/science imagery<br>
  • <b>Pinterest</b> — <b>Playwright browser</b> se asli <a href="https://www.pinterest.com/search/pins/?q=wallpaper" target="_blank">keyword search</a>
     + <a href="https://www.pinterest.com/ideas/" target="_blank">/ideas</a> + home feed + public category feeds
     (bina login/key; official <a href="https://developers.pinterest.com/docs/api/v5/search_partner_pins/" target="_blank">search API</a>
     = restricted feature, app ko access nahi). Images <b>user-generated copyright</b> = personal use only.<br>
  Har wallpaper ka original link + licence <code>manifest.csv</code> me save hota hai.<br>
  Best quality: resolution boost 4K (+14) &gt; 2K (+9) &gt; 1080p (+4), landscape default.
</footer>
</div>

<script>
let job=null, timer=null, colour='any';
document.getElementById('swatches').addEventListener('click', e=>{
  const b=e.target.closest('.sw'); if(!b) return;
  document.querySelectorAll('.sw').forEach(x=>x.classList.remove('on'));
  b.classList.add('on'); colour=b.dataset.c;
});
async function start(){
  const kw=document.getElementById('kw').value.trim();
  if(!kw){alert('Pehle keywords daalo');return;}
  const body={keywords:kw, count:+document.getElementById('count').value||50,
    colour:colour, style:document.getElementById('style').value,
    source:document.getElementById('source').value,
    orientation:document.getElementById('orient').value,
    min_res:+document.getElementById('minres').value,
    category:document.getElementById('cat').value, smart:document.getElementById('smart').checked,
    newfolder:document.getElementById('newfolder').checked, folder:document.getElementById('folder').value};
  document.getElementById('go').disabled=true;
  document.getElementById('prog').classList.remove('hidden');
  document.getElementById('res').classList.add('hidden');
  document.getElementById('grid').innerHTML='';
  document.getElementById('log').textContent='';
  const r=await fetch('/api/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json();
  if(j.error){alert(j.error);document.getElementById('go').disabled=false;return;}
  job=j.job; timer=setInterval(poll,900); poll();
}
async function poll(){
  if(!job) return;
  const r=await fetch('/api/status?job='+job); const s=await r.json();
  document.getElementById('phase').textContent=s.state;
  const c=s.counts, tgt=Math.max(c.target,1);
  document.getElementById('fill').style.width=Math.min(100,100*c.saved/tgt)+'%';
  c1.textContent=c.searched; c2.textContent=c.analysed; c3.textContent=c.matched;
  c4.textContent=c.saved; c5.textContent=c.target;
  document.getElementById('elapsed').textContent=s.elapsed+'s';
  document.getElementById('folderPath').textContent=s.folder||'';
  document.getElementById('log').textContent=s.log.join('\n');
  const log=document.getElementById('log'); log.scrollTop=log.scrollHeight;
  if(s.files.length){
    document.getElementById('res').classList.remove('hidden');
    document.getElementById('resTitle').textContent='Saved: '+s.files.length;
    const g=document.getElementById('grid');
    g.innerHTML=s.files.map(f=>`<div class="t"><img loading="lazy" src="${f.url}">
      <div class="m"><span class="badge">${f.src}</span><b>${f.name}</b>${f.res} &middot; score ${f.score} &middot; ${f.license}</div></div>`).join('');
  }
  if(s.state==='done'||s.state==='error'){
    clearInterval(timer); timer=null;
    document.getElementById('go').disabled=false;
    if(s.state==='error') alert('Error: '+s.error);
    else document.getElementById('fill').style.width='100%';
  }
}
function stopPoll(){if(timer){clearInterval(timer);timer=null;} document.getElementById('go').disabled=false;}
async function openFolder(){ if(job) await fetch('/api/open',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job})}); }
async function pinSave(){
  const m=document.getElementById('pinMsg'); m.textContent='saving...';
  const r=await fetch('/api/pinterest',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({client_id:document.getElementById('pinId').value.trim(),
                         client_secret:document.getElementById('pinSec').value.trim(),
                         access_token:document.getElementById('pinTok').value.trim()})});
  const j=await r.json();
  m.style.color = j.error ? '#ff8a8a' : '#9ee493';
  m.textContent = j.error ? j.error : (j.token ? 'OK - token ban gaya ('+j.token.slice(0,12)+'...)' : j.msg);
}
async function pinLoad(){
  try{
    const j=await (await fetch('/api/pinterest')).json();
    document.getElementById('pinId').value=j.client_id||'';
    document.getElementById('pinTok').value=j.access_token?'(saved)':'';
    if(j.token) document.getElementById('pinMsg').textContent='token active: '+j.token.slice(0,14)+'...';
  }catch(e){}
}
pinLoad();
document.getElementById('kw').addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey))start();});
</script></body></html>"""

@app.route("/")
def index():
    return render_template_string(PAGE)

@app.route("/api/start", methods=["POST"])
def api_start():
    o = request.get_json(force=True)
    try:
        # Pehle yahan 500 ka hard cap tha -> user 7000 maangta to chupke se 500
        # ban jaata aur "7000 me se 127" jaisa confusion hota. Ab 10000 tak allowed.
        # (Realistic yield keywords+source+filters pe depend karta hai - log me dikhta hai.)
        o["count"] = max(1, min(10000, int(o.get("count", 50))))
        o["min_res"] = int(o.get("min_res", 1920))
    except Exception:
        return jsonify({"error": "count/resolution galat hai"}), 400
    job = Job(o)
    JOBS[job.id] = job

    def _run(j):
        try:
            if JOB_QUEUE.locked():
                j.say("Doosri job chal rahi hai - queue me wait...")
                j.state = "queued"
            with JOB_QUEUE:                 # ek-ek karke -> rate-limit nahi
                run_job(j)
        except Exception as e:          # koi bhi crash ho to page par dikhe
            j.error = f"{type(e).__name__}: {e}"
            j.state = "error"

    threading.Thread(target=_run, args=(job,), daemon=True).start()
    return jsonify({"job": job.id})

@app.route("/api/status")
def api_status():
    j = JOBS.get(request.args.get("job"))
    if not j:
        return jsonify({"error": "job nahi mila"}), 404
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
    """Pinterest app credentials save/load (sirf yeh source key maangta hai)."""
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
            return jsonify({"error": "kuch daalo (client_id / secret / token)"}), 400
        try:
            with open(PIN_CFG, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception as e:
            return jsonify({"error": f"save fail: {e}"}), 500
        _PIN.update(tok="", exp=0.0)        # cache reset -> naya token laao
        global _PIN_API_BLOCKED
        _PIN_API_BLOCKED = False            # naya token -> API ek baar try karo
        tok = pin_token()
        if tok:
            return jsonify({"token": tok[:16] + "...", "msg": "saved"})
        return jsonify({"msg": "saved (token baad me banega - 2FA/app access check karo)"})
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

if __name__ == "__main__":
    threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    print(f"\n  Wallpaper Fetcher chal raha hai ->  http://127.0.0.1:{PORT}\n")
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
