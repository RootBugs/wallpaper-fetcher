"""Manifest repair: add any image that exists on disk but is missing from manifest.csv.

Why this matters: if the server is killed mid-run, its images stay on disk while the
manifest is never written. That causes two problems on the next run:
  * the next run takes seq = len(old_rows) and writes to nature-2001.jpg again
    (the 585 already-saved files get overwritten)
  * their ahash/ID are unknown, so visual duplicates get downloaded again
The id/source are lost at that point (the manifest was never written), so rows are
recovered with id="" and dedupe falls back to the ahash.
"""
import csv, os, sys
from PIL import Image

FOLDER = r"C:\Users\hp\Pictures\nature-7000"
MF = os.path.join(FOLDER, "manifest.csv")
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")


def ahash(img):
    # Must match the tool's ahash() EXACTLY (no resample argument -> same default
    # filter), otherwise the hashes will not match and cross-run dedupe won't work.
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
        print("nothing missing - no repair needed")
        return

    added = 0
    for n in missing:
        p = os.path.join(FOLDER, n)
        try:
            with Image.open(p) as im:
                w, h = im.size
                hh = ahash(im)
        except Exception as e:
            print(f"  skipped {n}: {e}")
            continue
        rows.append({
            "file": n, "resolution": f"{w}x{h}", "brightness_L": "",
            "hue": "", "tint": "", "complexity_edge": "", "dark_frac": "",
            "saturation": "", "score": "", "keywords": "", "source": "",
            "license": "", "landing": "", "id": "", "ahash": format(hh, "x"),
        })
        added += 1

    # fieldnames = UNION of every key (older rows may not have an ahash column)
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
