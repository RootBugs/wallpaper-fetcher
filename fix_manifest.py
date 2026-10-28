"""Manifest repair: disk pe maujood images jo manifest me NAHI hain unhe add karo.

Kyun zaroori: run 2 17:16 pe kill ho gaya (server mar gaya) -> uski 585 images
disk pe hain par manifest me nahi. Isse do problem hoti:
  * agla run seq = len(old_rows) = 2000 leta -> nature-2001.jpg par DOBARA likhta
    (purani 585 files overwrite) 
  * unka ahash/ID skip nahi hota -> visual duplicate bante
Id/source to kho chuka hai (manifest hi nahi bana), isliye id="" + ahash se
dedupe cover karte hain.
"""
import csv, os, sys
from PIL import Image

FOLDER = r"C:\Users\hp\Pictures\nature-7000"
MF = os.path.join(FOLDER, "manifest.csv")
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")


def ahash(img):
    # EXACTLY tool ke ahash() jaisa (bina resample arg -> wahi default filter),
    # warna hash match nahi karega aur cross-run dedupe kaam nahi karega.
    g = img.convert("L").resize((8, 8))
    px = list(g.getdata())
    avg = sum(px) / 64
    return sum((1 << i) for i, v in enumerate(px) if v > avg)


def main():
    with open(MF, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    have = {r.get("file") for r in rows}
    files = sorted(n for n in os.listdir(FOLDER) if n.lower().endswith(IMG_EXT))
    missing = [n for n in files if n not in have]
    print(f"manifest={len(rows)}  disk={len(files)}  missing={len(missing)}")

    if not missing:
        print("kuch missing nahi - repair ki zaroorat nahi")
        return

    added = 0
    for n in missing:
        p = os.path.join(FOLDER, n)
        try:
            with Image.open(p) as im:
                w, h = im.size
                hh = ahash(im)
        except Exception as e:
            print(f"  skip {n}: {e}")
            continue
        rows.append({
            "file": n, "resolution": f"{w}x{h}", "brightness_L": "",
            "hue": "", "tint": "", "complexity_edge": "", "dark_frac": "",
            "saturation": "", "score": "", "keywords": "", "source": "",
            "license": "", "landing": "", "id": "", "ahash": format(hh, "x"),
        })
        added += 1

    # fieldnames = UNION (purani rows me ahash ho bhi na ho)
    fields = list(rows[0].keys())
    for r in rows[1:]:
        for k in r:
            if k not in fields:
                fields.append(k)
    tmp = MF + ".bak"
    os.replace(MF, tmp)
    with open(MF, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"added={added}  total={len(rows)}  (backup: {os.path.basename(tmp)})")


if __name__ == "__main__":
    main()
