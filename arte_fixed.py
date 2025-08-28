# -*- coding: utf-8 -*-
# arquivo: arte_fixed.py
import io
import os
import math
import textwrap
import argparse
import subprocess
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

# ======== CONSTANTES DO LAYOUT (fixo) ========
W, H = 1080, 1920                          # canvas 9:16
MARGIN_WHITE = 36                           # margem interna da caixa branca
BAND_H = 180                                # altura da faixa preta separadora
LOGO_W = 220                                # largura destino do logo central
PILL_W, PILL_H = 300, 72                    # pílula vermelha da categoria
PILL_RADIUS = 14

# Tipografia
FONT_ANTON = "Anton-Regular.ttf"            # manchete
FONT_ROBOTO = "Roboto-Black.ttf"            # categoria/rodapé
SIZE_CAT = 32                               # categoria (em caixa)
SIZE_TITLE = 55                             # título (em caixa)
SIZE_FOOT = 40                              # @assinatura

ASSINATURA = "@BOCANOTROMBONELITORAL"      # rodapé dentro da caixa branca

# ======== util ========
def load_image_any(url_or_path: str) -> Image.Image:
    """Carrega imagem de URL (com headers p/ evitar 403) ou caminho local.
       Garante modo RGB."""
    if url_or_path.startswith("http"):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": "https://google.com",
        }
        r = requests.get(url_or_path, headers=headers, timeout=30)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content))
    else:
        img = Image.open(url_or_path)
    # alguns formatos vêm como P/LA/RGBA: normaliza
    if img.mode in ("P", "LA"):
        img = img.convert("RGBA")
    if img.mode == "RGBA":
        # se tiver alpha, compõe sobre branco
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    return img


def cover_resize(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Corta/resize no estilo 'object-fit: cover' sem distorcer."""
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = int(src_w * scale), int(src_h * scale)
    img2 = img.resize((new_w, new_h), Image.LANCZOS)
    # crop central
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img2.crop((left, top, left + target_w, top + target_h))


def rounded_rectangle(draw: ImageDraw.Draw, xy, radius, fill):
    """Desenha retângulo arredondado simples."""
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle(xy, radius=radius, fill=fill)


def text_size(draw: ImageDraw.Draw, text: str, font: ImageFont.FreeTypeFont):
    """Mede texto (largura/altura)."""
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def wrap_text_to_width(draw, text, font, max_w):
    """Quebra o texto por palavras para caber em max_w (caixa branca)."""
    words = text.split()
    lines = []
    cur = []
    for w in words:
        trial = (" ".join(cur + [w])).strip()
        if not trial:
            continue
        tw, _ = text_size(draw, trial, font)
        if tw <= max_w:
            cur.append(w)
        else:
            if cur:
                lines.append(" ".join(cur))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))
    return lines


def draw_centered(draw, text, font, x, y, fill="white"):
    w, h = text_size(draw, text, font)
    draw.text((x - w // 2, y - h // 2), text, font=font, fill=fill)


# ========= render principal =========
def render_card(bg_img: Image.Image, categoria: str, titulo: str, logo_path="logo_boca.png") -> Image.Image:
    """
    Monta a arte 1080x1920 no padrão fixo:
      - Foto em cima com 'cover'
      - Faixa preta (BAND_H)
      - Logo central sobre a faixa
      - Pílula vermelha da categoria
      - Caixa branca com margem 36, título em Anton 55
      - Rodapé dentro da caixa: @BOCANOTROMBONELITORAL Roboto 40
    """
    # Canvas
    canvas = Image.new("RGB", (W, H), "black")
    draw = ImageDraw.Draw(canvas)

    # --- FOTO NO TOPO (altura até começo da faixa preta) ---
    photo_h = H - (BAND_H + 0) - 600  # altura da foto; ajustei para ficar como seu mock
    # Para ficar “como no mock”, a foto ocupa ~ 720–760 px; ajuste fino:
    photo_h = max(700, min(900, photo_h))
    photo_area = cover_resize(bg_img, W, photo_h)
    canvas.paste(photo_area, (0, 0))

    # --- FAIXA PRETA ---
    band_y = photo_h
    draw.rectangle([0, band_y, W, band_y + BAND_H], fill="black")

    # --- LOGO CENTRAL SOBRE A FAIXA ---
    if Path(logo_path).exists():
        logo = Image.open(logo_path).convert("RGBA")
        # normaliza largura
        ratio = LOGO_W / logo.width
        logo = logo.resize((int(logo.width * ratio), int(logo.height * ratio)), Image.LANCZOS)
        lx = (W - logo.width) // 2
        ly = band_y + (BAND_H - logo.height) // 2 - 10  # pequeno ajuste para cima
        canvas.paste(logo, (lx, ly), mask=logo.split()[-1])

    # --- PÍLULA VERMELHA DA CATEGORIA ---
    cat_font = ImageFont.truetype(FONT_ROBOTO, SIZE_CAT)
    cat_text = categoria.strip().upper()
    pill_x = (W - PILL_W) // 2
    pill_y = band_y + BAND_H  # logo abaixo da faixa
    rounded_rectangle(draw, (pill_x, pill_y, pill_x + PILL_W, pill_y + PILL_H), PILL_RADIUS, fill="#E11D1D")
    draw_centered(draw, cat_text, cat_font, W // 2, pill_y + PILL_H // 2, fill="white")

    # --- CAIXA BRANCA DA MANCHETE ---
    # largura da caixa branca com margem lateral 36, conforme padrão
    box_w = W - (MARGIN_WHITE * 2)
    # altura variável, mas mantendo o look (cerca de 500 px)
    box_h = 500
    box_x1 = MARGIN_WHITE
    box_y1 = pill_y + PILL_H + 24  # espaço entre pílula e caixa
    box_x2 = box_x1 + box_w
    box_y2 = box_y1 + box_h
    draw.rectangle([box_x1, box_y1, box_x2, box_y2], fill="white")

    # --- TÍTULO (Anton 55, caixa alta, centralizado) ---
    title_font = ImageFont.truetype(FONT_ANTON, SIZE_TITLE)
    title_text = " ".join(titulo.strip().upper().split())
    inner_w = box_w - (MARGIN_WHITE * 1)  # pequena margem interna
    # quebra em linhas para caber
    lines = wrap_text_to_width(draw, title_text, title_font, inner_w)
    # calcula altura total das linhas
    line_h = title_font.getbbox("A")[3] - title_font.getbbox("A")[1]
    total_h = len(lines) * line_h + (len(lines) - 1) * 10
    # topo do texto dentro da caixa branca
    ty = box_y1 + 32
    # desenha cada linha centralizada
    for ln in lines:
        tw, _ = text_size(draw, ln, title_font)
        tx = (W - tw) // 2
        draw.text((tx, ty), ln, font=title_font, fill="black")
        ty += line_h + 10

    # --- ASSINATURA (@BOCANOTROMBONELITORAL) ---
    foot_font = ImageFont.truetype(FONT_ROBOTO, SIZE_FOOT)
    fw, fh = text_size(draw, ASSINATURA, foot_font)
    fx = (W - fw) // 2
    fy = box_y2 - fh - 24
    draw.text((fx, fy), ASSINATURA, font=foot_font, fill="#E7B10A")  # amarelo suave

    return canvas


def make_video_from_image(jpg_path: str, mp4_path: str, seconds=10, audio="audio_fundo.mp3"):
    """Gera um MP4 de duração fixa a partir do JPG. Requer ffmpeg instalado."""
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-t", str(seconds), "-i", jpg_path,
        "-stream_loop", "-1", "-i", audio if os.path.exists(audio) else "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-shortest",
        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25",
        "-c:a", "aac", "-b:a", "128k",
        mp4_path,
    ]
    # quando não houver áudio local, muda input da anullsrc
    if not os.path.exists(audio):
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-t", str(seconds), "-i", jpg_path,
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-shortest",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25",
            "-c:a", "aac", "-b:a", "128k",
            mp4_path,
        ]
    subprocess.run(cmd, check=True)


def main():
    ap = argparse.ArgumentParser(description="Gera arte 1080x1920 no padrão fixo + (opcional) MP4 10s.")
    ap.add_argument("--img", required=True, help="URL ou caminho da foto de fundo")
    ap.add_argument("--categoria", required=True)
    ap.add_argument("--titulo", required=True)
    ap.add_argument("--out", default="out/arte.jpg")
    ap.add_argument("--mp4", default="")
    args = ap.parse_args()

    Path("out").mkdir(exist_ok=True)
    bg = load_image_any(args.img)
    card = render_card(bg, args.categoria, args.titulo)
    card.save(args.out, "JPEG", quality=95)
    print(f"✅ Arte: {args.out}")

    if args.mp4:
        make_video_from_image(args.out, args.mp4, seconds=10)
        print(f"✅ Vídeo: {args.mp4}")


if __name__ == "__main__":
    main()
