"""
Pinterest feed scraper (Playwright).

Modes:
  --allow-anon : public pages (no login) -> home/explore/ideas/search
  (default)    : login required -> your personalized HOME FEED

Several URLs in one browser session, infinite-scroll on each URL.
JSON on stdout; logs on stderr.

To log in:        python pin_home_feed.py --urls https://www.pinterest.com/
                  (opens a headed browser, log in within 150s, session saved)
"""
import argparse, json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(HERE, ".pin_profile")   # logged-in session persists here


def log(m):
    sys.stderr.write(str(m) + "\n")
    sys.stderr.flush()


EXTRACT_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  const push = (id, src, alt, href) => {
    if (!src || !src.includes('pinimg.com') || seen.has(id)) return;
    seen.add(id);
    out.push({id: String(id), src: src, alt: (alt||'').slice(0,300), href: href||''});
  };
  // 1) pin links -> gives the id, so a landing URL can be built
  document.querySelectorAll('a[href*="/pin/"]').forEach(a => {
    const m = (a.getAttribute('href')||'').match(/\/pin\/(\d+)/);
    if (!m) return;
    const img = (a.closest('[data-test-id="pin"]') || a.parentElement || a).querySelector('img');
    const src = img ? (img.currentSrc || img.src || '') : '';
    push(m[1], src, img ? img.alt : '', a.href);
  });
  // 2) feed images that have no link
  document.querySelectorAll('img').forEach(img => {
    const src = img.currentSrc || img.src || '';
    if (!src.includes('pinimg.com')) return;
    const mm = src.match(/\/pin\/(\d{10,})/);
    const id = mm ? mm[1] : 'img_' + src.split('/').slice(-1)[0].split('.')[0];
    push(id, src, img.alt || '', '');
  });
  return out;
}
"""

LOGIN_JS = r"""
() => {
  const txt = (document.body && document.body.innerText || '').slice(0, 5000);
  // the cookie check is WRONG (anonymous users also get _pinterest_sess) -> use UI + _auth
  const loginWall = /Join Pinterest for free|I already have an account|Create the life you love|Welcome to Pinterest|Log in to continue|Sign up to continue/i.test(txt);
  const imgs  = document.querySelectorAll('img[src*="pinimg.com"]').length;
  const pins  = document.querySelectorAll('a[href*="/pin/"]').length;
  return {loginWall: loginWall, imgs: imgs, pins: pins, url: location.href,
          head: txt.slice(0, 160)};
}
"""


def is_logged_in(page, ctx=None):
    """_auth cookie: '0' = anonymous, '1' = logged in (verified). UI = fallback."""
    if ctx is not None:
        try:
            for c in ctx.cookies():
                if c.get("name") == "_auth" and c.get("value") not in ("0", "", None):
                    return True, {"via": "_auth", "val": str(c.get("value"))[:20]}
        except Exception:
            pass
    try:
        s = page.evaluate(LOGIN_JS)
    except Exception:
        return False, {}
    ok = (not s.get("loginWall")) and (s.get("pins", 0) >= 3 or s.get("imgs", 0) >= 8)
    return ok, s


def upgrade(src):
    """Size-variant URL -> originals (full res). save() tries the ladder itself."""
    m = re.search(r"i\.pinimg\.com/[^/]+/(.+)$", src.replace("\\/", "/"))
    if not m:
        return src, None
    hsh = m.group(1)
    return f"https://i.pinimg.com/originals/{hsh}", hsh


def scrape_url(page, url, scroll, out, seen):
    """Scroll one URL and collect its pins (deduped across URLs via `seen`)."""
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3200)
    before = len(out)
    for i in range(scroll):
        try:
            batch = page.evaluate(EXTRACT_JS) or []
        except Exception as e:
            log(f"  extract err: {e}")
            batch = []
        new = 0
        for it in batch:
            if it["id"] in seen:
                continue
            seen.add(it["id"])
            full, hsh = upgrade(it["src"])
            if not hsh:
                continue
            out.append({
                "id": it["id"], "hash": hsh, "path": full,
                "thumb": f"https://i.pinimg.com/1200x/{hsh}",
                "alt": it["alt"],
                # asli pin id nahi mili to page URL hi landing (woh honest hai)
                "landing": (f"https://www.pinterest.com/pin/{it['id']}/"
                            if not it["id"].startswith("img_") else url),
            })
            new += 1
        log(f"  scroll {i+1}/{scroll}: +{new} (total {len(out)})")
        page.mouse.wheel(0, 2600)
        page.wait_for_timeout(2100)
        if new == 0 and i >= 3 and len(out) > before:
            break
        if new == 0 and i >= 3:
            break
    return len(out) - before


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scroll", type=int, default=12)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--wait-login", type=int, default=150)
    ap.add_argument("--urls", nargs="+", default=["https://www.pinterest.com/"])
    ap.add_argument("--allow-anon", action="store_true",
                    help="no login required (public pages)")
    ap.add_argument("--shot", default="", help="screenshot prefix (for verification)")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print(json.dumps({"error": f"playwright missing: {e}"}))
        return 2

    os.makedirs(PROFILE, exist_ok=True)
    ctx = None
    page = None

    def shot(tag):
        if not args.shot or page is None:
            return
        try:
            path = args.shot
            if not path.lower().endswith(".png"):
                path = path + "-" + tag + ".png"
            page.screenshot(path=path)
            log(f"shot saved: {path}")
        except Exception as e:
            log(f"shot failed: {e}")

    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                PROFILE,
                headless=args.headless,
                viewport={"width": 1440, "height": 1800},
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"),
                locale="en-US",
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()

            # inject the saved session (extracted by chrome_pinterest_session.py)
            sess_file = os.path.join(HERE, "pin_session.json")
            if os.path.exists(sess_file):
                try:
                    ck = json.load(open(sess_file, encoding="utf-8"))
                    if ck:
                        ctx.add_cookies(ck)
                        log(f"loaded {len(ck)} saved pinterest cookies")
                except Exception as e:
                    log(f"cookie load failed: {e}")

            log(f"opening {args.urls[0]}")
            page.goto(args.urls[0], wait_until="domcontentloaded", timeout=60000)
            shot("load")

            # ---- login (only when --allow-anon is off) ----
            ok, st = is_logged_in(page, ctx)
            if not ok and not args.allow_anon:
                log("LOGIN REQUIRED -> please log in to Pinterest in the browser "
                    f"(waiting {args.wait_login}s). First time only, session is saved.")
                deadline = time.time() + args.wait_login
                while time.time() < deadline:
                    time.sleep(3)
                    try:
                        if "/login" in (page.url or ""):
                            page.goto(args.urls[0], wait_until="domcontentloaded",
                                      timeout=60000)
                            page.wait_for_timeout(2500)
                        ok, st = is_logged_in(page, ctx)
                    except Exception:
                        continue
                    if ok:
                        log("login detected ✅")
                        break
                if not ok:
                    try:
                        page.goto(args.urls[0], wait_until="domcontentloaded",
                                  timeout=60000)
                        page.wait_for_timeout(3500)
                        ok, st = is_logged_in(page, ctx)
                    except Exception:
                        pass
            if not ok and not args.allow_anon:
                shot("nologin")
                print(json.dumps({"error": "login_required",
                                  "detail": ("No Pinterest session found - log in "
                                             "in the headed browser. state="
                                             + json.dumps(st)[:220])}))
                return 3

            # ---- scrape every URL ----
            pins, seen = [], set()
            for u in args.urls:
                log(f"=== {u} ===")
                try:
                    n = scrape_url(page, u, args.scroll, pins, seen)
                    log(f"  -> {n} new")
                except Exception as e:
                    log(f"  url failed: {e}")

            shot("feed")
            print(json.dumps({"ok": True, "logged_in": ok,
                              "count": len(pins), "pins": pins}))
            return 0
    except Exception as e:
        print(json.dumps({"error": f"scrape failed: {e}"}))
        return 1
    finally:
        try:
            if ctx:
                ctx.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
