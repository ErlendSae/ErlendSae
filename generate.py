#!/usr/bin/env python3
"""
neofetch-style profile card generator
-------------------------------------
Reads config.json, converts an image to ASCII art, optionally pulls live
GitHub stats, and writes an SVG you can embed in your profile README.

    python generate.py                  # uses config.json
    python generate.py --config me.json --out card.svg
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.request
from html import escape
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

# ---------------------------------------------------------------- ascii art

# Dark -> light. Reverse this if your image has a light background.
RAMPS = {
    "blocks": " .:-=+*#%@",
    "dense": " .'`^\",:;Il!i><~+_-?][}{1)(|\\/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$",
    "minimal": " .:*#@",
    "shade": " ░▒▓█",
}

# Must match the char/line spacing render_svg() actually draws with, or ascii
# rows get generated for a cell ratio that isn't the one they're displayed at
# (image comes out stretched). Default char_aspect below is derived from these.
CHAR_WIDTH_RATIO = 0.6  # of font_size
LINE_HEIGHT_RATIO = 1.45  # of font_size
DEFAULT_CHAR_ASPECT = CHAR_WIDTH_RATIO / LINE_HEIGHT_RATIO


def load_image(src: str) -> Image.Image:
    if src.startswith(("http://", "https://")):
        req = urllib.request.Request(src, headers={"User-Agent": "neofetch-readme"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return Image.open(io.BytesIO(resp.read()))
    return Image.open(src)


def image_to_ascii(
    src: str,
    width: int = 44,
    ramp: str = "blocks",
    invert: bool = False,
    contrast: float = 1.0,
    char_aspect: float = DEFAULT_CHAR_ASPECT,
    denoise: int = 3,
) -> list[str]:
    """Return a list of strings, one per row of ASCII art."""
    chars = RAMPS.get(ramp, ramp)  # allow passing a literal ramp string
    img = load_image(src)

    # Flatten transparency onto black so cut-out avatars keep their silhouette.
    fg_mask = None
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        # Cut-out subjects only cover part of the frame; letting the huge flat
        # background into the contrast stretch skews it and blows out the
        # subject's highlights. Restrict the stretch to the foreground.
        fg_mask = img.getchannel("A").point(lambda a: 255 if a > 128 else 0)
        bg = Image.new("RGBA", img.size, (0, 0, 0, 255))
        img = Image.alpha_composite(bg, img)

    img = img.convert("L")

    # Median denoise before downsampling: smooths skin pores / JPEG speckle so
    # the ramp maps clean tone instead of noise. Matters most for the long
    # "dense" ramp, whose many levels otherwise turn texture into visual grit.
    if denoise and denoise >= 3 and denoise % 2 == 1:
        img = img.filter(ImageFilter.MedianFilter(denoise))

    if contrast != 1.0:
        # Global tonal stretch over the subject. `contrast` sets how much of
        # each tail to clip before stretching (more clip = punchier). The
        # dense ramp supplies the fine tonal gradation that renders facial
        # structure; a whole-image stretch keeps it clean rather than noisy.
        cutoff = max(0.0, (contrast - 1.0) * 5.0)
        img = ImageOps.autocontrast(img, cutoff=cutoff, mask=fg_mask)

    if invert:
        img = ImageOps.invert(img)

    w, h = img.size
    height = max(1, int(width * (h / w) * char_aspect))
    img = img.resize((width, height), Image.LANCZOS)
    px = img.load()
    n = len(chars) - 1
    rows = []
    for y in range(height):
        rows.append("".join(chars[int(px[x, y] / 255 * n)] for x in range(width)))
    return rows


# ------------------------------------------------------------ github stats


def gh_json(url: str, token: str | None):
    headers = {"User-Agent": "neofetch-readme", "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def fetch_stats(username: str, token: str | None) -> dict:
    """Best-effort stats. Never raises - missing keys just render as '?'."""
    stats: dict[str, str] = {}
    try:
        user = gh_json(f"https://api.github.com/users/{username}", token)
        stats["followers"] = f"{user.get('followers', 0):,}"
        stats["following"] = f"{user.get('following', 0):,}"
        stats["repos"] = str(user.get("public_repos", 0))
        stats["name"] = user.get("name") or username
        stats["bio"] = user.get("bio") or ""
    except Exception as e:  # noqa: BLE001
        print(f"  ! user lookup failed: {e}", file=sys.stderr)

    try:
        stars = 0
        langs: dict[str, int] = {}
        page = 1
        while page <= 5:
            batch = gh_json(
                f"https://api.github.com/users/{username}/repos"
                f"?per_page=100&page={page}&type=owner",
                token,
            )
            if not batch:
                break
            for r in batch:
                if r.get("fork"):
                    continue
                stars += r.get("stargazers_count", 0)
                if r.get("language"):
                    langs[r["language"]] = langs.get(r["language"], 0) + 1
            page += 1
        stats["stars"] = f"{stars:,}"
        stats["top_languages"] = ", ".join(
            k for k, _ in sorted(langs.items(), key=lambda kv: -kv[1])[:5]
        )
    except Exception as e:  # noqa: BLE001
        print(f"  ! repo scan failed: {e}", file=sys.stderr)

    return stats


def substitute(text: str, stats: dict) -> str:
    """Replace {{stars}} style placeholders with fetched values."""
    for key, val in stats.items():
        text = text.replace("{{" + key + "}}", str(val))
    # Anything left unresolved becomes a dash rather than raw braces.
    while "{{" in text and "}}" in text:
        start = text.index("{{")
        end = text.index("}}", start) + 2
        text = text[:start] + "?" + text[end:]
    return text


# ----------------------------------------------------------------- svg out

DEFAULT_THEME = {
    "bg": "#0d1117",
    "chrome": "#161b22",
    "border": "#30363d",
    "ascii": "#7aa2f7",
    "title": "#f7768e",
    "rule": "#30363d",
    "section": "#9ece6a",
    "key": "#7dcfff",
    "dots": "#2b3340",
    "value": "#c0caf5",
    "accent": "#e0af68",
}


def build_lines(cfg: dict, stats: dict) -> list[tuple[str, str, str]]:
    """Flatten config sections into (kind, left, right) tuples."""
    out: list[tuple[str, str, str]] = []
    out.append(("title", substitute(cfg.get("title", "you@github"), stats), ""))
    out.append(("rule", "", ""))
    for section in cfg.get("sections", []):
        if section.get("heading"):
            out.append(("section", section["heading"], ""))
        for item in section.get("items", []):
            key, value = (item + ["", ""])[:2] if isinstance(item, list) else (
                item.get("key", ""),
                item.get("value", ""),
            )
            out.append(("item", key, substitute(str(value), stats)))
        out.append(("gap", "", ""))
    while out and out[-1][0] == "gap":
        out.pop()
    return out


def render_svg(ascii_rows: list[str], lines, theme: dict, opts: dict) -> str:
    fs = opts.get("font_size", 13)
    lh = round(fs * LINE_HEIGHT_RATIO, 2)
    ch = fs * CHAR_WIDTH_RATIO  # monospace advance width
    pad = 24
    gutter = 28

    # The art uses its own font-size, independent of the info panel's. By
    # default ("fit") it's auto-scaled so the art's block height exactly
    # matches the info panel's height - the art has a fixed number of rows
    # (set by width x aspect) that rarely equals the number of info lines, so
    # without this one side leaves blank vertical space beside the other.
    # A numeric art_font_size overrides the fit (e.g. to shrink dense/braille
    # art below the readable text size).
    text_h = len(lines) * lh
    art_rows = len(ascii_rows)
    art_fs = opts.get("art_font_size", "fit")
    if art_fs == "fit":
        art_fs = (text_h / (art_rows * LINE_HEIGHT_RATIO)) if art_rows else fs
    art_lh = round(art_fs * LINE_HEIGHT_RATIO, 2)
    art_ch = art_fs * CHAR_WIDTH_RATIO

    art_w = max((len(r) for r in ascii_rows), default=0) * art_ch
    art_h = art_rows * art_lh
    dot_col = opts.get("key_column", 26)  # column where values start
    text_w = max(
        (dot_col + len(r) + 2) * ch for _, _, r in lines
    ) if lines else 300
    text_w = max(text_w, 46 * ch)

    body_w = pad * 2 + art_w + gutter + text_w
    body_h = pad * 2 + max(art_h, text_h) + 34  # + title bar

    art_x = pad
    txt_x = pad + art_w + gutter
    top = 34 + pad

    font = (
        "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, "
        "'DejaVu Sans Mono', 'Liberation Mono', monospace"
    )

    p: list[str] = []
    p.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{body_w:.0f}" '
        f'height="{body_h:.0f}" viewBox="0 0 {body_w:.0f} {body_h:.0f}" '
        f'font-family="{font}" font-size="{fs}" xml:space="preserve" '
        f'role="img" aria-label="neofetch style profile card">'
    )
    p.append(
        f'<rect width="100%" height="100%" rx="10" fill="{theme["bg"]}" '
        f'stroke="{theme["border"]}"/>'
    )
    if opts.get("window_chrome", True):
        p.append(
            f'<path d="M0 10a10 10 0 0 1 10-10h{body_w - 20:.0f}a10 10 0 0 1 10 10v24H0z" '
            f'fill="{theme["chrome"]}"/>'
        )
        label = escape(opts.get("window_title", "~/profile"))
        p.append(
            f'<text x="{body_w / 2:.0f}" y="21" fill="{theme["border"]}" '
            f'font-size="{fs - 1}" text-anchor="middle">{label}</text>'
        )

    # ASCII art
    # Leading/embedded spaces carry the shape (they center narrower rows like
    # the top of the head). Some renderers collapse plain spaces in <text>
    # despite xml:space="preserve" (e.g. when the SVG is parsed through an
    # HTML pipeline), which shoves every row left by a different amount and
    # warps the image. Non-breaking spaces can't be collapsed, so use those.
    for i, row in enumerate(ascii_rows):
        y = top + i * art_lh + art_fs
        # textLength pins the row to the width we calculated the layout for,
        # regardless of the actual font's advance width for these glyphs
        # (e.g. Braille Patterns can render wider than the ASCII ramp in
        # whatever font a given viewer substitutes), which would otherwise
        # push rows into the info panel.
        p.append(
            f'<text x="{art_x:.0f}" y="{y:.1f}" fill="{theme["ascii"]}" '
            f'opacity="0.95" font-size="{art_fs}" textLength="{art_w:.1f}" '
            f'lengthAdjust="spacingAndGlyphs">'
            f'{escape(row).replace(chr(32), chr(0xa0))}</text>'
        )

    # Info panel
    for i, (kind, left, right) in enumerate(lines):
        y = top + i * lh + fs
        if kind == "gap":
            continue
        if kind == "title":
            p.append(
                f'<text x="{txt_x:.0f}" y="{y:.1f}" fill="{theme["title"]}" '
                f'font-weight="bold">{escape(left)}</text>'
            )
        elif kind == "rule":
            p.append(
                f'<text x="{txt_x:.0f}" y="{y:.1f}" fill="{theme["rule"]}">'
                f'{"─" * int(text_w / ch)}</text>'
            )
        elif kind == "section":
            head = f"─ {left} "
            head += "─" * max(0, int(text_w / ch) - len(head))
            p.append(
                f'<text x="{txt_x:.0f}" y="{y:.1f}" fill="{theme["section"]}">'
                f'{escape(head)}</text>'
            )
        else:
            dots = "." * max(1, dot_col - len(left) - 1)
            p.append(f'<text x="{txt_x:.0f}" y="{y:.1f}">')
            p.append(f'<tspan fill="{theme["key"]}">{escape(left)}</tspan>')
            p.append(f'<tspan fill="{theme["dots"]}"> {escape(dots)} </tspan>')
            p.append(f'<tspan fill="{theme["value"]}">{escape(right)}</tspan>')
            p.append("</text>")

    p.append("</svg>")
    return "".join(p)


# -------------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    out_path = Path(args.out or cfg.get("output", "profile-card.svg"))

    stats: dict = {}
    username = cfg.get("github_username")
    if username and cfg.get("fetch_stats", True):
        print(f"-> fetching stats for {username}")
        stats = fetch_stats(username, os.environ.get("GITHUB_TOKEN"))

    art_cfg = cfg.get("ascii", {})
    source = art_cfg.get("source")
    if source and source.startswith("{{avatar}}") and username:
        source = f"https://github.com/{username}.png?size=460"
    if source:
        print(f"-> rendering ascii from {source}")
        rows = image_to_ascii(
            source,
            width=art_cfg.get("width", 44),
            ramp=art_cfg.get("ramp", "blocks"),
            invert=art_cfg.get("invert", False),
            contrast=art_cfg.get("contrast", 1.0),
            char_aspect=art_cfg.get("char_aspect", DEFAULT_CHAR_ASPECT),
            denoise=art_cfg.get("denoise", 3),
        )
    else:
        rows = art_cfg.get("literal", [])

    theme = {**DEFAULT_THEME, **cfg.get("theme", {})}
    svg = render_svg(rows, build_lines(cfg, stats), theme, cfg.get("layout", {}))
    out_path.write_text(svg, encoding="utf-8")
    print(f"-> wrote {out_path} ({len(svg) / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
