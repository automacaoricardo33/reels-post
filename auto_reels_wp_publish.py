# -*- coding: utf-8 -*-
# arquivo: auto_reels_wp_publish.py

import os, io, time, math, textwrap, subprocess, argparse
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps
from dotenv import load_dotenv
import cloudinary
import cloudinary.uploader

# ================== CONFIG ==================
load_dotenv()
WP_URL   = os.getenv("WP_URL", "https://jornalvozdolitoral.com").rstrip("/")
TOKEN    = os.getenv("USER_ACCESS_TOKEN", "").strip()
PAGE_ID  = os.getenv("FACEBOOK_PAGE_ID", "").strip()
IG_ID    = os.getenv("INSTAGRAM_ID", "").strip()

CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
CLOUD_KEY  = os.getenv("CLOUDINARY_API_KEY", "")
CLOUD_SEC  = os.getenv("CLOUDINARY_API_SECRET", "")

assert TOKEN and PAGE_ID and IG_ID, "Faltam variáveis no .env (TOKEN/PAGE_ID/IG_ID)."
assert CLOUD_NAME and CLOUD_KEY and CLOUD_SEC, "Faltam credenciais Cloudinary no .env."

cloudinary.config(
    cloud_name=CLOUD_NAME,
    api_key=CLOUD_KEY,
    api_secret=CLOUD_SEC,
    secure=True,
)

OUT = Path("out"); OUT.mkdir(exist_ok=True)
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

# ======== CONSTANTES DO LAYOUT ========
W, H = 1080, 1920
MARGIN_WHITE = 36
BAND_H = 180
LOGO_W = 220
PILL_W, PILL_H = 300, 72
PILL_RADIUS = 14
FONT_ANTON  = "Anton-Regular.ttf"
FONT_ROBOTO = "Roboto-Black.ttf"
SIZE_CAT   = 32
SIZE_TITLE = 55
SIZE_FOOT  = 40
ASSINATURA = "@BOCANOTROMBONELITORAL"
LOGO_PATH  = "logo_boca.png"

# ============ util de imagem ============
def load_image_any(url_or_path: str) -> Image.Image:
    if url_or_path.startswith("http"):
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": "https://google.com",
        }
        r = requests.get(url_or_path, headers=headers, timeout=30)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content))
    else:
        img = Image.open(url_or_path)
    if img.mode in ("P","LA"):
        img = img.convert("RGBA")
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    return img

def cover_resize(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    sw, sh = img.size
    scale = max(target_w / sw, target_h / sh)
    nw, nh = int(sw*scale), int(sh*scale)
    img2 = img.resize((nw, nh), Image.LANCZOS)
    left = (nw-target_w)//2
    top  = (nh-target_h)//2
    return img2.crop((left, top, left+target_w, top+target_h))

def rounded_rectangle(draw: ImageDraw.Draw, xy, radius, fill):
    draw.rounded_rectangle(xy, radius=radius, fill=fill)

def text_size(draw: ImageDraw.Draw, text: str, font: ImageFont.FreeTypeFont):
    bbox = draw.textbbox((0,0), text, font=font)
    return bbox[2]-bbox[0], bbox[3]-bbox[1]

def wrap_text_to_width(draw, text, font, max_w):
    words = text.split()
    lines, cur = [], []
    for w in words:
        trial = (" ".join(cur+[w])).strip()
        tw, _ = text_size(draw, trial, font)
        if tw <= max_w:
            cur.append(w)
        else:
            if cur: lines.append(" ".join(cur))
            cur = [w]
    if cur: lines.append(" ".join(cur))
    return lines

def render_card(bg_img: Image.Image, categoria: str, titulo: str) -> Image.Image:
    canvas = Image.new("RGB", (W,H), "black")
    draw = ImageDraw.Draw(canvas)

    # foto topo
    photo_h = 760
    photo_area = cover_resize(bg_img, W, photo_h)
    canvas.paste(photo_area, (0,0))

    # faixa preta
    band_y = photo_h
    draw.rectangle([0, band_y, W, band_y+BAND_H], fill="black")

    # logo
    if Path(LOGO_PATH).exists():
        logo = Image.open(LOGO_PATH).convert("RGBA")
        ratio = LOGO_W / logo.width
        logo = logo.resize((int(logo.width*ratio), int(logo.height*ratio)), Image.LANCZOS)
        lx = (W - logo.width)//2
        ly = band_y + (BAND_H - logo.height)//2 - 10
        canvas.paste(logo, (lx,ly), mask=logo.split()[-1])

    # pílula
    cat_font = ImageFont.truetype(FONT_ROBOTO, SIZE_CAT)
    pill_x = (W-PILL_W)//2
    pill_y = band_y + BAND_H
    rounded_rectangle(draw, (pill_x, pill_y, pill_x+PILL_W, pill_y+PILL_H), PILL_RADIUS, "#E11D1D")
    cw, ch = text_size(draw, categoria.upper(), cat_font)
    draw.text((W//2 - cw//2, pill_y + (PILL_H-ch)//2), categoria.upper(), font=cat_font, fill="white")

    # caixa branca
    box_w = W - (MARGIN_WHITE*2)
    box_h = 500
    x1 = MARGIN_WHITE
    y1 = pill_y + PILL_H + 24
    x2 = x1 + box_w
    y2 = y1 + box_h
    draw.rectangle([x1,y1,x2,y2], fill="white")

    # título
    title_font = ImageFont.truetype(FONT_ANTON, SIZE_TITLE)
    t = " ".join(titulo.upper().split())
    inner_w = box_w - MARGIN_WHITE
    lines = wrap_text_to_width(draw, t, title_font, inner_w)
    line_h = title_font.getbbox("A")[3] - title_font.getbbox("A")[1]
    ty = y1 + 32
    for ln in lines:
        tw,_ = text_size(draw, ln, title_font)
        tx = (W - tw)//2
        draw.text((tx,ty), ln, font=title_font, fill="black")
        ty += line_h + 10

    # assinatura
    foot_font = ImageFont.truetype(FONT_ROBOTO, SIZE_FOOT)
    fw,fh = text_size(draw, ASSINATURA, foot_font)
    fx = (W - fw)//2
    fy = y2 - fh - 24
    draw.text((fx,fy), ASSINATURA, font=foot_font, fill="#E7B10A")

    return canvas

def make_video_from_image(jpg_path: str, mp4_path: str, seconds=10, audio="audio_fundo.mp3"):
    if not Path(jpg_path).exists():
        raise FileNotFoundError(jpg_path)
    if Path(audio).exists():
        cmd = [
            "ffmpeg","-y","-loop","1","-t",str(seconds),"-i",jpg_path,
            "-stream_loop","-1","-i",audio,"-shortest",
            "-vf","scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
            "-c:v","libx264","-pix_fmt","yuv420p","-r","25",
            "-c:a","aac","-b:a","128k", mp4_path
        ]
    else:
        cmd = [
            "ffmpeg","-y","-loop","1","-t",str(seconds),"-i",jpg_path,
            "-f","lavfi","-i","anullsrc=channel_layout=stereo:sample_rate=44100","-shortest",
            "-vf","scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
            "-c:v","libx264","-pix_fmt","yuv420p","-r","25",
            "-c:a","aac","-b:a","128k", mp4_path
        ]
    subprocess.run(cmd, check=True)

# ============ WordPress ============
def fetch_posts(limit=6):
    url = f"{WP_URL}/wp-json/wp/v2/posts"
    params = {
        "per_page": limit,
        "orderby": "date",
        "_fields": "id,title,excerpt,featured_media,content,link,categories"
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def first_image_from_post(post):
    # tenta featured via content (WordPress do seu site já retorna <img> no content)
    from bs4 import BeautifulSoup
    html = post.get("content",{}).get("rendered","") or post.get("excerpt",{}).get("rendered","")
    soup = BeautifulSoup(html, "html.parser")
    # pega img do corpo (não o destaque)
    img = soup.find("img")
    return img["src"] if img and img.has_attr("src") else None

# ============ Cloudinary ============
def upload_to_cloudinary(video_path: str) -> str:
    up = cloudinary.uploader.upload_large(
        video_path,
        resource_type="video",
        folder="auto_reels",
        overwrite=True
    )
    return up["secure_url"]

# ============ Facebook Page ============
def fb_publish_video_local(video_path: str, message: str=""):
    url = f"https://graph.facebook.com/v23.0/{PAGE_ID}/videos"
    files = {"source": open(video_path, "rb")}
    data = {"description": message}
    r = requests.post(url, headers={"Authorization": f"Bearer {TOKEN}"}, files=files, data=data, timeout=300)
    r.raise_for_status()
    return r.json().get("id")

# ============ Instagram Reels (com polling) ============
def ig_create_container(video_url: str, caption: str) -> str:
    url = f"https://graph.facebook.com/v23.0/{IG_ID}/media"
    data = {
        "video_url": video_url,
        "caption": caption,
        "media_type": "REELS",    # importante !
        "share_to_feed": "true"
    }
    r = requests.post(url, headers=HEADERS, data=data, timeout=60)
    r.raise_for_status()
    return r.json()["id"]  # creation_id

def ig_poll_container(creation_id: str, max_wait=300, step=5) -> str:
    """Espera status_code FINISHED."""
    url = f"https://graph.facebook.com/v23.0/{creation_id}"
    waited = 0
    while waited <= max_wait:
        r = requests.get(url, headers=HEADERS, params={"fields":"status_code,status"}, timeout=30)
        r.raise_for_status()
        js = r.json()
        sc = js.get("status_code") or js.get("status")
        if sc == "FINISHED":
            return sc
        time.sleep(step)
        waited += step
    raise RuntimeError("Timeout esperando FINISHED do container do IG.")

def ig_publish(creation_id: str) -> str:
    url = f"https://graph.facebook.com/v23.0/{IG_ID}/media_publish"
    data = {"creation_id": creation_id}
    # tentativas com backoff para contornar 9007
    for i in range(8):
        r = requests.post(url, headers=HEADERS, data=data, timeout=60)
        if r.status_code == 200:
            return r.json()["id"]
        try:
            js = r.json()
        except Exception:
            js = {}
        err = js.get("error", {})
        sub = err.get("error_subcode")
        if err.get("code") == 9007 or sub == 2207027:
            time.sleep(5 * (i+1))   # backoff
            continue
        r.raise_for_status()
    raise RuntimeError(f"IG /media_publish falhou: {r.text}")

# ============ fluxo completo ============
def process_post(post):
    pid   = post["id"]
    title = post["title"]["rendered"]
    url   = post.get("link","")
    categoria = "ILHABELA" if "ilhabela" in title.lower() else "CARAGUATATUBA" if "caragu" in title.lower() else "SÃO SEBASTIÃO" if "sebasti" in title.lower() else "LITORAL NORTE"

    img_url = first_image_from_post(post)
    if not img_url:
        print(f"⚠️  Post {pid} sem imagem — pulando.")
        return

    try:
        bg = load_image_any(img_url)
    except Exception as e:
        print(f"⚠️  Não baixei imagem post {pid}: {e}")
        # gera fallback cinza
        bg = Image.new("RGB",(1080,1080), "#222")

    card = render_card(bg, categoria, title)
    jpg = OUT / f"arte_{pid}.jpg"
    card.save(jpg, "JPEG", quality=95)
    print(f"✅ Arte: {jpg}")

    mp4 = OUT / f"reel_{pid}.mp4"
    make_video_from_image(str(jpg), str(mp4), seconds=10)
    print(f"✅ Vídeo: {mp4}")

    # publica no FB (local upload)
    try:
        fb_id = fb_publish_video_local(str(mp4), message=title)
        print(f"📘 Publicado na Página: {fb_id}")
    except Exception as e:
        print(f"❌ FB falhou: {e}")

    # IG: precisa de URL público -> Cloudinary
    try:
        url_pub = upload_to_cloudinary(str(mp4))
        creation = ig_create_container(url_pub, caption=title)
        ig_poll_container(creation, max_wait=360, step=6)
        ig_id = ig_publish(creation)
        print(f"📷 Reels publicado no IG: {ig_id}")
    except Exception as e:
        print(f"❌ IG Reels falhou: {e}")

def main():
    print("🚀 Auto Reels (WP→FB+IG) iniciado")
    posts = fetch_posts(6)
    for post in posts:
        process_post(post)

if __name__ == "__main__":
    main()
