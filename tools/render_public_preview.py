"""使用公开 CC BY-NC 演示帧生成仓库首页预览图。

脚本只读取 assets/pet，输出稳定的 1600×900 PNG，不访问网络或私有素材。

运行方式：
    python tools/render_public_preview.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "assets" / "onepic-showcase.png"
SIZE = (1600, 900)
FRAME_SIZE = (300, 470)
INK = "#EAF2FF"
MUTED = "#9EB1CA"
ACCENT = "#77D5C8"

FRAMES = (
    ("IDLE", ROOT / "assets/pet/idle/idle_01.png"),
    ("WALK", ROOT / "assets/pet/walk/walk_03.png"),
    ("INTERACT", ROOT / "assets/pet/interact/wave_01.png"),
    ("SLEEP", ROOT / "assets/pet/sleep/sleep_05.png"),
)


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """优先使用 Windows UI 字体，不可用时回退 Pillow 默认字体。"""

    candidates = (
        Path("C:/Windows/Fonts/seguisb.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
        Path(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ),
    )
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def fit_character(path: Path) -> Image.Image:
    """按透明内容框缩放角色，保持完整轮廓与一致视觉高度。"""

    image = Image.open(path).convert("RGBA")
    bbox = image.getbbox()
    if bbox is None:
        raise ValueError(f"透明空帧：{path}")
    image = image.crop(bbox)
    scale = min(FRAME_SIZE[0] / image.width, FRAME_SIZE[1] / image.height)
    return image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )


def main() -> int:
    """渲染首页预览并返回进程状态。"""

    canvas = Image.new("RGB", SIZE, "#0B1220")
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (40, 32, 1560, 868),
        radius=34,
        fill="#111C30",
        outline="#263955",
        width=2,
    )
    draw.text((82, 62), "ONEPIC DESKTOP PET", font=font(50, bold=True), fill=INK)
    draw.text(
        (84, 124),
        "Authorized art  ·  38+2 frame recipe  ·  human approval  ·  privacy-safe release",
        font=font(23),
        fill=MUTED,
    )

    card_left = 70
    card_top = 188
    card_width = 350
    gap = 20
    for index, (label, path) in enumerate(FRAMES):
        left = card_left + index * (card_width + gap)
        right = left + card_width
        draw.rounded_rectangle(
            (left, card_top, right, 690),
            radius=24,
            fill=("#182843" if index % 2 == 0 else "#16253D"),
            outline="#314966",
            width=2,
        )
        character = fit_character(path)
        x = left + (card_width - character.width) // 2
        y = 208 + (455 - character.height)
        canvas.paste(character, (x, y), character)
        draw.rounded_rectangle(
            (left + 14, card_top + 12, left + 190, card_top + 58),
            radius=12,
            fill="#132139",
        )
        draw.text(
            (left + 24, card_top + 20),
            f"0{index + 1}  {label}",
            font=font(21, bold=True),
            fill=ACCENT,
        )

    steps = ("SOURCE ART", "CHARACTER APPROVAL", "ANIMATION REVIEW", "QA + RELEASE")
    step_width = 315
    start_x = 105
    y = 750
    for index, label in enumerate(steps):
        left = start_x + index * 370
        draw.rounded_rectangle(
            (left, y, left + step_width, y + 64),
            radius=16,
            fill="#203553",
            outline="#41617F",
            width=2,
        )
        text_box = draw.textbbox((0, 0), label, font=font(17, bold=True))
        text_width = text_box[2] - text_box[0]
        draw.text(
            (left + (step_width - text_width) / 2, y + 20),
            label,
            font=font(17, bold=True),
            fill=INK,
        )
        if index < len(steps) - 1:
            arrow_x = left + step_width + 18
            draw.line(
                (arrow_x, y + 32, arrow_x + 22, y + 32),
                fill=ACCENT,
                width=3,
            )
            draw.polygon(
                (
                    (arrow_x + 22, y + 25),
                    (arrow_x + 34, y + 32),
                    (arrow_x + 22, y + 39),
                ),
                fill=ACCENT,
            )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUTPUT, optimize=True)
    print(f"[OnePic] preview: {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
