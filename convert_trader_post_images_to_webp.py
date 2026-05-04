"""Convert cached trader post images to WebP and update JSON references."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
IMAGES_DIR = ROOT / "data" / "trader_post_images"
POSTS_JSON = ROOT / "data" / "trader_posts_romainroth.json"
SRC_EXTS = {".png", ".jpg", ".jpeg", ".gif"}
# "200 KB" strict in decimal bytes.
MAX_WEBP_BYTES = 200_000
QUALITY_STEPS = (85, 80, 75, 70, 65, 60, 55, 50, 45, 40, 35, 30, 25)
SCALE_STEPS = (1.0, 0.92, 0.85, 0.78, 0.72, 0.66, 0.60, 0.54)


def _load_base_image(src: Path) -> Image.Image:
    with Image.open(src) as img:
        if getattr(img, "is_animated", False):
            img.seek(0)
        if img.mode in ("RGBA", "LA", "P"):
            return img.convert("RGBA")
        return img.convert("RGB")


def _save_webp_bounded(img: Image.Image, out: Path, max_bytes: int = MAX_WEBP_BYTES) -> bool:
    base_w, base_h = img.size
    for scale in SCALE_STEPS:
        if scale == 1.0:
            candidate = img
        else:
            new_w = max(1, int(base_w * scale))
            new_h = max(1, int(base_h * scale))
            candidate = img.resize((new_w, new_h), Image.LANCZOS)
        for quality in QUALITY_STEPS:
            candidate.save(out, format="WEBP", quality=quality, method=6)
            try:
                if out.stat().st_size <= max_bytes:
                    return True
            except FileNotFoundError:
                return False
    # Hard fallback: keep shrinking until under target.
    w, h = img.size
    while w > 32 and h > 32:
        w = max(32, int(w * 0.9))
        h = max(32, int(h * 0.9))
        candidate = img.resize((w, h), Image.LANCZOS)
        candidate.save(out, format="WEBP", quality=20, method=6)
        try:
            if out.stat().st_size <= max_bytes:
                return True
        except FileNotFoundError:
            return False
    # Last resort: keep smallest candidate we could produce.
    return out.is_file()


def _save_webp_from_path(src: Path, out: Path) -> bool:
    try:
        img = _load_base_image(src)
        return _save_webp_bounded(img, out)
    except Exception:
        return False


def main() -> None:
    if not IMAGES_DIR.is_dir():
        raise SystemExit(f"Images dir not found: {IMAGES_DIR}")
    if not POSTS_JSON.is_file():
        raise SystemExit(f"Posts JSON not found: {POSTS_JSON}")

    converted = 0
    optimized_webp = 0
    name_map: dict[str, str] = {}

    for p in sorted(IMAGES_DIR.iterdir()):
        if not p.is_file():
            continue
        suffix = p.suffix.lower()
        if suffix == ".webp":
            try:
                size_before = p.stat().st_size
            except FileNotFoundError:
                continue
            if size_before > MAX_WEBP_BYTES and _save_webp_from_path(p, p):
                try:
                    if p.stat().st_size < size_before:
                        optimized_webp += 1
                except FileNotFoundError:
                    pass
            continue
        if suffix not in SRC_EXTS:
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
    print(f"Optimized existing .webp >200KB: {optimized_webp}")
    print("Updated", POSTS_JSON)


if __name__ == "__main__":
    main()
