"""Convert cached trader post images to WebP and update JSON references."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
IMAGES_DIR = ROOT / "data" / "trader_post_images"
POSTS_JSON = ROOT / "data" / "trader_posts_romainroth.json"
SRC_EXTS = {".png", ".jpg", ".jpeg", ".gif"}


def _save_webp_from_path(src: Path, out: Path) -> bool:
    try:
        with Image.open(src) as img:
            if getattr(img, "is_animated", False):
                img.seek(0)
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
            else:
                img = img.convert("RGB")
            img.save(out, format="WEBP", quality=85, method=6)
        return out.is_file()
    except Exception:
        return False


def main() -> None:
    if not IMAGES_DIR.is_dir():
        raise SystemExit(f"Images dir not found: {IMAGES_DIR}")
    if not POSTS_JSON.is_file():
        raise SystemExit(f"Posts JSON not found: {POSTS_JSON}")

    converted = 0
    name_map: dict[str, str] = {}

    for p in sorted(IMAGES_DIR.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() not in SRC_EXTS:
            continue
        out = p.with_suffix(".webp")
        if out.is_file():
            name_map[p.name] = out.name
            p.unlink(missing_ok=True)
            converted += 1
            continue
        if _save_webp_from_path(p, out):
            name_map[p.name] = out.name
            p.unlink(missing_ok=True)
            converted += 1

    data = json.loads(POSTS_JSON.read_text(encoding="utf-8"))
    posts = data.get("posts") if isinstance(data, dict) else None
    if isinstance(posts, list):
        for item in posts:
            if not isinstance(item, dict):
                continue
            image_file = str(item.get("image_file") or "").strip()
            if not image_file:
                continue
            new_name = name_map.get(image_file) or Path(image_file).with_suffix(".webp").name
            if (IMAGES_DIR / new_name).is_file():
                item["image_file"] = new_name
                item["image_url"] = f"/api/trader-post-image/{new_name}"

    POSTS_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Converted / remapped: {converted}")
    print("Updated", POSTS_JSON)


if __name__ == "__main__":
    main()
