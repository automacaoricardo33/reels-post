# auto_reels_wp_publish.py
# ============================================
# WP -> Gera arte no padrão Boca -> Vídeo 10s -> Publica FB + IG Reels
# Layout fixo, sem “escorregar” nada.

import os, io, re, time, json, subprocess, logging, textwrap
from pathlib import Path
from urllib.parse import urljoin
from datetime import datetime

import requests
from requests.adapters import HTTPAdapter, Retry
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv

import cloudinary, cloudinary.uploader


# --------- CONFIG BÁSICA ---------
load_dotenv()
W, H = 1080, 1920
TOP_H = 1080

WP_URL = os.getenv("WP_URL", "").rstrip("/")
PAGE_ID = os.getenv("FACEBOOK_PAGE_ID")
IG_ID   = os.getenv("INSTAGRAM_ID")
TOKEN   = os.getenv("USER_ACCESS_TOKEN")
API_V   = os.getenv("API_VERSION", "v23.0")

CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
CLOUD_KEY  = os.getenv("CLOUDINARY_API_KEY")
CLOUD_SEC  = os.getenv("CLOUDINARY_API_SECRET")

VIDEO_SECONDS = int(os.getenv("VIDEO_SECONDS", "10"))

ANTON  = "Anton-Regular.ttf"
ROBOTO = "Roboto-Black.ttf"
LOGO   = "logo_boca.png"
AUDIO  = "audio_fundo.mp3"

OUT_DIR = Path("out"); OUT_DIR.mkdir(exist_ok=True)
PROCESSED_FILE = Path("processed_post_ids.txt")

# --------- LOG ---------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("auto-reels")

# --------- HTTP SESSION COM RETRY ---------
def http():
    s = requests.Session()
    r = Retry(total=3, backoff_factor=0.6, status_forcelist=[429,502,503,504])
    s.mount("http://", HTTPAdapter(max_retries=r))
    s.mount("https://", HTTPAdapter(max_retries=r))
    s.headers.update({"User-Agent":"BocaAutoReels/1.0"})
    return s

S = http()

# --------- UTILS ---------
def read_processed():
    if not PROCESSED_FILE.exists(): return set()
    return set(x.strip() for x in PROCESSED_FILE.read_text(encoding="utf-8").splitlines() if x.strip())

def add_processed(pid):
    with PROCESSED_FILE.open("a", encoding="utf-8") as f:
        f.write(str(pid)+"\n")

def clean_text(html_or_text):
    txt = BeautifulSoup(html_or_text or "", "html.parser").get_text(" ", strip=True)
    # normaliza espaços
    return re.sub(r"\s+", " ", txt).strip()

def find_first_image_url(html):
    soup = BeautifulSoup(html or "", "html.parser")
    img = soup.find("img")
    return (img.get("src") if img else None)

def download_image(url):
    r = S.get(url, timeout=30)
    r.raise_for_status()
    # lida com avif/webp: se Pillow não tiver, converte via fallback da origem (quando possível)
    img = Image.open(io.BytesIO(r.content))
    if img.mode not in ("RGB","RGBA"):
        img = img.convert("RGB")
    return img

# --------- RENDER ARTE FIXA ---------
BLACK=(0,0,0); WHITE=(255,255,255); RED=(224,31,24); YELLOW=(255,204,0)

def _font(path, size): return ImageFont.truetype(str(Path(path)), size)

def _cover(img, box_w, box_h):
    img = img.convert("RGB")
    ratio = max(box_w / img.width, box_h / img.height)
    im2 = img.resize((int(img.width*ratio), int(img.height*ratio)), Image.LANCZOS)
    cx = (im2.width - box_w)//2
    cy = (im2.height - box_h)//2
    return im2.crop((cx, cy, cx+box_w, cy+box_h))

def _round_rect(draw, xy, r, fill):
    draw.rounded_rectangle(xy, radius=r, fill=fill)

def _draw_centered_text(draw, text, font, y, max_w, fill=BLACK, line_spacing=10):
    # quebra usando bbox real para não passar da caixa
    words = text.strip()
    # tentativa progressiva por largura média de linha
    lines=[]
    # quebra simples em blocos de ~32, depois ajusta se estourar
    for chunk in textwrap.wrap(words, width=32):
        # se ainda estourar largura, diminui o bloco
        if draw.textbbox((0,0), chunk, font=font)[2] > max_w:
            ok=False
            for w in range(31,10,-1):
                alt = textwrap.wrap(chunk, width=w)
                if all(draw.textbbox((0,0), ln, font=font)[2] <= max_w for ln in alt):
                    lines.extend(alt); ok=True; break
            if not ok:
                lines.append(chunk)
        else:
            lines.append(chunk)

    for ln in lines:
        tb = draw.textbbox((0,0), ln, font=font)
        tw = tb[2]-tb[0]; th = tb[3]-tb[1]
        draw.text(((W - tw)//2, y), ln, font=font, fill=fill)
        y += th + line_spacing
    return y

def render_arte_fixa(bg_image, title, category, out_path):
    canvas = Image.new("RGB", (W, H), BLACK)
    draw = ImageDraw.Draw(canvas)

    # foto topo
    photo = _cover(bg_image, W, TOP_H)
    canvas.paste(photo, (0,0))

    # logo central encostando na faixa preta
    try:
        logo = Image.open(LOGO).convert("RGBA")
        lw=360; ratio=lw/logo.width
        logo=logo.resize((lw, int(logo.height*ratio)), Image.LANCZOS)
        lx=(W-logo.width)//2; ly=TOP_H - logo.height//2
        canvas.paste(logo, (lx,ly), mask=logo)
    except Exception as e:
        log.warning(f"⚠️  Erro ao aplicar logo: {e}")

    # pílula vermelha (maior)
    cat = (category or "DESTAQUES").strip().upper()
    f_cat = _font(ROBOTO, 60)
    tw = draw.textbbox((0,0), cat, font=f_cat)[2]
    pill_w = max(int(W*0.60), tw+160)  # ≥ 60% da largura
    pill_h = 118
    pill_y = TOP_H + 64
    pill_x0 = (W - pill_w)//2
    _round_rect(draw, (pill_x0, pill_y, pill_x0+pill_w, pill_y+pill_h), 18, RED)
    tb = draw.textbbox((0,0), cat, font=f_cat)
    draw.text(((W - (tb[2]-tb[0]))//2, pill_y + (pill_h - (tb[3]-tb[1]))//2 - 2), cat, font=f_cat, fill=WHITE)

    # caixa branca do título
    margin_x=44
    box_top = pill_y + pill_h + 42
    box_bottom = box_top + 330
    _round_rect(draw, (margin_x, box_top, W-margin_x, box_bottom), 18, WHITE)

    # título ANTON maior
    title_up = (title or "").strip().upper()
    f_title = _font(ANTON, 64)
    inner_w = W - 2*margin_x - 60
    _draw_centered_text(draw, title_up, f_title, box_top+36, inner_w, fill=BLACK, line_spacing=12)

    # assinatura
    handle = "@BOCANOTROMBONELITORAL"
    f_sign = _font(ROBOTO, 40)
    tb = draw.textbbox((0,0), handle, font=f_sign)
    draw.text(((W - (tb[2]-tb[0]))//2, box_bottom + 26), handle, font=f_sign, fill=YELLOW)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "JPEG", quality=92, optimize=True)
    return out_path

# --------- VÍDEO COM FFMPEG ---------
def make_video_from_image(img_path, out_mp4, seconds=10):
    cmd = [
        "ffmpeg","-y",
        "-loop","1","-r","25",
        "-i", img_path,
        "-stream_loop","-1","-i", AUDIO,
        "-t", str(seconds),
        "-vf","scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        "-c:v","libx264","-pix_fmt","yuv420p",
        "-c:a","aac","-b:a","128k",
        "-movflags","+faststart",
        out_mp4
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out_mp4

# --------- PUBLICAÇÃO FB/IG ---------
def upload_cloudinary(path):
    cloudinary.config(cloud_name=CLOUD_NAME, api_key=CLOUD_KEY, api_secret=CLOUD_SEC, secure=True)
    r = cloudinary.uploader.upload_large(path, resource_type="video", chunk_size=6_000_000)
    return r["secure_url"]

def fb_upload_video(page_id, token, video_url, caption):
    r = S.post(f"https://graph.facebook.com/{API_V}/{page_id}/videos",
               data={"file_url": video_url, "description": caption, "access_token": token}, timeout=60)
    r.raise_for_status()
    return r.json().get("id")

def ig_create_container(ig_id, token, video_url, caption):
    r = S.post(f"https://graph.facebook.com/{API_V}/{ig_id}/media",
               data={"media_type":"REELS","video_url": video_url,
                     "caption": caption, "access_token": token}, timeout=60)
    r.raise_for_status()
    return r.json()["id"]

def ig_publish_container(ig_id, token, creation_id):
    r = S.post(f"https://graph.facebook.com/{API_V}/{ig_id}/media_publish",
               data={"creation_id": creation_id, "access_token": token}, timeout=60)
    r.raise_for_status()
    return r.json()

def ig_check_status(creation_id, token):
    r = S.get(f"https://graph.facebook.com/{API_V}/{creation_id}",
              params={"fields":"status_code,status","access_token": token}, timeout=30)
    r.raise_for_status()
    return r.json()

# --------- WP FETCH ---------
def fetch_wp_posts():
    url = f"{WP_URL}/wp-json/wp/v2/posts"
    params = {
        "per_page": 5,
        "orderby": "date",
        "_fields": "id,title,excerpt,featured_media,content,link,categories"
    }
    r = S.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def get_featured_src(post):
    # tenta no content primeiro
    img = find_first_image_url(post.get("content",{}).get("rendered",""))
    if img: return img
    # fallback: thumbnail padrão WP (usando wp-json do media)
    media_id = post.get("featured_media")
    if media_id:
        try:
            m = S.get(f"{WP_URL}/wp-json/wp/v2/media/{media_id}", timeout=20)
            if m.ok:
                j = m.json()
                return j.get("source_url")
        except Exception:
            pass
    return None

def guess_category_name(cat_id_list):
    if not cat_id_list: return "DESTAQUES"
    # opcional: consultar /wp/v2/categories?id=...
    return "DESTAQUES"

# --------- MAIN LOOP ---------
def process_post(p):
    pid = p["id"]
    title = clean_text(p.get("title",{}).get("rendered","")).strip()
    category = guess_category_name(p.get("categories") or [])
    img_url = get_featured_src(p)

    if not title: 
        log.info(f"→ Post {pid} sem título – pulando"); return

    try:
        if not img_url:
            log.info(f"→ Post {pid} sem imagem – usando fallback cinza")
            bg = Image.new("RGB",(1080,1080),(30,30,30))
        else:
            bg = download_image(img_url)

        arte = str(OUT_DIR / f"arte_{pid}.jpg")
        render_arte_fixa(bg, title, category, arte)
        log.info(f"✅ Arte: {arte}")

        mp4 = str(OUT_DIR / f"reel_{pid}.mp4")
        make_video_from_image(arte, mp4, VIDEO_SECONDS)
        log.info(f"✅ Vídeo: {mp4}")

        # legenda (começo do conteúdo + crédito)
        excerpt = clean_text(p.get("excerpt",{}).get("rendered",""))
        content = clean_text(p.get("content",{}).get("rendered",""))
        snippet = (content or excerpt or title)[:400]
        caption = f"{title}\n\n{snippet}\n\nLeia mais: jornalvozdolitoral.com"

        # Cloudinary
        url = upload_cloudinary(mp4)
        log.info(f"☁️  Cloudinary OK: {url}")

        # Facebook vídeo
        try:
            fb_id = fb_upload_video(PAGE_ID, TOKEN, url, caption)
            log.info(f"📘 Publicado na Página (vídeo): id={fb_id}")
        except Exception as e:
            log.error(f"❌ FB falhou: {e}")

        # Instagram Reels
        try:
            creation = ig_create_container(IG_ID, TOKEN, url, caption)
            # poll até FINISHED (máx ~2 min)
            for _ in range(24):
                st = ig_check_status(creation, TOKEN)
                sc = st.get("status_code")
                log.info(f"⏳ IG status: {sc}")
                if sc in ("FINISHED","ERROR"):
                    break
                time.sleep(5)
            if sc == "FINISHED":
                ig_publish_container(IG_ID, TOKEN, creation)
                log.info("🎬 IG Reels publicado!")
            else:
                log.error(f"❌ IG não finalizou: {st}")
        except Exception as e:
            log.error(f"❌ IG falhou: {e}")

        add_processed(pid)

    except Exception as e:
        log.error(f"❌ Falha post {pid}: {e}")

def main():
    log.info("🚀 Auto Reels (WP→FB+IG) iniciado")
    processed = read_processed()
    posts = fetch_wp_posts()
    log.info(f"→ Recebidos {len(posts)} posts")
    for p in posts:
        if str(p["id"]) in processed:
            continue
        log.info(f"🎨 Arte post {p['id']}…")
        process_post(p)
    log.info("⏳ Fim do ciclo.")

if __name__ == "__main__":
    main()
