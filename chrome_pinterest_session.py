"""
Extracts Pinterest cookies from Chrome and saves them to pin_session.json.

BEFORE RUNNING: close Chrome (otherwise the cookies DB stays locked).

- v10 cookies -> decrypted with DPAPI + AES-GCM (works)
- v20 cookies -> Chrome App-Bound Encryption, cannot be decrypted from outside
                  (in that case the tool drives your installed Chrome instead -
                   see the .bat file)

Output: pin_session.json  (auto-loaded by pin_home_feed.py)
"""
import base64, ctypes, ctypes.wintypes, json, os, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
UD = r"C:\Users\hp\AppData\Local\Google\Chrome\User Data"
LOCAL_STATE = os.path.join(UD, "Local State")
COOKIES = os.path.join(UD, "Default", "Network", "Cookies")
OUT = os.path.join(HERE, "pin_session.json")

# Chromium epoch (1601-01-01) -> Unix epoch
_CHROME_EPOCH = 11644473600


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi_decrypt(data: bytes) -> bytes:
    bi = DATA_BLOB(len(data), ctypes.create_string_buffer(data, len(data)))
    bo = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(bi), None, None, None, None, 0, ctypes.byref(bo)):
        raise OSError("DPAPI failed")
    out = ctypes.string_at(bo.pbData, bo.cbData)
    ctypes.windll.kernel32.LocalFree(bo.pbData)
    return out


def aesgcm_decrypt(key: bytes, ev: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(key).decrypt(ev[3:15], ev[15:], None)


def main():
    if not os.path.exists(COOKIES):
        print("ERROR: Chrome cookies database not found:", COOKIES)
        return 1
    try:
        open(COOKIES, "rb").close()
    except PermissionError:
        print("LOCKED: Chrome is running -> close it first, then run this again.")
        return 2

    ls = json.load(open(LOCAL_STATE, encoding="utf-8"))
    ek = base64.b64decode(ls["os_crypt"]["encrypted_key"])
    master = dpapi_decrypt(ek[5:])

    con = sqlite3.connect(f"file:{COOKIES}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT name, host_key, path, value, encrypted_value, expires_utc, "
        "is_secure, is_httponly, has_expires, samesite "
        "FROM cookies WHERE host_key LIKE '%pinterest%'"
    ).fetchall()
    con.close()

    if not rows:
        print("NO_COOKIES: no Pinterest cookies found "
              "-> are you logged in to Pinterest in Chrome?")
        return 3

    out, v20, failed = [], [], []
    for (name, host, path, plain, ev, exp, secure, httponly, has_exp, ss) in rows:
        val = None
        if plain:
            val = plain
        elif ev:
            pre = ev[:3]
            if pre == b"v10":
                try:
                    val = aesgcm_decrypt(master, ev).decode("utf-8", "replace")
                except Exception as e:
                    failed.append(f"{name}({e})")
            elif pre == b"v20":
                v20.append(name)
            else:
                failed.append(f"{name}(unknown prefix {pre!r})")
        if val is None:
            continue
        c = {"name": name, "value": val, "domain": host, "path": path or "/"}
        if secure:
            c["secure"] = True
        if httponly:
            c["httpOnly"] = True
        if has_exp and exp and exp > 0:
            c["expires"] = exp / 1_000_000 - _CHROME_EPOCH
        if ss == 1:
            c["sameSite"] = "Lax"
        elif ss == 2:
            c["sameSite"] = "Strict"
        out.append(c)

    print(f"cookies total={len(rows)} decoded={len(out)} v20={len(v20)} failed={len(failed)}")
    if v20:
        print("V20_ABE:", ",".join(v20))
    if failed:
        print("FAILED:", "; ".join(failed)[:300])

    sess = [c for c in out if c["name"] in ("_pinterest_sess", "_auth", "csrftoken")]
    if not sess:
        print("NO_SESSION: _pinterest_sess / _auth / csrftoken not found.")
        return 4
    auth = next((c for c in out if c["name"] == "_auth"), None)
    print("_auth =", auth["value"] if auth else "(missing)")
    if auth and auth["value"] in ("0", "", None):
        print("WARN: _auth=0 -> you are NOT logged in to Pinterest in Chrome.")

    json.dump(out, open(OUT, "w", encoding="utf-8"), indent=1)
    print("SAVED:", OUT, f"({len(out)} cookies)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
