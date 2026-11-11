"""Icon preparation, provenance and small-size QA artifacts."""
import hashlib
import io
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .model import IconError, chat_id, save, validate_manifest, validate_topics


def checked_svg(raw):
    if len(raw) > 1_000_000 or re.search(br"<!DOCTYPE|<!ENTITY", raw, re.I):
        raise IconError("Oversize SVG or forbidden DTD/entity")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise IconError("Invalid SVG") from None
    if root.tag != "{http://www.w3.org/2000/svg}svg":
        raise IconError("Expected namespaced SVG")
    forbidden = {"script", "foreignObject", "image", "style", "iframe", "audio", "video", "animate", "animateTransform", "set", "feImage"}
    for node in root.iter():
        if node.tag.split("}")[-1] in forbidden:
            raise IconError("Active or external SVG content is not allowed")
        for key, value in node.attrib.items():
            key = key.split("}")[-1].lower()
            if key.startswith("on") or (key in ("href", "src") and not value.startswith("#")):
                raise IconError("Unsafe SVG attribute")
            if re.search(r'url\s*\(\s*[\"\']?(?!#)', value, re.I):
                raise IconError("External SVG CSS reference")
    return raw


def read_image(path):
    if path.stat().st_size > 4_000_000:
        raise IconError("Source asset exceeds 4 MB")
    if path.suffix.lower() == ".svg":
        raw = checked_svg(path.read_bytes())
        renderer = shutil.which("rsvg-convert")
        if not renderer:
            raise IconError("SVG input needs rsvg-convert, or supply a raster asset")
        try:
            result = subprocess.run([renderer, "--width", "400", "--height", "400"], input=raw,
                                    capture_output=True, timeout=30, check=True)
        except (subprocess.SubprocessError, OSError):
            raise IconError("SVG rasterization failed; do not weaken validation") from None
        source = io.BytesIO(result.stdout)
    else:
        source = path
    try:
        with Image.open(source) as image:
            if image.width * image.height > 16_000_000 or getattr(image, "n_frames", 1) != 1:
                raise IconError("Static assets up to 16 megapixels only")
            image.load()
            return image.convert("RGBA")
    except (OSError, ValueError):
        raise IconError("Asset is not a supported static image") from None


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepared_integrity(manifest):
    validate_manifest(manifest)
    pixels = set()
    for row in manifest["icons"]:
        path = Path(row["path"])
        if file_digest(path) != row["file_sha256"]:
            raise IconError("Prepared file changed since rendering")
        image = read_image(path)
        if image.size != (100, 100) or path.stat().st_size > 512_000:
            raise IconError("Custom emoji must be 100x100 and under 512 kB")
        digest = hashlib.sha256(image.tobytes()).hexdigest()
        if digest != row["pixel_sha256"] or digest in pixels:
            raise IconError("Prepared pixels changed or duplicate decoded images")
        if not image.getchannel("A").getbbox():
            raise IconError("Prepared icon is entirely transparent")
        pixels.add(digest)


def prepare(inventory, design, assets_root, out):
    target = chat_id(inventory["chat_id"])
    topics = validate_topics(inventory["topics"])
    if design["chat_id"] != target:
        raise IconError("Design chat differs from inventory")
    rows = design["icons"]
    if len(rows) != len(topics) - 1 or {r["topic_id"] for r in rows} != set(topics) - {1}:
        raise IconError("Design must cover every editable topic exactly once, excluding General")
    root = Path(assets_root).resolve()
    out = Path(out).resolve()
    if out.exists() and any(out.iterdir()):
        raise IconError("Rendering output must be new or empty; preserve existing run state")
    out.mkdir(parents=True, mode=0o700, exist_ok=True)
    icons = []
    for row in rows:
        relative = Path(row["asset"])
        if relative.is_absolute() or ".." in relative.parts:
            raise IconError("Asset paths must stay inside the declared assets root")
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise IconError("Missing asset or asset escaped the declared root")
        if row.get("kind") not in ("brand", "thematic") or not row.get("design_id"):
            raise IconError("Every asset needs kind and distinct design_id")
        provenance = row.get("provenance", {})
        if not provenance.get("source") or not provenance.get("rights"):
            raise IconError("Every asset needs source and rights metadata")
        if row["kind"] == "brand" and not provenance.get("identity"):
            raise IconError("Brand assets require exact company/product identity")
        image = read_image(path)
        # Fit without stretching, recoloring or cropping the supplied mark.
        image = ImageOps.contain(image, (100, 100), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (100, 100))
        canvas.alpha_composite(image, ((100 - image.width) // 2, (100 - image.height) // 2))
        tid = row["topic_id"]
        destination = out / (str(tid) + ".webp")
        canvas.save(destination, "WEBP", lossless=True)
        topic = topics[tid]
        icons.append({"topic_id": tid, "title": topic["title"], "old_icon": topic["icon_custom_emoji_id"],
                      "design_id": row["design_id"], "kind": row["kind"], "provenance": provenance,
                      "source_sha256": file_digest(path), "path": str(destination),
                      "file_sha256": file_digest(destination), "pixel_sha256": hashlib.sha256(canvas.tobytes()).hexdigest()})
    manifest = {"schema": 1, "chat_id": target, "topics": inventory["topics"], "icons": icons}
    prepared_integrity(manifest)
    save(out / "manifest.json", manifest)
    previews(manifest, out)
    return manifest


def previews(manifest, out):
    out = Path(out)
    by_id = {row["topic_id"]: row for row in manifest["icons"]}
    topics = manifest["topics"]
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 14)
    except OSError:
        font = ImageFont.load_default(size=14)
    # Paginated labelled sheets avoid silently omitting historical topics.
    for start in range(0, len(topics), 48):
        page = topics[start:start + 48]
        measuring = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        wrapped = {}
        for topic in page:
            lines = [""]
            for char in topic["title"]:
                if measuring.textlength(lines[-1] + char, font=font) > 184:
                    lines.append("")
                lines[-1] += char
            wrapped[topic["id"]] = lines
        cell_height = max(130, 85 + 17 * max(len(v) for v in wrapped.values()))
        sheet = Image.new("RGB", (1200, cell_height * 8), "#172033")
        draw = ImageDraw.Draw(sheet)
        for j, topic in enumerate(topics[start:start + 48]):
            x = (j % 6) * 200
            y = (j // 6) * cell_height
            if topic["id"] in by_id:
                icon = read_image(Path(by_id[topic["id"]]["path"])).resize((62, 62), Image.Resampling.LANCZOS)
                sheet.paste(icon, (x + 69, y + 4), icon)
            else:
                draw.text((x + 55, y + 25), "SYSTEM ICON", font=font, fill="#9CA3AF")
            # Wrap by measured width; retain every character of the exact title.
            lines = wrapped[topic["id"]]
            for k, line in enumerate(lines):
                draw.text((x + 8, y + 72 + k * 17), line, font=font, fill="white")
        sheet.save(out / ("contact-" + str(start // 48 + 1) + ".png"))
    # This is a mockup, not a claimed Telegram screenshot.
    for start in range(0, len(topics), 30):
        measuring = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        width = max(700, 60 + int(max(measuring.textlength(t["title"], font=font) for t in topics[start:start + 30])))
        sheet = Image.new("RGB", (width, 1140), "#17212D")
        draw = ImageDraw.Draw(sheet)
        draw.text((12, 8), "22px LIST MOCKUP — General retains its actual system icon", font=font, fill="#A8B4C5")
        for j, topic in enumerate(topics[start:start + 30]):
            y = 42 + j * 36
            if topic["id"] in by_id:
                icon = read_image(Path(by_id[topic["id"]]["path"])).resize((22, 22), Image.Resampling.LANCZOS)
                sheet.paste(icon, (12, y), icon)
            else:
                draw.ellipse((14, y + 2, 32, y + 20), outline="#9CA3AF", width=2)
            draw.text((46, y + 1), topic["title"], font=font, fill="white")
        sheet.save(out / ("list-mockup-" + str(start // 30 + 1) + ".png"))
