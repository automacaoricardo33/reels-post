import os, time, json, io, subprocess, logging, textwrap
from pathlib import Path
from logging.handlers import RotatingFileHandler

import requests
from requests.adapters import HTTPAdapter, Retry
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageOps

import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv

# =========================
# Config & logging
# =========================
BASE = Path(__file__).parent
OUT = BASE / "out"
OUT.mkdir(exist_ok=True)

log = logging.getLogger("auto-reels")
log.setLevel(logging.INFO)
fh = RotatingFileHandler(OUT / "auto_reels.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
ch = logging.StreamHandler()
ch.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
log.addHandler(fh)
log.addHandler(ch)

load_dotenv()

WP_URL           = os.getenv("WP_URL", "").rstrip("/")
USER_TOKEN       = os.getenv("USER_ACCESS_TOKEN")
PAGE_ID          = os.getenv("FACEBOOK_PAGE_ID")
IG_ID            = os.getenv("INSTAGRAM_ID")
API_V            = os.getenv("API_VERSION", "v23.0")

CLOUD_NAME       = os.getenv("CLOUDINARY_CLOUD_NAME")
CLOUD_KEY        = os.getenv("CLOUDINARY_API_KEY")
CLOUD_SECRET     = os.getenv("CLOUDINARY_API_SECRET")

# arte/layout
W, H             = 1080, 1920
TOP_IMG_H        = 1080   # imagem ocupa 1080x1080 no topo (sem distorcer, crop central)
BLACK_Y          = TOP_IMG_H               # começo da faixa preta
PADDING          = 36
CAT_FONT_SIZE    = 48     # categoria robusta
TITLE_FONT_SIZE  = 64     # Anton grande
TITLE_LINE_SP    = 72     # espaçamento
SIGN_FONT_SIZE   = 42

TITLE_BOX_MARGIN_TOP = 160   # distância do topo da faixa preta até a caixa branca
TITLE_BOX_SIDE       = PADDING
TITLE_BOX_PAD        = 36

LOGO_TARGET_W    = 320   # largura final do logo
LOGO_BOTTOM_ON_BLACK_EDGE = True  # encostar logo no início do preto

VIDEO_SECONDS    = 10

FONT_ANTON       = str(BASE / "Anton-Regular.ttf")
FONT_ROBOTO_B    = str(BASE / "Roboto-Black.ttf")
LOGO_PATH        = str(BASE / "logo_boca.png")
AUDIO_PATH       = str(BASE / "audio_fundo.mp3")

PROCESSED_FILE   = BASE / "processed_post_ids.txt"

# Cloudinary config (se existir)
if CLOUD_NAME and CLOUD_KEY and CLOUD_SECRET:
    cloudinary.config(
        cloud_name=CLOUD_NAME,
        api_key=CLOUD_KEY,
        api_secret=CLOUD_SECRET,
        secure=True
    )

# =========================
# HTTP session com retry
# =========================
def make_session():
    s = requests.Session()
    retries = Retry(total=4, backoff_factor=0.8, status_forcelist=(429, 500, 502, 503, 504))
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.mount("http://", HTTPAdapter(max_retries=retries))
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AutoReelsBot/1.0",
        "Accept": "text/html,application/json",
        "Accept-Language": "pt-BR,pt;q=0.9"
    })
    return s

SESSION = make_session()

# =========================
# Utils
# =========================
def load_processed():
    if not PROCESSED_FILE.exists():
        return set()
    return set(x.strip() for x in PROCESSED_FILE.read_text(encoding="utf-8").splitlines() if x.strip())

def save_processed(processed_set):
    PROCESSED_FILE.write_text("\n".join(sorted(processed_set)), encoding="utf-8")

def wp_get_latest(limit=5):
    url = f"{WP_URL}/wp-json/wp/v2/posts?per_page={limit}&orderby=date&_fields=id,title,excerpt,featured_media,content,link,categories"
    r = SESSION.get(url, timeout=20)
    r.raise_for_status()
    return r.json()

def wp_get_media(media_id):
    if not media_id:
        return None
    url = f"{WP_URL}/wp-json/wp/v2/media/{media_id}"
    r = SESSION.get(url, timeout=20)
    if r.status_code != 200:
        return None
    return r.json()

def first_image_from_content(html):
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    img = soup.find("img")
    if img and img.get("src"):
        return img["src"]
    return None

def best_image_for_post(post):
    # 1) do corpo
    src = first_image_from_content(post.get("content", {}).get("rendered", ""))
    if src:
        return src
    # 2) da destacada
    media = wp_get_media(post.get("featured_media"))
    if media and media.get("source_url"):
        return media["source_url"]
    return None

def download_image(url) -> Image.Image:
    """ baixa para bytes e abre em PIL RGB; força RGB para evitar 'wrong mode' """
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    im = Image.open(io.BytesIO(r.content))
    # converte (resolve CMYK, LA etc)
    if im.mode != "RGB":
        im = im.convert("RGB")
    return im

def rounded_rectangle(draw, xy, radius, fill, outline=None, width=1):
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)

def wrap_text(draw, text, font, max_width):
    """quebra manual usando medição de largura real"""
    words = text.split()
    lines = []
    line = ""
    for w in words:
        test = (line + " " + w).strip()
        if draw.textlength(test, font=font) <= max_width:
            line = test
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines

def center_text(draw, text, font, x_center, y, fill):
    w = draw.textlength(text, font=font)
    draw.text((x_center - w/2, y), text, font=font, fill=fill)

# =========================
# Layout da arte (modelo IDEAL)
# =========================
def render_art(src_img: Image.Image, title: str, category: str, out_path: Path):
    # Canvas
    canvas = Image.new("RGB", (W, H), "black")
    draw = ImageDraw.Draw(canvas)

    # --- Foto no topo: preencher 1080x1080 sem distorcer (cover)
    img = src_img.copy()
    # cover crop para 1080x1080
    img = ImageOps.fit(img, (W, TOP_IMG_H), method=Image.LANCZOS, centering=(0.5, 0.35))
    canvas.paste(img, (0, 0))

    # --- Logo: encostar no início do preto
    try:
        logo = Image.open(LOGO_PATH).convert("RGBA")
        ratio = LOGO_TARGET_W / logo.width
        logo = logo.resize((LOGO_TARGET_W, int(logo.height * ratio)), Image.LANCZOS)
        # bottom do logo = BLACK_Y (topo da faixa preta). logo_top = BLACK_Y - logo_h
        lx = (W - logo.width) // 2
        ly = BLACK_Y - logo.height if LOGO_BOTTOM_ON_BLACK_EDGE else BLACK_Y - logo.height - 10
        canvas.paste(logo, (lx, ly), logo)
    except Exception as e:
        log.info("⚠️  Erro ao aplicar logo: %s", e)

    # --- Faixa vermelha da categoria (robusta)
    cat_font = ImageFont.truetype(FONT_ROBOTO_B, CAT_FONT_SIZE)
    cat_txt = (category or "").strip().upper() or "GERAL"
    cat_w = draw.textlength(cat_txt, font=cat_font)
    cat_h = cat_font.size + 22  # faixa mais alta
    faixa_y = BLACK_Y + 40
    faixa_x1 = (W - (cat_w + 80)) // 2
    faixa_x2 = (W + (cat_w + 80)) // 2
    draw.rectangle([faixa_x1, faixa_y, faixa_x2, faixa_y + cat_h], fill="#E41F1F")
    center_text(draw, cat_txt, cat_font, W/2, faixa_y + (cat_h - cat_font.size)/2 - 2, "white")

    # --- Caixa branca do título (com margem & padding)
    title_font = ImageFont.truetype(FONT_ANTON, TITLE_FONT_SIZE)
    box_y = BLACK_Y + TITLE_BOX_MARGIN_TOP
    box_x1 = TITLE_BOX_SIDE
    box_x2 = W - TITLE_BOX_SIDE
    max_text_width = (box_x2 - box_x1) - 2*TITLE_BOX_PAD

    lines = wrap_text(draw, (title or "").strip().upper(), title_font, max_text_width)
    # calculo de altura
    content_h = len(lines) * TITLE_LINE_SP + 12
    box_y2 = box_y + TITLE_BOX_PAD + content_h + TITLE_BOX_PAD

    # fundo branco
    rounded_rectangle(draw, (box_x1, box_y, box_x2, box_y2), radius=18, fill="white")

    # escreve linhas centralizadas
    ty = box_y + TITLE_BOX_PAD
    for ln in lines:
        center_text(draw, ln, title_font, W/2, ty, "black")
        ty += TITLE_LINE_SP

    # --- Assinatura amarela no rodapé
    sig_font = ImageFont.truetype(FONT_ROBOTO_B, SIGN_FONT_SIZE)
    sig_txt = "@BOCANOTROMBONELITORAL"
    sig_w = draw.textlength(sig_txt, font=sig_font)
    sy = H - 42 - sig_font.size
    draw.text(((W - sig_w)/2, sy), sig_txt, font=sig_font, fill="#FFD700")

    # salva
    canvas.save(out_path, "JPEG", quality=95)

# =========================
# Vídeo (ffmpeg)
# =========================
def make_video_from_image(img_path: Path, out_mp4: Path, seconds=VIDEO_SECONDS):
    # Com áudio se existir; senão silente
    if Path(AUDIO_PATH).exists():
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-t", str(seconds),
            "-i", str(img_path),
            "-ss", "0",
            "-i", AUDIO_PATH,
            "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25",
            "-c:a", "aac", "-b:a", "128k",
            str(out_mp4)
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-t", str(seconds),
            "-i", str(img_path),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25",
            str(out_mp4)
        ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# =========================
# Publicadores
# =========================
GRAPH = f"https://graph.facebook.com/{API_V}"

def fb_publish_page_video(mp4_path: Path, message: str):
    url = f"{GRAPH}/{PAGE_ID}/videos"
    with open(mp4_path, "rb") as f:
        files = {"source": f}
        data = {"access_token": USER_TOKEN, "description": message}
        r = SESSION.post(url, files=files, data=data, timeout=300)
    r.raise_for_status()
    return r.json().get("id")

def cloudinary_upload_video(mp4_path: Path) -> str:
    res = cloudinary.uploader.upload(
        str(mp4_path),
        resource_type="video",
        folder="auto_reels",
        overwrite=True
    )
    return res["secure_url"]

def ig_create_container(video_url: str, caption: str):
    url = f"{GRAPH}/{IG_ID}/media"
    data = {
        "access_token": USER_TOKEN,
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption
    }
    r = SESSION.post(url, data=data, timeout=60)
    r.raise_for_status()
    return r.json()["id"]

def ig_publish_when_ready(creation_id: str, max_wait=180):
    # Tenta publicar com pequenos waits para evitar "Media ID is not available"
    start = time.time()
    while True:
        pub = SESSION.post(
            f"{GRAPH}/{IG_ID}/media_publish",
            data={"creation_id": creation_id, "access_token": USER_TOKEN},
            timeout=60
        )
        if pub.status_code == 200:
            return pub.json().get("id")
        try:
            err = pub.json().get("error", {})
        except Exception:
            err = {}
        code = err.get("code")
        subc = err.get("error_subcode")
        if code == 9007 or subc in (2207007, 2207027):
            # não pronto ainda
            if time.time() - start > max_wait:
                pub.raise_for_status()
            time.sleep(6)
            continue
        pub.raise_for_status()

# =========================
# Main loop (uma passada)
# =========================
def process_once():
    if not (WP_URL and USER_TOKEN and PAGE_ID and IG_ID):
        raise SystemExit("Faltam variáveis no .env (WP_URL, USER_ACCESS_TOKEN, FACEBOOK_PAGE_ID, INSTAGRAM_ID).")

    log.info("🔎 GET %s/wp-json/wp/v2/posts?per_page=5… → OK", WP_URL)
    posts = wp_get_latest(5)
    log.info("→ Recebidos %d posts", len(posts))

    processed = load_processed()

    for p in posts:
        pid = str(p["id"])
        if pid in processed:
            continue

        # escolhe imagem
        img_url = best_image_for_post(p)
        title = BeautifulSoup(p.get("title", {}).get("rendered", ""), "html.parser").get_text(" ", strip=True)
        category = "Geral"
        try:
            cats = p.get("categories") or []
            if cats:
                # se precisar, busque nomes depois; aqui usamos "Geral" se vazio
                pass
        except Exception:
            pass

        try:
            log.info("🎨 Arte post %s…", pid)
            src = None
            if img_url:
                try:
                    src = download_image(img_url)
                except Exception as e:
                    log.info("⚠️  Não baixei imagem: %s", e)
            if src is None:
                # placeholder neutro (fundo cinza)
                src = Image.new("RGB", (1200, 800), "#d9d9d9")

            arte_path = OUT / f"arte_{pid}.jpg"
            render_art(src, title, category, arte_path)
            log.info("✅ Arte: %s", arte_path)

            # vídeo
            mp4_path = OUT / f"reel_{pid}.mp4"
            log.info("🎬 Gerando vídeo 10s…")
            make_video_from_image(arte_path, mp4_path, VIDEO_SECONDS)
            log.info("✅ Vídeo: %s", mp4_path)

            # Facebook
            fb_id = fb_publish_page_video(mp4_path, title)
            log.info("📘 Publicado na Página (vídeo): id=%s", fb_id)

            # Instagram (via Cloudinary)
            if CLOUD_NAME and CLOUD_KEY and CLOUD_SECRET:
                try:
                    vurl = cloudinary_upload_video(mp4_path)
                    creation = ig_create_container(vurl, title + "\n\n#bocanotrombone #litoralnorte #news")
                    ig_id = ig_publish_when_ready(creation)
                    log.info("📷 Reels publicado: id=%s", ig_id)
                except Exception as e:
                    log.error("❌ IG Reels falhou: %s", e)
            else:
                log.info("ℹ️  Sem credenciais do Cloudinary no .env — pulando publicação no IG.")

            processed.add(pid)
            save_processed(processed)

        except subprocess.CalledProcessError as e:
            log.error("❌ ffmpeg falhou (%s) — post %s", e, pid)
        except requests.HTTPError as e:
            log.error("❌ HTTP error — %s", e)
        except Exception as e:
            log.error("❌ Falha post %s: %s", pid, e)

def main():
    log.info("🚀 Auto Reels (WP→FB+IG) iniciado")
    while True:
        process_once()
        log.info("⏳ Aguardando 20s…")
        time.sleep(20)

if __name__ == "__main__":
    main()
