# -*- coding: utf-8 -*-
"""
Auto Reels (WP → Facebook + Instagram)
- Busca posts no WP
- Gera arte 1080x1920 (logo, faixa VERMELHA GRANDE da categoria, título, @assinatura)
- Converte a arte em vídeo 10s (com áudio de fundo)
- Publica vídeo no Feed da Página (Facebook) e como Reels no Instagram
- Legenda COMPLETA no Reels: título + texto do artigo (limpo) + CTA

Requisitos: requests, python-dotenv, beautifulsoup4, Pillow, cloudinary, ffmpeg instalado
Arquivos esperados no diretório:
  - .env (tokens)
  - logo_boca.png (com transparência)
  - Anton-Regular.ttf (manchete)
  - Roboto-Black.ttf (categoria)  (pode trocar se preferir Bold)
  - audio_fundo.mp3

Env (.env) – exemplo:
  WP_URL=https://jornalvozdolitoral.com
  USER_ACCESS_TOKEN=EAA...  (token EAA da Página/FB)
  FACEBOOK_PAGE_ID=2137...  (ID da Página)
  INSTAGRAM_ID=17841464...  (ID comercial IG conectado)
  CLOUDINARY_CLOUD_NAME=xxxx
  CLOUDINARY_API_KEY=xxxx
  CLOUDINARY_API_SECRET=xxxx
"""

import os
import io
import time
import textwrap
import logging
import subprocess
from pathlib import Path
from urllib.parse import urljoin, urlparse
import re

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import cloudinary
import cloudinary.uploader

# =========================
# CONFIG GERAL
# =========================
load_dotenv()

WP_URL          = os.getenv("WP_URL", "https://jornalvozdolitoral.com").rstrip("/")
ACCESS_TOKEN    = os.getenv("USER_ACCESS_TOKEN", "")      # EAA...
PAGE_ID         = os.getenv("FACEBOOK_PAGE_ID", "")
IG_ID           = os.getenv("INSTAGRAM_ID", "")

CLOUD_NAME      = os.getenv("CLOUDINARY_CLOUD_NAME", "")
CLOUD_KEY       = os.getenv("CLOUDINARY_API_KEY", "")
CLOUD_SECRET    = os.getenv("CLOUDINARY_API_SECRET", "")

OUT_DIR = Path("out"); OUT_DIR.mkdir(exist_ok=True)
PROCESSED = OUT_DIR / "processed_post_ids.txt"

# Dimensões do Reels
W, H = 1080, 1920
BLACK_Y = 760                    # início da faixa preta de layout
WHITE_BOX_TOP = 920              # início da caixa branca do título

# Tipografia (arquivos .ttf no diretório)
FONT_ANTON  = "Anton-Regular.ttf"
FONT_ROBOTO = "Roboto-Black.ttf"

# Tamanhos
TITLE_FONT_SIZE   = 56           # manchete
CAT_FONT_SIZE     = 76           # categoria (maior)
HANDLE_FONT_SIZE  = 42           # @boca
CAT_RECT_EXTRA_W  = 220          # largura a mais da faixa vermelha
CAT_RECT_EXTRA_H  = 140          # ALTURA da faixa vermelha (BEM ROBUSTA)

WHITE_PAD_H       = 42           # padding da caixa branca
WHITE_SIDE_PAD    = 60           # margem lateral da caixa branca
WHITE_MAX_WIDTH   = W - 2*WHITE_SIDE_PAD

HANDLE_TEXT       = "@BOCANOTROMBONELITORAL"

# Vídeo
VIDEO_SECONDS = 10
AUDIO_PATH    = "audio_fundo.mp3"

# Instagram caption
CTA_SITE   = "jornalvozdolitoral.com"
CTA_SUFFIX = f"\n\nLeia completo em {CTA_SITE}\n{HANDLE_TEXT}"

# Intervalo
INTERVAL = 20  # segundos entre ciclos quando usado em loop externo

# =========================
# LOGGING
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("auto-reels")

# =========================
# UTILS
# =========================
def read_processed_ids() -> set:
    if not PROCESSED.exists():
        return set()
    return set(x.strip() for x in PROCESSED.read_text(encoding="utf-8").splitlines() if x.strip())

def add_processed_id(pid: int) -> None:
    with PROCESSED.open("a", encoding="utf-8") as f:
        f.write(str(pid) + "\n")

def strip_html(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    # remove scripts/styles
    for t in soup(["script", "style"]):
        t.decompose()
    text = soup.get_text("\n")
    # compacta espaços
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text

def session_with_retry():
    s = requests.Session()
    adapter = requests.adapters.HTTPAdapter(max_retries=3)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s

def fetch_posts():
    url = f"{WP_URL}/wp-json/wp/v2/posts?per_page=6&orderby=date&_fields=id,title,excerpt,featured_media,content,link,categories"
    s = session_with_retry()
    r = s.get(url, timeout=20)
    log.info("🔎 GET %s → %s", url, f"OK" if r.ok else r.status_code)
    r.raise_for_status()
    posts = r.json()
    log.info("→ Recebidos %d posts", len(posts))
    return posts

def build_image_url_from_html(post: dict) -> str | None:
    """
    Pega a PRIMEIRA imagem do corpo (content.rendered). 
    Se não achar, tenta no excerpt. Converte avif/webp -> jpg quando for CDN do Metro/FB.
    """
    html = post.get("content", {}).get("rendered") or ""
    soup = BeautifulSoup(html, "html.parser")
    img = soup.find("img")
    if not img or not img.get("src"):
        # tenta excerpt como fallback
        ex = BeautifulSoup(post.get("excerpt", {}).get("rendered") or "", "html.parser").find("img")
        if ex and ex.get("src"):
            src = ex["src"]
        else:
            return None
    else:
        src = img["src"]

    # normaliza urls de CDN com parâmetros que quebram PIL
    # força formato jpg quando detectar /f:avif ou /f:webp
    try:
        if "i.metroimg.com" in src:
            src = re.sub(r"/f:(avif|webp)/", "/f:jpg/", src)
        if "fbcdn.net" in src and "format=" in src:
            # muitas variações — deixamos como está; se 403, seguimos sem baixar
            pass
    except Exception:
        pass

    return src

def download_image(url: str) -> Image.Image | None:
    if not url:
        return None
    s = session_with_retry()
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = s.get(url, headers=headers, timeout=25)
        r.raise_for_status()
        im = Image.open(io.BytesIO(r.content))
        # converte sempre pra RGB
        return im.convert("RGB")
    except Exception as e:
        log.warning("⚠️  Não baixei imagem: %s", e)
        return None

def fit_image(img: Image.Image, dst_w: int, dst_h: int) -> Image.Image:
    """Enche o quadro (cover) preservando proporção e cortando excedente."""
    src_w, src_h = img.size
    scale = max(dst_w / src_w, dst_h / src_h)
    new_w, new_h = int(src_w * scale), int(src_h * scale)
    img2 = img.resize((new_w, new_h), Image.LANCZOS)
    # crop central
    left = (new_w - dst_w) // 2
    top  = (new_h - dst_h) // 2
    return img2.crop((left, top, left + dst_w, top + dst_h))

def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.ImageDraw):
    lines = []
    for p in text.split("\n"):
        if not p.strip():
            lines.append("")
            continue
        words = p.split()
        line = ""
        for w in words:
            test = f"{line} {w}".strip()
            if draw.textlength(test, font=font) <= max_width:
                line = test
            else:
                if line:
                    lines.append(line)
                line = w
        if line:
            lines.append(line)
    return lines

def center_text(draw, txt, font, cx, cy, fill):
    tw = draw.textlength(txt, font=font)
    draw.text((cx - tw/2, cy), txt, font=font, fill=fill)

# =========================
# ARTE
# =========================
def build_art(post: dict, logo_path="logo_boca.png") -> Path:
    """
    Gera a arte 1080x1920 no padrão combinado:
    - Foto full bleed no topo (até BLACK_Y)
    - Logo centralizado sobre a foto, encostando na faixa preta
    - Faixa VERMELHA GRANDE de categoria (aprox metade da caixa branca)
    - Caixa branca com título
    - Assinatura @BOCANOTROMBONE...
    """
    pid = post["id"]
    title_html = post.get("title", {}).get("rendered") or ""
    title = strip_html(title_html).upper()

    # 1) imagem do corpo do artigo
    img_url = build_image_url_from_html(post)
    bg = download_image(img_url) or Image.new("RGB", (W, H), "#202020")
    # ocupa apenas o topo até a faixa preta
    bg_top = fit_image(bg, W, BLACK_Y)

    # canvas final
    canvas = Image.new("RGB", (W, H), "black")
    canvas.paste(bg_top, (0, 0))

    draw = ImageDraw.Draw(canvas)

    # 2) LOGO (convert RGBA → paste com mask)
    try:
        logo = Image.open(logo_path).convert("RGBA")
        scale = 0.26  # tamanho do logo em relação à largura
        lw = int(W * scale)
        lh = int(logo.height * (lw / logo.width))
        logo = logo.resize((lw, lh), Image.LANCZOS)
        # posição: centralizado, encostando na faixa preta
        lx = (W - lw) // 2
        ly = BLACK_Y - lh // 2  # metade sobre a foto e metade na faixa preta
        canvas.paste(logo, (lx, ly), logo)
    except Exception as e:
        log.info("⚠️  Erro ao aplicar logo: %s", e)

    # 3) Faixa VERMELHA GRANDE da CATEGORIA
    cat_txt = "PARLAMENTARES"  # padrão; se houver categoria, você pode mapear por id → nome
    try:
        # se vier "categories" no post, opcionalmente buscar nome; aqui deixo HARD para visual
        pass
    except:
        pass

    cat_font = ImageFont.truetype(FONT_ROBOTO, CAT_FONT_SIZE)
    cat_w = draw.textlength(cat_txt, font=cat_font)
    faixa_w = int(cat_w + CAT_RECT_EXTRA_W)
    faixa_h = int(CAT_RECT_EXTRA_H)
    faixa_x1 = (W - faixa_w) // 2
    faixa_y  = BLACK_Y + 28
    faixa_x2 = faixa_x1 + faixa_w
    draw.rectangle([faixa_x1, faixa_y, faixa_x2, faixa_y + faixa_h], fill="#E41F1F")

    # texto branco dentro da faixa, maior e central
    center_text(draw, cat_txt, cat_font, W/2, faixa_y + (faixa_h - CAT_FONT_SIZE)//2 - 6, "white")

    # 4) Caixa branca (título)
    title_font = ImageFont.truetype(FONT_ANTON, TITLE_FONT_SIZE)
    # onde começa a caixa branca? logo após a faixa vermelha + um respiro
    white_top = faixa_y + faixa_h + 26
    white_height_available = 370  # altura segura para 2–4 linhas
    white_bottom = white_top + white_height_available

    # fundo branco
    draw.rectangle([WHITE_SIDE_PAD, white_top, W - WHITE_SIDE_PAD, white_bottom], fill="white")

    # quebra de linha dentro da largura da caixa
    inner_width = (W - 2*WHITE_SIDE_PAD) - 2*36  # margem interna extra
    lines = wrap_text(title, title_font, inner_width, draw)

    # escreve linhas centralizadas vertical/horizontalmente
    ty = white_top + WHITE_PAD_H
    line_gap = 12
    for ln in lines[:6]:  # limitar pra não estourar
        tw = draw.textlength(ln, font=title_font)
        tx = (W - tw) // 2
        draw.text((tx, ty), ln, font=title_font, fill="black")
        ty += title_font.size + line_gap

    # 5) Assinatura
    handle_font = ImageFont.truetype(FONT_ROBOTO, HANDLE_FONT_SIZE)
    handle_w = draw.textlength(HANDLE_TEXT, font=handle_font)
    hx = (W - handle_w) // 2
    hy = white_bottom + 18
    draw.text((hx, hy), HANDLE_TEXT, font=handle_font, fill="#FDD13A")

    # salva
    out_path = OUT_DIR / f"arte_{pid}.jpg"
    canvas.save(out_path, "JPEG", quality=92, optimize=True)
    return out_path

# =========================
# VÍDEO
# =========================
def make_video_from_image(img_path: Path, out_path: Path, seconds=VIDEO_SECONDS):
    """
    Gera vídeo mp4 (1080x1920, 25fps) com a imagem estática + áudio.
    """
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(img_path),
        "-i", AUDIO_PATH,
        "-t", str(seconds),
        "-r", "25",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        "-c:a", "aac", "-b:a", "128k",
        str(out_path)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# =========================
# FACEBOOK
# =========================
def fb_publish_video(page_id: str, video_path: Path, message: str) -> str | None:
    url = f"https://graph.facebook.com/v23.0/{page_id}/videos"
    files = {"source": open(video_path, "rb")}
    data  = {"access_token": ACCESS_TOKEN, "description": message}
    r = requests.post(url, files=files, data=data, timeout=120)
    try:
        r.raise_for_status()
        vid = r.json().get("id")
        log.info("📘 Publicado na Página (vídeo): id=%s", vid)
        return vid
    except Exception:
        log.error("❌ FB falhou: %s | %s", r.status_code, r.text)
        return None
    finally:
        files["source"].close()

# =========================
# CLOUDINARY + INSTAGRAM
# =========================
cloudinary.config(
    cloud_name=CLOUD_NAME,
    api_key=CLOUD_KEY,
    api_secret=CLOUD_SECRET,
    secure=True
)

def upload_cloudinary(video_path: Path) -> str | None:
    try:
        res = cloudinary.uploader.upload_large(
            str(video_path),
            resource_type="video",
            folder="reels"
        )
        return res.get("secure_url")
    except Exception as e:
        log.error("❌ Cloudinary falhou: %s", e)
        return None

def ig_create_media(ig_id: str, video_url: str, caption: str) -> str | None:
    url = f"https://graph.facebook.com/v23.0/{ig_id}/media"
    data = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "access_token": ACCESS_TOKEN
    }
    r = requests.post(url, data=data, timeout=60)
    try:
        r.raise_for_status()
        creation_id = r.json()["id"]
        return creation_id
    except Exception:
        log.error("❌ IG /media falhou: %s | %s", r.status_code, r.text)
        return None

def ig_publish(ig_id: str, creation_id: str) -> str | None:
    url = f"https://graph.facebook.com/v23.0/{ig_id}/media_publish"
    data = {"creation_id": creation_id, "access_token": ACCESS_TOKEN}
    r = requests.post(url, data=data, timeout=60)
    try:
        r.raise_for_status()
        media_id = r.json()["id"]
        log.info("📷 Reels publicado: id=%s", media_id)
        return media_id
    except Exception:
        log.error("❌ IG /media_publish falhou: %s | %s", r.status_code, r.text)
        return None

def ig_check_status(creation_id: str) -> str | None:
    url = f"https://graph.facebook.com/v23.0/{creation_id}?fields=status_code&access_token={ACCESS_TOKEN}"
    r = requests.get(url, timeout=20)
    try:
        r.raise_for_status()
        return r.json().get("status_code")
    except Exception:
        return None

# =========================
# LEGENDA (CAPTION) COMPLETA
# =========================
def build_caption(post: dict) -> str:
    title = strip_html(post.get("title", {}).get("rendered") or "").strip()
    body  = strip_html(post.get("content", {}).get("rendered") or "")
    # IG tem limite ~2.200 chars
    MAX_CAP = 2200
    core = (title.upper() + "\n\n" + body).strip()
    if len(core) + len(CTA_SUFFIX) > MAX_CAP:
        core = core[:MAX_CAP - len(CTA_SUFFIX) - 10].rstrip() + "…"
    caption = core + CTA_SUFFIX
    return caption

# =========================
# PROCESSAMENTO
# =========================
def process_post(post: dict):
    pid = post["id"]
    log.info("🎨 Arte post %s…", pid)
    try:
        art = build_art(post)
        log.info("✅ Arte: %s", art)
    except Exception as e:
        log.error("❌ Falha gerar arte %s: %s", pid, e)
        return

    reel = OUT_DIR / f"reel_{pid}.mp4"
    try:
        log.info("🎬 Gerando vídeo 10s…")
        make_video_from_image(art, reel, VIDEO_SECONDS)
        log.info("✅ Vídeo: %s", reel)
    except Exception as e:
        log.error("❌ Falha gerar vídeo %s: %s", pid, e)
        return

    # CAPTION completo para IG
    caption = build_caption(post)

    # Publica no Feed da Página (opcional: usar o mesmo caption resumido)
    fb_msg = strip_html(post.get("title", {}).get("rendered") or "")
    fb_publish_video(PAGE_ID, reel, fb_msg)

    # Sobe para Cloudinary → IG
    video_url = upload_cloudinary(reel)
    if not video_url:
        return

    creation_id = ig_create_media(IG_ID, video_url, caption)
    if not creation_id:
        return

    # Poll até FINISHED com paciência
    for _ in range(40):  # ~40 * 3s = 120s
        status = ig_check_status(creation_id)
        if status:
            log.info("⏳ IG status: %s", status)
            if status == "FINISHED":
                break
            if status in ("ERROR", "EXPIRED"):
                log.error("❌ IG status final: %s", status)
                return
        time.sleep(3)

    # Publish
    media_id = ig_publish(IG_ID, creation_id)
    if media_id:
        add_processed_id(pid)

def main_once():
    seen = read_processed_ids()
    posts = fetch_posts()
    for p in posts:
        if str(p["id"]) in seen:
            continue
        process_post(p)

def main():
    log.info("🚀 Auto Reels (WP→FB+IG) iniciado")
    main_once()

if __name__ == "__main__":
    main()
