# -*- coding: utf-8 -*-
"""
Auto Reels WP -> Arte -> MP4 -> Cloudinary -> Facebook Page Video + Instagram Reels
Padrão visual:
 - Fundo: foto (metade superior) 1080x960, com logo centralizado perto da linha de corte
 - Caixa branca (manchete) mais alta, acima do meio, com Anton 55
 - Categoria (vermelha) Roboto 32
 - Rodapé @BOCANOTROMBONELITORAL Roboto 40
Vídeo: 1080x1920, 10s, com áudio (audio_fundo.mp3)

Arquivos esperados na pasta:
 - logo_boca.png
 - Anton-Regular.ttf
 - Roboto-Black.ttf
 - audio_fundo.mp3

.env (exemplos):
WP_URL=https://jornalvozdolitoral.com
CRON_INTERVAL_SECONDS=20
VIDEO_SECONDS=10
BRAND_HANDLE=@BOCANOTROMBONELITORAL
USER_ACCESS_TOKEN=<PAGE TOKEN COM PERMISSÕES>
FACEBOOK_PAGE_ID=213776928485804
INSTAGRAM_ID=17841464327364824
CLOUDINARY_CLOUD_NAME=<cloud>
CLOUDINARY_API_KEY=<key>
CLOUDINARY_API_SECRET=<secret>
"""

import os, io, time, json, re, subprocess, textwrap, logging, tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path
from base64 import b64encode

import requests
from requests.adapters import HTTPAdapter, Retry
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageOps

import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv

# ========= Config/log =========
ROOT = Path(__file__).resolve().parent
OUT  = ROOT / "out"
OUT.mkdir(exist_ok=True)

log_path = OUT / "auto-reels.log"
logger = logging.getLogger("auto-reels")
logger.setLevel(logging.INFO)
handler = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S")
handler.setFormatter(fmt)
logger.addHandler(handler)
console = logging.StreamHandler()
console.setFormatter(fmt)
logger.addHandler(console)
log = logger.info
log_err = logger.error

load_dotenv()

WP_URL   = os.getenv("WP_URL", "").rstrip("/")
INTERVAL = int(os.getenv("CRON_INTERVAL_SECONDS", "20"))
VIDEO_S  = int(os.getenv("VIDEO_SECONDS", "10"))
HANDLE   = os.getenv("BRAND_HANDLE", "@BOCANOTROMBONELITORAL")

PAGE_TOKEN = os.getenv("USER_ACCESS_TOKEN", "")
PAGE_ID    = os.getenv("FACEBOOK_PAGE_ID", "")
IG_ID      = os.getenv("INSTAGRAM_ID", "")
API_V      = os.getenv("API_VERSION", "v23.0")

CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
CLOUD_KEY  = os.getenv("CLOUDINARY_API_KEY", "")
CLOUD_SEC  = os.getenv("CLOUDINARY_API_SECRET", "")

# Tipos/arquivos
LOGO_PATH     = ROOT / "logo_boca.png"
FONT_HEADLINE = ROOT / "Anton-Regular.ttf"     # manchete
FONT_ROBOTO   = ROOT / "Roboto-Black.ttf"      # categoria/rodapé
AUDIO_PATH    = ROOT / "audio_fundo.mp3"

# Controle de duplicatas
PROCESSED = OUT / "processed_post_ids.txt"

# ======== HTTP session com retry ========
def make_session():
    s = requests.Session()
    retries = Retry(total=3, connect=3, read=3, backoff_factor=0.6,
                    status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["GET", "POST", "HEAD"])
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.mount("http://",  HTTPAdapter(max_retries=retries))
    s.headers["User-Agent"] = "auto-reels/1.0"
    return s

http = make_session()

# ======== Utils duplicatas ========
def get_done():
    if not PROCESSED.exists():
        return set()
    return set(a.strip() for a in PROCESSED.read_text(encoding="utf-8").splitlines() if a.strip())

def mark_done(pid: str):
    with PROCESSED.open("a", encoding="utf-8") as f:
        f.write(pid + "\n")

# ======== WP helpers ========
def wp_get_posts(per_page=6):
    url = f"{WP_URL}/wp-json/wp/v2/posts?per_page={per_page}&orderby=date&_fields=id,title,excerpt,featured_media,content,link,categories"
    r = http.get(url, timeout=20)
    log(f"🔎 GET {url} → HTTP {r.status_code}")
    r.raise_for_status()
    posts = r.json()
    log(f"→ Recebidos {len(posts)} posts deste endpoint")
    return posts

def wp_media_url(media_id: int):
    if not media_id:
        return None
    url = f"{WP_URL}/wp-json/wp/v2/media/{media_id}"
    try:
        r = http.get(url, timeout=15)
        r.raise_for_status()
        return r.json().get("source_url")
    except Exception:
        return None

def extract_first_image_from_content(html: str):
    soup = BeautifulSoup(html or "", "html.parser")
    # 1) <img src>
    img = soup.find("img")
    if img and img.get("src"):
        return img["src"]
    # 2) <figure><img>
    fig = soup.find("figure")
    if fig:
        im = fig.find("img")
        if im and im.get("src"):
            return im["src"]
    # 3) fallback nenhum
    return None

# ======== Download imagem (com fallback e conversões) ========
def download_image(url: str) -> Image.Image:
    """
    Baixa a imagem como RGB. Se AVIF ou 403 em CDN, ignora e deixa levantar exceção
    para quem chamou tratar fallback preto.
    """
    r = http.get(url, timeout=30)
    r.raise_for_status()

    raw = io.BytesIO(r.content)

    # Tenta AVIF -> converter (se instalado); senão cai no except e o chamador trata fallback
    try:
        im = Image.open(raw)
        im.load()
        # Converte sempre pra RGB (evita 'wrong mode')
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA")
        # Remove alfa em fundo preto se RGBA
        if im.mode == "RGBA":
            bg = Image.new("RGB", im.size, (0,0,0))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        return im
    except Exception as e:
        raise

# ======== Layout da arte ========
def make_art(img_bg: Image.Image, title: str, category: str, out_path: Path):
    """
    Layout 1080x1920, metade de cima com a foto (1080x960, cover)
    logo centralizado na transição, faixa branca mais alta acima do meio,
    Anton 55 (manchete), Roboto 32 (categoria), Roboto 40 rodapé handle.
    """
    W, H = 1080, 1920
    canvas = Image.new("RGB", (W, H), "black")
    draw   = ImageDraw.Draw(canvas)

    # ---- fundo (capa) na metade de cima (cover)
    # redimensiona a imagem base pra cobrir 1080x960 sem distorcer
    target_w, target_h = 1080, 960
    bg = img_bg.copy()
    bg_ratio = bg.width / bg.height
    target_ratio = target_w / target_h

    if bg_ratio > target_ratio:
        # cortar lados
        new_h = target_h
        new_w = int(new_h * bg_ratio)
        bg = bg.resize((new_w, new_h), Image.Resampling.LANCZOS)
        left = (new_w - target_w)//2
        bg = bg.crop((left, 0, left+target_w, target_h))
    else:
        # cortar topo/baixo
        new_w = target_w
        new_h = int(new_w / bg_ratio)
        bg = bg.resize((new_w, new_h), Image.Resampling.LANCZOS)
        top = (new_h - target_h)//2
        bg = bg.crop((0, top, target_w, top+target_h))

    canvas.paste(bg, (0, 0))

    # ---- logo sobre a transição (um pouco acima do meio superior)
    if LOGO_PATH.exists():
        logo = Image.open(LOGO_PATH).convert("RGBA")
        # largura alvo logo (aprox 34% da largura)
        target_logo_w = 360
        ratio = target_logo_w / logo.width
        logo = logo.resize((target_logo_w, int(logo.height*ratio)), Image.Resampling.LANCZOS)
        # posição: centro, ~ 860px de altura (um pouco acima da linha de 960)
        lx = (W - logo.width)//2
        ly = 860 - (logo.height//2)
        canvas.paste(logo, (lx, ly), logo)

    # ---- fontes
    try:
        f_cat = ImageFont.truetype(str(FONT_ROBOTO), 32)
    except:
        f_cat = ImageFont.load_default()
    try:
        f_head = ImageFont.truetype(str(FONT_HEADLINE), 55)
    except:
        f_head = ImageFont.load_default()
    try:
        f_rod = ImageFont.truetype(str(FONT_ROBOTO), 40)
    except:
        f_rod = ImageFont.load_default()

    # ---- faixa branca (manchete) — mais alta e próxima do logo
    # área de texto: y ~ 1020 até ~1500
    box_margin_x = 60
    box_y = 1040  # levantada
    box_w = W - 2*box_margin_x

    # categoria (retângulo vermelho)
    cat_text = (category or "Notícias").upper()
    cat_bbox = draw.textbbox((0,0), cat_text, font=f_cat)
    cat_w = cat_bbox[2]-cat_bbox[0]; cat_h = cat_bbox[3]-cat_bbox[1]
    cat_pad_x, cat_pad_y = 28, 16
    cat_box_w = cat_w + 2*cat_pad_x
    cat_box_h = cat_h + 2*cat_pad_y
    cat_x = (W - cat_box_w)//2
    cat_y = box_y - (cat_box_h + 28)  # um pouco acima da caixa branca

    draw.rectangle([cat_x, cat_y, cat_x+cat_box_w, cat_y+cat_box_h], fill="#e50000")
    draw.text((cat_x+cat_box_w//2, cat_y+cat_box_h//2), cat_text, font=f_cat, fill="white", anchor="mm")

    # caixa branca com a manchete
    # quebra de linha ~ 23-25 chars dependendo do título
    lines = textwrap.wrap(title.strip(), width=24)
    # mede altura total
    line_h = (draw.textbbox((0,0), "Ay", font=f_head)[3])
    text_h = line_h*len(lines) + 20*(len(lines)-1)
    pad_tb, pad_lr = 32, 28
    box_h = text_h + 2*pad_tb
    box_x0 = box_margin_x
    box_y0 = box_y
    box_y1 = box_y0 + box_h
    draw.rectangle([box_x0, box_y0, W - box_margin_x, box_y1], fill="white")

    # escreve as linhas centralizadas
    y_cursor = box_y0 + pad_tb
    for ln in lines:
        draw.text((W//2, y_cursor), ln, font=f_head, fill="black", anchor="ma")  # middle, top align centrado
        y_cursor += line_h + 20

    # rodapé (handle) amarelo, centralizado, mais alto do que antes
    rod_text = HANDLE or "@BOCANOTROMBONELITORAL"
    rod_y = box_y1 + 28
    rod_bbox = draw.textbbox((0,0), rod_text, font=f_rod)
    rod_w = rod_bbox[2]-rod_bbox[0]
    # fundo amarelo com padding leve
    padx, pady = 18, 8
    rx0 = (W - (rod_w + 2*padx))//2
    ry0 = rod_y
    rx1 = rx0 + rod_w + 2*padx
    ry1 = ry0 + (rod_bbox[3]-rod_bbox[1]) + 2*pady
    draw.rectangle([rx0, ry0, rx1, ry1], fill="#ffde00")
    draw.text((W//2, ry0 + (ry1-ry0)//2), rod_text, font=f_rod, fill="black", anchor="mm")

    # salva
    canvas.save(out_path, "JPEG", quality=92)

# ======== Vídeo ========
def make_video_from_image(image_path: Path, out_mp4: Path, seconds: int):
    """
    Gera um MP4 1080x1920 com imagem estática + áudio (se existir).
    """
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(image_path),
    ]
    if AUDIO_PATH.exists():
        cmd += ["-i", str(AUDIO_PATH)]
    # filtros
    vf = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
    cmd += [
        "-t", str(seconds),
        "-r", "25",
        "-vf", vf,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
    ]
    if AUDIO_PATH.exists():
        cmd += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
    else:
        cmd += ["-an"]
    cmd.append(str(out_mp4))

    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ======== Cloudinary ========
def cloudinary_setup():
    if not (CLOUD_NAME and CLOUD_KEY and CLOUD_SEC):
        raise RuntimeError("Cloudinary credenciais ausentes no .env")
    cloudinary.config(cloud_name=CLOUD_NAME, api_key=CLOUD_KEY, api_secret=CLOUD_SEC)

def cloudinary_upload_video(mp4_path: Path) -> str:
    res = cloudinary.uploader.upload(
        str(mp4_path),
        resource_type="video",
        folder="auto-reels",
        overwrite=True,
        use_filename=True,
    )
    return res["secure_url"]

# ======== Publicadores ========
def publish_facebook_video(file_url: str, caption: str):
    """
    Publica como VÍDEO na página (robusto). Reels do Facebook variam por conta da session_id;
    o vídeo na página é aceito de forma estável.
    """
    url = f"https://graph.facebook.com/{API_V}/{PAGE_ID}/videos"
    params = {
        "file_url": file_url,
        "description": caption,
        "access_token": PAGE_TOKEN
    }
    r = http.post(url, params=params, timeout=180)
    if r.status_code != 200:
        raise RuntimeError(f"FB /videos {r.status_code} | {r.text}")
    return r.json().get("id")

def publish_instagram_reel(video_url: str, caption: str):
    """
    Publica Reels no IG. Requer IG_ID e token com permissões.
    """
    if not IG_ID:
        raise RuntimeError("INSTAGRAM_ID ausente no .env")
    # 1) cria media
    url_media = f"https://graph.facebook.com/{API_V}/{IG_ID}/media"
    r1 = http.post(url_media, params={
        "video_url": video_url,
        "caption": caption,
        "media_type": "REELS",
        "access_token": PAGE_TOKEN
    }, timeout=180)
    if r1.status_code != 200:
        raise RuntimeError(f"IG /media {r1.status_code} | {r1.text}")
    creation_id = r1.json()["id"]
    # 2) publica
    url_pub = f"https://graph.facebook.com/{API_V}/{IG_ID}/media_publish"
    r2 = http.post(url_pub, params={
        "creation_id": creation_id,
        "access_token": PAGE_TOKEN
    }, timeout=180)
    if r2.status_code != 200:
        raise RuntimeError(f"IG /media_publish {r2.status_code} | {r2.text}")
    return r2.json()

# ======== Pipeline de 1 post ========
def process_post(post: dict):
    pid = str(post.get("id"))
    title_html = post.get("title", {}).get("rendered", "")
    title = BeautifulSoup(title_html, "html.parser").get_text().strip()
    if not title:
        title = "Notícia"

    content_html = post.get("content", {}).get("rendered", "")
    first_img = extract_first_image_from_content(content_html)
    # se veio URL de fbcdn/avif que dá 403, vamos tentar destacar
    if not first_img:
        first_img = wp_media_url(post.get("featured_media"))

    category_name = "Notícias"
    try:
        cats = post.get("categories") or []
        if cats:
            cat_id = cats[0]
            r = http.get(f"{WP_URL}/wp-json/wp/v2/categories/{cat_id}", timeout=10)
            if r.ok:
                category_name = r.json().get("name", "Notícias")
    except Exception:
        pass

    log(f"🎨 Arte post {pid}…")
    # Baixar imagem ou fallback preto
    try:
        if not first_img:
            raise RuntimeError("Sem imagem no conteúdo nem destaque.")
        bg = download_image(first_img)  # RGB garantido
    except Exception as e:
        log(f"⚠️  Não baixei imagem: {e}")
        # fundo preto
        bg = Image.new("RGB", (1080, 960), "black")

    arte_path = OUT / f"arte_{pid}.jpg"
    make_art(bg, title, category_name, arte_path)
    log(f"✅ Arte: {arte_path}")

    # Vídeo
    reel_path = OUT / f"reel_{pid}.mp4"
    log("🎬 Gerando vídeo 10s…")
    make_video_from_image(arte_path, reel_path, VIDEO_S)
    log(f"✅ Vídeo: {reel_path}")

    # Upload Cloudinary
    cloudinary_setup()
    file_url = cloudinary_upload_video(reel_path)

    # Caption básica
    caption = f"{title}\n\nLeia mais em: {post.get('link','')}\n\n{HANDLE} #noticias"

    # Publica Facebook Page (vídeo)
    try:
        fb_id = publish_facebook_video(file_url, caption)
        log(f"📘 Publicado na Página (vídeo): id={fb_id}")
    except Exception as e:
        log_err(f"❌ FB vídeo falhou: {e}")

    # Publica IG Reels (se IG_ID definido)
    if IG_ID and PAGE_TOKEN:
        try:
            _ = publish_instagram_reel(file_url, caption)
            log("📸 Reels publicado no Instagram.")
        except Exception as e:
            log_err(f"❌ IG Reels falhou: {e}")

    mark_done(pid)

# ======== Loop principal ========
def main():
    log("🚀 Auto Reels (WP→FB+IG) iniciado")
    done = get_done()
    while True:
        try:
            posts = wp_get_posts(per_page=6)
            for p in posts:
                pid = str(p["id"])
                if pid in done:
                    continue
                process_post(p)
                done.add(pid)
        except Exception as e:
            log_err(f"❌ Ciclo falhou: {e}")
        log(f"⏳ Aguardando {INTERVAL}s…")
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
