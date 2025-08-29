# auto_reels_wp_publish.py (FIX)
# WP -> Arte (padrão Boca) -> MP4 (10s) -> Cloudinary -> FB /videos + IG Reels (polling + retry)

import os, io, re, time, json, subprocess, logging
from logging.handlers import RotatingFileHandler
from urllib.parse import urljoin
import requests
from requests.adapters import HTTPAdapter, Retry
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageOps
from dotenv import load_dotenv
import cloudinary
import cloudinary.uploader

# ============== LOGGING ==============
os.makedirs("out", exist_ok=True)
log_path = os.path.join("out", "auto-reels.log")
logger = logging.getLogger("auto-reels")
logger.setLevel(logging.INFO)
fh = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
fh.setFormatter(logging.Formatter("%Y-%m-%d %H:%M:%S | %(levelname)s | %(message)s"))
logger.addHandler(fh)
ch = logging.StreamHandler()
ch.setFormatter(logging.Formatter("%Y-%m-%d %H:%M:%S | %(levelname)s | %(message)s"))
logger.addHandler(ch)
log = logger.info
err = logger.error

# ============== ENV ==============
load_dotenv()

WP_URL         = os.getenv("WP_URL", "").rstrip("/")
PAGE_ID        = os.getenv("FACEBOOK_PAGE_ID")
IG_ID          = os.getenv("INSTAGRAM_ID")
TOKEN          = os.getenv("USER_ACCESS_TOKEN")
API_V          = os.getenv("API_VERSION", "v23.0")

CLOUD_NAME     = os.getenv("CLOUDINARY_CLOUD_NAME")
CLOUD_KEY      = os.getenv("CLOUDINARY_API_KEY")
CLOUD_SECRET   = os.getenv("CLOUDINARY_API_SECRET")

POSTS_PER_RUN  = int(os.getenv("POSTS_PER_RUN", "5"))
VIDEO_SECONDS  = int(os.getenv("VIDEO_SECONDS", "10"))

FONT_ROBOTO    = os.getenv("FONT_ROBOTO", "Roboto-Bold.ttf")
FONT_ANTON     = os.getenv("FONT_ANTON",  "Anton-Regular.ttf")
LOGO_PATH      = os.getenv("LOGO_PATH", "logo_boca.png")

# ============== HTTP SESSION (Retry) ==============
def make_session():
    s = requests.Session()
    retries = Retry(total=3, backoff_factor=0.5,
                    status_forcelist=(500, 502, 503, 504),
                    allowed_methods=["GET","HEAD"])
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.mount("http://",  HTTPAdapter(max_retries=retries))
    return s

SESSION = make_session()

# ============== WP: buscar posts ==============
def wp_get_latest_posts(n=5):
    url = f"{WP_URL}/wp-json/wp/v2/posts?per_page={n}&orderby=date&_fields=id,title,excerpt,featured_media,content,link,categories"
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    return r.json()

def extract_image_from_content(html):
    try:
        soup = BeautifulSoup(html or "", "html.parser")
        img = soup.find("img")
        if img and img.get("src"):
            return img["src"]
    except Exception:
        pass
    return None

def get_featured_media_url(_post):
    return None  # mantemos simples; priorizamos imagem do conteúdo

# ============== Download imagem ==============
def try_download_image(url):
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    return r.content

def download_image_best_effort(post):
    src = extract_image_from_content(post.get("content", {}).get("rendered", ""))
    if not src:
        src = get_featured_media_url(post)
    if not src:
        return None, None
    try:
        raw = try_download_image(src)
        return src, raw
    except Exception as e:
        log(f"⚠️  Não baixei imagem: {e}")
        return src, None

# ============== Tipografia util ==============
def load_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()

def _bbox_wh(draw, text, font):
    l,t,r,b = draw.textbbox((0,0), text, font=font)
    return (r-l, b-t)

def draw_text_boxed_center(draw, text, font, box_xywh, fill_text, align="center", max_lines=3, line_spacing=1.0):
    x, y, w, h = box_xywh
    words = text.replace("\n"," ").split()
    lines = []
    current = ""
    for wd in words:
        test = (current + " " + wd).strip()
        tw, th = _bbox_wh(draw, test, font)
        if tw <= w:
            current = test
        else:
            if current:
                lines.append(current)
            current = wd
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)

    # altura total
    line_heights = []
    for ln in lines:
        _, lh = _bbox_wh(draw, ln, font)
        line_heights.append(lh)
    total_h = int(sum(line_heights) + (len(line_heights)-1) * (line_heights[0] * (line_spacing-1.0) if line_heights else 0))
    start_y = y + (h - total_h)//2

    cy = start_y
    for ln in lines:
        tw, lh = _bbox_wh(draw, ln, font)
        if align == "center":
            tx = x + (w - tw)//2
        elif align == "left":
            tx = x
        else:
            tx = x + w - tw
        draw.text((tx, cy), ln, fill=fill_text, font=font)
        cy += lh

# ============== Arte (1080x1920) ==============
def build_art(image_bytes_or_none, title, categoria):
    W, H = 1080, 1920
    # canvas RGB (permite paste com mask)
    canvas = Image.new("RGB", (W,H), "#000000")
    draw = ImageDraw.Draw(canvas)

    # Fundo (metade superior) com COVER
    if image_bytes_or_none:
        try:
            img = Image.open(io.BytesIO(image_bytes_or_none)).convert("RGB")
            top_h = H//2
            img_cover = ImageOps.fit(img, (W, top_h), method=Image.LANCZOS, centering=(0.5,0.5))
            canvas.paste(img_cover, (0,0))
        except Exception as e:
            log(f"⚠️  Imagem de fundo inválida, usando preto: {e}")

    # LOGO centralizado (usar paste com mask para evitar 'wrong mode')
    try:
        if os.path.isfile(LOGO_PATH):
            logo = Image.open(LOGO_PATH).convert("RGBA")
            lw = 300
            ratio = lw / logo.width
            logo = logo.resize((lw, int(logo.height*ratio)), Image.LANCZOS)
            lx = (W - logo.width)//2
            ly = (H//2) - logo.height - 20
            canvas.paste(logo, (lx, ly), logo)  # <<<< paste com mask
    except Exception as e:
        log(f"⚠️  Erro ao aplicar logo: {e}")

    # Categoria (caixa vermelha) — Roboto 32
    cat_font = load_font(FONT_ROBOTO, 32)
    cat_text = (categoria or "").upper()[:60] if categoria else ""
    cw, ch = _bbox_wh(draw, cat_text, cat_font)
    pad_x, pad_y = 24, 14
    cat_box_w = cw + 2*pad_x
    cat_box_h = ch + 2*pad_y
    cat_x = (W - cat_box_w)//2
    cat_y = (H//2) + 40
    draw.rectangle([cat_x, cat_y, cat_x+cat_box_w, cat_y+cat_box_h], fill="#e50000")
    draw.text((cat_x+pad_x, cat_y+pad_y), cat_text, font=cat_font, fill="#ffffff")

    # Faixa branca (manchete) — Anton 55
    head_font = load_font(FONT_ANTON, 55)
    margin = 36
    head_x = margin
    head_y = cat_y + cat_box_h + 20
    head_w = W - 2*margin
    head_h = 480
    draw.rectangle([head_x, head_y, head_x+head_w, head_y+head_h], fill="#ffffff")

    # Título CAIXA ALTA
    titulo = BeautifulSoup((title or ""), "html.parser").get_text()
    titulo = re.sub(r"\s+", " ", titulo).strip().upper()
    draw_text_boxed_center(draw, titulo, head_font,
                           (head_x+24, head_y+24, head_w-48, head_h-48),
                           fill_text="#000000", max_lines=4, line_spacing=1.05)

    # Rodapé — @BOCANOTROMBONELITORAL (Roboto 40, amarelo)
    foot_font = load_font(FONT_ROBOTO, 40)
    footer = "@BOCANOTROMBONELITORAL"
    fw, fh = _bbox_wh(draw, footer, foot_font)
    fx = (W - fw)//2
    fy = H - fh - 60
    draw.text((fx, fy), footer, font=foot_font, fill="#ffde00")

    out_img = os.path.join("out", f"arte_{int(time.time())%1000000}.jpg")
    canvas.save(out_img, "JPEG", quality=92, optimize=True)
    return out_img

# ============== Vídeo 10s (com áudio se existir) ==============
def make_video_from_image(img_path, out_path, seconds=10, audio_path="audio_fundo.mp3"):
    cmd = ["ffmpeg","-y","-loop","1","-i",img_path,"-t",str(seconds),"-r","25"]
    if os.path.isfile(audio_path):
        cmd += ["-i", audio_path, "-shortest"]
    cmd += [
        "-vf","scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        "-c:v","libx264","-pix_fmt","yuv420p",
        "-c:a","aac","-b:a","128k",
        out_path
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ============== Cloudinary ==============
def cloudinary_setup():
    cloudinary.config(cloud_name=CLOUD_NAME, api_key=CLOUD_KEY, api_secret=CLOUD_SECRET, secure=True)

def upload_cloudinary_video(path):
    cloudinary_setup()
    up = cloudinary.uploader.upload_large(path, resource_type="video", folder="auto_reels", chunk_size=10_000_000)
    return up.get("secure_url")

# ============== Facebook PAGE /videos ==============
def fb_publish_video(page_id, token, video_url, description):
    url = f"https://graph.facebook.com/{API_V}/{page_id}/videos"
    data = {"file_url": video_url, "description": (description or "")[:2200]}
    r = SESSION.post(url, data=data, params={"access_token": token}, timeout=120)
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text}
    if r.status_code == 200 and "id" in body:
        log(f"📘 Publicado na Página (vídeo): id={body['id']}")
        return True
    err(f"❌ FB /videos {r.status_code} | {json.dumps(body, ensure_ascii=False)}")
    return False

# ============== Instagram Reels (container + polling + retry) ==============
def ig_create_container(ig_id, token, video_url, caption):
    url = f"https://graph.facebook.com/{API_V}/{ig_id}/media"
    data = {"media_type": "REELS", "video_url": video_url}
    if caption:
        data["caption"] = caption[:2200]
    r = SESSION.post(url, data=data, params={"access_token": token}, timeout=120)
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text}
    if r.status_code == 200 and "id" in body:
        return body["id"]
    err(f"❌ IG /media {r.status_code} | {json.dumps(body, ensure_ascii=False)}")
    return None

def ig_get_status(container_id, token):
    url = f"https://graph.facebook.com/{API_V}/{container_id}?fields=status_code"
    r = SESSION.get(url, params={"access_token": token}, timeout=60)
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text}
    return body.get("status_code")

def ig_publish(ig_id, token, container_id):
    url = f"https://graph.facebook.com/{API_V}/{ig_id}/media_publish"
    r = SESSION.post(url, data={"creation_id": container_id}, params={"access_token": token}, timeout=120)
    if r.status_code == 200:
        return True
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text}
    err(f"❌ IG /media_publish {r.status_code} | {json.dumps(body, ensure_ascii=False)}")
    return False

# ============== Categoria (heurística simples) ==============
def guess_categoria(post):
    title = (post.get("title", {}) or {}).get("rendered", "").strip()
    title = BeautifulSoup(title, "html.parser").get_text()
    if not title:
        return "NOTÍCIA"
    m = re.match(r"^([^\s,.;:!?\-–—]+)", title)
    if m:
        raw = m.group(1)
        return raw[:18]
    return "NOTÍCIA"

# ============== PROCESSAR 1 POST ==============
def process_post(post):
    pid   = post["id"]
    title_html = (post.get("title", {}) or {}).get("rendered", "")
    title = BeautifulSoup(title_html, "html.parser").get_text().strip()
    link  = post.get("link", "")
    categoria = guess_categoria(post)

    log(f"🎨 Arte post {pid}…")
    img_url, img_bytes = download_image_best_effort(post)
    if img_bytes is None:
        log("⚠️  Sem imagem válida — seguirei com fundo padrão")
    arte = build_art(img_bytes, title, categoria)
    log(f"✅ Arte: {os.path.abspath(arte)}")

    reel_path = os.path.join("out", f"reel_{pid}.mp4")
    log("🎬 Gerando vídeo 10s…")
    make_video_from_image(arte, reel_path, VIDEO_SECONDS)
    log(f"✅ Vídeo: {os.path.abspath(reel_path)}")

    vurl = upload_cloudinary_video(reel_path)
    caption = f"{title}\n\n{link}".strip()

    fb_publish_video(PAGE_ID, TOKEN, vurl, caption)

    if IG_ID and TOKEN:
        cid = ig_create_container(IG_ID, TOKEN, vurl, caption)
        if cid:
            log(f"⏳ IG container={cid} → aguardando processamento…")
            status = ""
            for i in range(120):
                status = ig_get_status(cid, TOKEN)
                if status in ("FINISHED", "ERROR", "EXPIRED"):
                    break
                if i % 6 == 0:
                    log(f"   IG status={status or '…'} ({i*5}s)")
                time.sleep(5)
            log(f"🧩 IG status final: {status or 'desconhecido'}")
            if status == "FINISHED":
                ok = False
                for t in range(5):
                    if ig_publish(IG_ID, TOKEN, cid):
                        ok = True
                        break
                    log("   IG /media_publish ainda não aceitou (9007?). Retentando…")
                    time.sleep(5 + t*5)
                if ok:
                    log("📷 IG Reels OK")
                else:
                    err("❌ IG /media_publish falhou mesmo após retries")
            else:
                err(f"❌ IG não processou: status={status}")
        else:
            err("❌ IG: falha ao criar container")

    return True

# ============== MAIN ==============
def main():
    log("🚀 Auto Reels (WP→FB+IG) iniciado")
    missing = []
    for k in ("WP_URL","FACEBOOK_PAGE_ID","USER_ACCESS_TOKEN","CLOUDINARY_CLOUD_NAME","CLOUDINARY_API_KEY","CLOUDINARY_API_SECRET"):
        if not os.getenv(k):
            missing.append(k)
    if missing:
        err(f"❌ Variáveis faltando no .env: {', '.join(missing)}")
        return
    try:
        posts = wp_get_latest_posts(POSTS_PER_RUN)
    except Exception as e:
        err(f"❌ WP erro: {e}")
        return
    log(f"🔎 GET {WP_URL}/wp-json/wp/v2/posts?per_page={POSTS_PER_RUN}… → OK")
    log(f"→ Recebidos {len(posts)} posts")
    for p in posts:
        try:
            process_post(p)
        except subprocess.CalledProcessError as fe:
            err(f"❌ ffmpeg falhou post {p['id']}: {fe}")
        except Exception as ex:
            err(f"❌ Falha post {p['id']}: {ex}")

if __name__ == "__main__":
    main()
