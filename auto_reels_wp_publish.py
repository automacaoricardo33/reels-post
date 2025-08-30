# -*- coding: utf-8 -*-
"""
Auto Reels (WP → FB + IG) – FULL (fix fontes e URLs relativas)
- Busca posts do WordPress
- Prefere imagem do conteúdo; se vier URL relativo, corrige com base do WP
- Arte no padrão: topo imagem, faixa vermelha robusta (categoria), caixa branca com título (quebra sem vazar), logo acima da faixa, rodapé @BOCANOTROMBONELITORAL
- Vídeo 10s (com áudio opcional audio_fundo.mp3)
- Cloudinary -> Facebook (/videos) -> Instagram (REELS com espera FINISHED)
- Salva IDs processados
Requisitos:
  pip install pillow requests python-dotenv cloudinary beautifulsoup4
  FFmpeg no PATH
  .env: WP_URL, USER_ACCESS_TOKEN, FACEBOOK_PAGE_ID, INSTAGRAM_ID,
        CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET
Arquivos: (opcional) logo_boca.png, Anton-Regular.ttf, Roboto-Black.ttf, audio_fundo.mp3
"""

import os, io, time, json, math, subprocess, textwrap, datetime
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv

# ====== ENV ======
load_dotenv()
WP_URL      = os.getenv("WP_URL", "").rstrip("/")
TOKEN       = os.getenv("USER_ACCESS_TOKEN", "")
PAGE_ID     = os.getenv("FACEBOOK_PAGE_ID", "")
IG_ID       = os.getenv("INSTAGRAM_ID", "")
API_V       = os.getenv("API_VERSION", "v23.0")

CLOUD_NAME  = os.getenv("CLOUDINARY_CLOUD_NAME", "")
CLOUD_KEY   = os.getenv("CLOUDINARY_API_KEY", "")
CLOUD_SEC   = os.getenv("CLOUDINARY_API_SECRET", "")

BASE = Path(__file__).parent
OUT  = BASE / "out"
OUT.mkdir(exist_ok=True)

# ====== AJUSTES VISUAIS (mexa só aqui) ======
W, H                 = 1080, 1920
TOP_IMAGE_H          = int(H * 0.50)

LOGO_PATH            = BASE / "logo_boca.png"  # se não existir, ignora
LOGO_MAX_W           = 300
LOGO_Y_FROM_TOPIMG   = TOP_IMAGE_H - 90        # menor = sobe / maior = desce

RED_BAR_H            = 260
CATEGORY_TXT_SIZE    = 58
CATEGORY_TXT_MARGINX = 36

WHITE_BOX_Y          = TOP_IMAGE_H + RED_BAR_H
WHITE_BOX_H          = 520
WHITE_BOX_MARGIN     = 36

TITLE_MAX_FONTSIZE   = 64
TITLE_MIN_FONTSIZE   = 42
TITLE_LINE_SPACING   = 1.05
TITLE_MAX_LINES      = 6
TITLE_COLOR          = (0, 0, 0)

RODAPE_TXT           = "@BOCANOTROMBONELITORAL"
RODAPE_SIZE          = 42
RODAPE_Y             = H - 120

BG_FILL_COLOR        = (0, 0, 0)
RED_COLOR            = (229, 0, 0)
WHITE_COLOR          = (255, 255, 255)

FONT_ANTON_PATH      = BASE / "Anton-Regular.ttf"
FONT_ROBOTO_PATH     = BASE / "Roboto-Black.ttf"

VIDEO_SECONDS        = 10
SLEEP_BETWEEN_RUNS   = 300

# ====== LOG ======
def log(msg, level="INFO"):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} | {level} | {msg}", flush=True)

# ====== HTTP SESSION ======
SESSION = requests.Session()
ADAPTER = requests.adapters.HTTPAdapter(max_retries=3)
SESSION.mount("http://", ADAPTER)
SESSION.mount("https://", ADAPTER)
SESSION.headers.update({"User-Agent": "AutoReelsBot/1.0"})

# ====== CLOUDINARY ======
def cloudinary_init():
    if not (CLOUD_NAME and CLOUD_KEY and CLOUD_SEC):
        return False
    try:
        import cloudinary
        cloudinary.config(
            cloud_name=CLOUD_NAME,
            api_key=CLOUD_KEY,
            api_secret=CLOUD_SEC,
            secure=True
        )
        return True
    except Exception as e:
        log(f"Cloudinary init falhou: {e}", "ERROR")
        return False

def cloudinary_upload_video(path: Path) -> str:
    import cloudinary.uploader
    res = cloudinary.uploader.upload_large(
        str(path), resource_type="video",
        timeout=600, folder="auto_reels", overwrite=True
    )
    return res["secure_url"]

# ====== WP ======
def wp_latest_posts(limit=5):
    url = f"{WP_URL}/wp-json/wp/v2/posts?per_page={limit}&orderby=date&_fields=id,title,excerpt,featured_media,content,link,categories"
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    return r.json()

def pick_category_name(post):
    t = post.get("title", {}).get("rendered", "") or ""
    if "Polícia" in t or "🚔" in t or "🚨" in t:
        return "POLÍCIA"
    if "Pronto Falei" in t or "‼️" in t:
        return "PRONTO FALEI"
    return "NOTÍCIAS"

def extract_title_text(post) -> str:
    from html import unescape
    raw = post.get("title", {}).get("rendered", "") or ""
    soup = BeautifulSoup(raw, "html.parser")
    txt = soup.get_text(" ", strip=True)
    return unescape(txt)

def first_image_from_content(post) -> str | None:
    html = post.get("content", {}).get("rendered", "") or ""
    soup = BeautifulSoup(html, "html.parser")
    img = soup.find("img")
    if img and img.get("src"):
        src = img["src"]
        # corrige URL relativa
        if src.startswith("//"):
            return "https:" + src
        if src.startswith("/"):
            return urljoin(WP_URL, src)
        if src.lower().startswith("http"):
            return src
        # qualquer outra coisa, tenta juntar
        return urljoin(WP_URL + "/", src)
    return None

def download_image_rgb(url: str) -> Image.Image | None:
    try:
        r = SESSION.get(url, timeout=30)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content))
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        else:
            img = img.convert("RGB")
        return img
    except Exception as e:
        log(f"⚠️  Não baixei imagem: {e}", "INFO")
        return None

def object_fit_cover(src: Image.Image, box_w: int, box_h: int) -> Image.Image:
    sw, sh = src.size
    scale = max(box_w / sw, box_h / sh)
    nw, nh = int(sw * scale), int(sh * scale)
    img = src.resize((nw, nh), Image.LANCZOS)
    x = (nw - box_w) // 2
    y = (nh - box_h) // 2
    return img.crop((x, y, x + box_w, y + box_h))

# ====== FONTES (com fallback) ======
def try_truetype(paths, size):
    """
    paths: lista de caminhos possíveis (.ttf). Retorna a primeira que abrir.
    fallback final: DejaVuSans.ttf do PIL (vem junto) — evita 'cannot open resource'
    """
    for p in paths:
        try:
            return ImageFont.truetype(str(p), size=size, layout_engine=ImageFont.LAYOUT_RAQM)
        except Exception:
            continue
    # fallback PIL
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size, layout_engine=ImageFont.LAYOUT_RAQM)
    except Exception:
        # último recurso: fonte PIL default (sem RAQM)
        return ImageFont.load_default()

def font_anton(size):
    candidates = [FONT_ANTON_PATH, "/usr/share/fonts/truetype/anton/Anton-Regular.ttf"]
    return try_truetype(candidates, size)

def font_roboto_black(size):
    candidates = [FONT_ROBOTO_PATH, "/usr/share/fonts/truetype/roboto/Roboto-Black.ttf"]
    return try_truetype(candidates, size)

# ====== TEXTO ======
def draw_centered_text(draw, text, font, box, fill=(255,255,255), line_spacing=1.0):
    x0, y0, x1, y1 = box
    w_box = x1 - x0
    lines = text.split("\n")
    # altura
    ascent, descent = font.getmetrics()
    line_h = ascent + descent
    total_h = int(sum((line_h * line_spacing) for _ in lines))
    y = y0 + ( (y1 - y0) - total_h ) // 2
    for line in lines:
        tw = font.getlength(line)
        tx = x0 + (w_box - int(tw)) // 2
        draw.text((tx, y), line, font=font, fill=fill)
        y += int(line_h * line_spacing)

def fit_title_in_box(draw, text, font_builder, box, max_size, min_size, max_lines, line_spacing=1.05):
    x0, y0, x1, y1 = box
    Wb, Hb = x1 - x0, y1 - y0
    for size in range(max_size, min_size - 1, -2):
        font = font_builder(size)
        # wrap por largura
        wrapped = []
        for paragraph in text.split("\n"):
            words = paragraph.strip().split()
            if not words:
                wrapped.append("")
                continue
            line = words[0]
            for w in words[1:]:
                if font.getlength(line + " " + w) <= (Wb - 2):
                    line += " " + w
                else:
                    wrapped.append(line)
                    line = w
            wrapped.append(line)
        if len(wrapped) > max_lines:
            wrapped = wrapped[:max_lines-1] + [wrapped[max_lines-1] + "…"]
        ascent, descent = font.getmetrics()
        line_h = ascent + descent
        total_h = int(len(wrapped) * line_h * line_spacing)
        if total_h <= Hb:
            return font, "\n".join(wrapped)
    # fallback
    return font_builder(min_size), textwrap.shorten(text, width=120, placeholder="…")

# ====== ARTE ======
def render_art(post, save_path: Path) -> Path:
    canvas = Image.new("RGB", (W, H), BG_FILL_COLOR)
    draw = ImageDraw.Draw(canvas)

    # imagem do conteúdo
    img_url = first_image_from_content(post)
    if img_url:
        bg = download_image_rgb(img_url)
    else:
        bg = None

    if bg is None:
        top = Image.new("RGB", (W, TOP_IMAGE_H), (20,20,20))
    else:
        top = object_fit_cover(bg, W, TOP_IMAGE_H)
    canvas.paste(top, (0, 0))

    # logo (opcional)
    if LOGO_PATH.exists():
        try:
            logo = Image.open(LOGO_PATH).convert("RGBA")
            scale = min(LOGO_MAX_W / logo.width, 1.0)
            nw, nh = int(logo.width * scale), int(logo.height * scale)
            logo = logo.resize((nw, nh), Image.LANCZOS)
            lx = (W - nw)//2
            ly = max(0, LOGO_Y_FROM_TOPIMG - nh)
            canvas.paste(logo, (lx, ly), logo)
        except Exception as e:
            log(f"⚠️  Erro ao aplicar logo: {e}", "INFO")

    # faixa vermelha (categoria)
    y_red0 = TOP_IMAGE_H
    y_red1 = TOP_IMAGE_H + RED_BAR_H
    draw.rectangle([0, y_red0, W, y_red1], fill=RED_COLOR)

    categoria = pick_category_name(post).upper()
    cat_font = font_roboto_black(CATEGORY_TXT_SIZE)
    cat_w = cat_font.getlength(categoria)
    ascent, descent = cat_font.getmetrics()
    cat_h = ascent + descent
    cat_x = (W - int(cat_w))//2
    cat_y = y_red0 + (RED_BAR_H - cat_h)//2
    draw.text((cat_x, cat_y), categoria, font=cat_font, fill=WHITE_COLOR)

    # caixa branca (título)
    y_white0 = y_red1
    y_white1 = y_red1 + WHITE_BOX_H
    draw.rectangle([0, y_white0, W, y_white1], fill=WHITE_COLOR)

    title = extract_title_text(post)
    title_box = (WHITE_BOX_MARGIN, y_white0 + WHITE_BOX_MARGIN,
                 W - WHITE_BOX_MARGIN, y_white1 - WHITE_BOX_MARGIN)
    t_font, wrapped = fit_title_in_box(
        draw, title, font_anton, title_box,
        max_size=TITLE_MAX_FONTSIZE, min_size=TITLE_MIN_FONTSIZE,
        max_lines=TITLE_MAX_LINES, line_spacing=TITLE_LINE_SPACING
    )
    draw_centered_text(draw, wrapped, t_font, title_box, fill=TITLE_COLOR, line_spacing=TITLE_LINE_SPACING)

    # rodapé
    rod_font = font_roboto_black(RODAPE_SIZE)
    rod_w = rod_font.getlength("@BOCANOTROMBONELITORAL")
    ra, rd = rod_font.getmetrics()
    rod_h = ra + rd
    rx = (W - int(rod_w))//2
    ry = RODAPE_Y
    draw.text((rx, ry), "@BOCANOTROMBONELITORAL", font=rod_font, fill=WHITE_COLOR)

    canvas.save(save_path, "JPEG", quality=92, optimize=True, progressive=True)
    return save_path

# ====== VÍDEO ======
def make_video(jpg: Path, mp4_out: Path, seconds=10):
    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(jpg)]
    audio = BASE / "audio_fundo.mp3"
    if audio.exists():
        cmd += ["-i", str(audio), "-shortest"]
    cmd += ["-t", str(seconds), "-r", "25", "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio.exists():
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    cmd += [str(mp4_out)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return mp4_out

# ====== FACEBOOK ======
def fb_publish_video(page_id: str, token: str, file_url: str, description: str) -> str | None:
    url = f"https://graph.facebook.com/{API_V}/{page_id}/videos"
    data = {"access_token": token, "file_url": file_url, "description": description[:2200]}
    r = SESSION.post(url, data=data, timeout=120)
    if r.status_code == 200:
        return r.json().get("id")
    log(f"❌ FB /videos falhou: {r.status_code} | {r.text}", "ERROR")
    return None

# ====== INSTAGRAM ======
def ig_create_container(ig_id: str, token: str, video_url: str, caption: str) -> str | None:
    url = f"https://graph.facebook.com/{API_V}/{ig_id}/media"
    data = {"access_token": token, "media_type": "REELS", "video_url": video_url, "caption": caption[:2200]}
    r = SESSION.post(url, data=data, timeout=120)
    if r.status_code == 200:
        return r.json().get("id")
    log(f"❌ IG /media falhou: {r.status_code} | {r.text}", "ERROR")
    return None

def ig_wait_finished(container_id: str, token: str, max_wait=480) -> bool:
    url = f"https://graph.facebook.com/{API_V}/{container_id}"
    waited = 0
    while waited <= max_wait:
        r = SESSION.get(url, params={"access_token": token, "fields": "status_code,status"}, timeout=60)
        if r.status_code == 200:
            st = r.json().get("status_code")
            log(f"⏳ IG status: {st}", "INFO")
            if st == "FINISHED":
                return True
            if st == "ERROR":
                return False
        time.sleep(10)
        waited += 10
    return False

def ig_publish(ig_id: str, token: str, creation_id: str) -> bool:
    url = f"https://graph.facebook.com/{API_V}/{ig_id}/media_publish"
    data = {"access_token": token, "creation_id": creation_id}
    r = SESSION.post(url, data=data, timeout=120)
    if r.status_code == 200:
        return True
    log(f"❌ IG /media_publish falhou: {r.status_code} | {r.text}", "ERROR")
    return False

# ====== PROCESSADOS ======
PROC_FILE = OUT / "processed.json"
def load_processed():
    if PROC_FILE.exists():
        try:
            return set(json.loads(PROC_FILE.read_text(encoding="utf-8")))
        except:
            return set()
    return set()

def save_processed(s: set):
    PROC_FILE.write_text(json.dumps(sorted(list(s))), encoding="utf-8")

# ====== LOOP ======
def process_once():
    for k, v in [("WP_URL", WP_URL), ("TOKEN", TOKEN), ("PAGE_ID", PAGE_ID), ("IG_ID", IG_ID),
                 ("CLOUDINARY_CLOUD_NAME", CLOUD_NAME), ("CLOUDINARY_API_KEY", CLOUD_KEY), ("CLOUDINARY_API_SECRET", CLOUD_SEC)]:
        if not v:
            log(f"❌ Variável ausente: {k}", "ERROR")
            return

    posts = wp_latest_posts(limit=5)
    log(f"→ Recebidos {len(posts)} posts", "INFO")

    processed = load_processed()
    cloud_ok = cloudinary_init()

    for post in posts:
        pid = str(post["id"])
        if pid in processed:
            continue

        try:
            log(f"🎨 Arte post {pid}…", "INFO")
            arte_path = OUT / f"arte_{pid}.jpg"
            render_art(post, arte_path)
            log(f"✅ Arte: {arte_path}", "INFO")

            mp4_path = OUT / f"reel_{pid}.mp4"
            log("🎬 Gerando vídeo 10s…", "INFO")
            make_video(arte_path, mp4_path, VIDEO_SECONDS)
            log(f"✅ Vídeo: {mp4_path}", "INFO")

            if not cloud_ok:
                log("❌ Cloudinary não configurado.", "ERROR")
                return
            url_video = cloudinary_upload_video(mp4_path)

            title = extract_title_text(post)
            link  = post.get("link", "")
            categoria = pick_category_name(post)
            caption = f"{title}\n\nCategoria: {categoria}\nLeia mais: {link}\n#BocaNoTrombone #Ilhabela"

            vid_id = fb_publish_video(PAGE_ID, TOKEN, url_video, caption)
            if vid_id:
                log(f"📘 Publicado na Página (vídeo): id={vid_id}", "INFO")

            creation = ig_create_container(IG_ID, TOKEN, url_video, caption)
            if creation:
                if ig_wait_finished(creation, TOKEN, max_wait=480):
                    if ig_publish(IG_ID, TOKEN, creation):
                        log("🎬 IG Reels publicado!", "INFO")
                    else:
                        log("⚠️ IG publish falhou mesmo após FINISHED.", "ERROR")
                else:
                    log("⚠️ IG não ficou FINISHED a tempo.", "ERROR")

            processed.add(pid)
            save_processed(processed)
            time.sleep(2)

        except subprocess.CalledProcessError as e:
            log(f"❌ FFmpeg falhou: {e}", "ERROR")
        except requests.RequestException as e:
            log(f"❌ HTTP falhou: {e}", "ERROR")
        except Exception as e:
            log(f"❌ Falha post {pid}: {e}", "ERROR")

def main():
    log("🚀 Auto Reels (WP→FB+IG) iniciado", "INFO")
    while True:
        process_once()
        log("⏳ Fim do ciclo.", "INFO")
        time.sleep(SLEEP_BETWEEN_RUNS)

if __name__ == "__main__":
    main()
# -*- coding: utf-8 -*-
"""
Auto Reels (WP → FB + IG) – FULL
- Busca posts do WordPress
- Extrai a 1ª imagem do conteúdo (preferência) ou usa destaque
- Gera arte no padrão solicitado (logo acima da faixa vermelha, faixa vermelha robusta, título ajusta fonte sem vazar)
- Adiciona rodapé @BOCANOTROMBONELITORAL
- Renderiza VÍDEO (9:16, 10s) com áudio opcional (se audio_fundo.mp3 existir)
- Sobe no Cloudinary (gera URL pública)
- Publica no Facebook (vídeo) e Instagram (Reels) com espera até FINISHED
- Guarda IDs processados em out/processed.json
Requisitos:
  pip install pillow requests python-dotenv cloudinary beautifulsoup4
  FFmpeg no PATH
  .env com: WP_URL, USER_ACCESS_TOKEN, FACEBOOK_PAGE_ID, INSTAGRAM_ID,
            CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET
Arquivos locais:
  - logo_boca.png (PNG com transparência)
  - Anton-Regular.ttf (fonte título)
  - Roboto-Black.ttf (fonte categoria e rodapé)
  - audio_fundo.mp3 (opcional)
"""

import os, io, time, json, math, subprocess, textwrap, datetime
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageOps
from dotenv import load_dotenv

# ====== CONFIG BÁSICA ======
load_dotenv()
WP_URL      = os.getenv("WP_URL", "").rstrip("/")
TOKEN       = os.getenv("USER_ACCESS_TOKEN", "")
PAGE_ID     = os.getenv("FACEBOOK_PAGE_ID", "")
IG_ID       = os.getenv("INSTAGRAM_ID", "")
API_V       = os.getenv("API_VERSION", "v23.0")

CLOUD_NAME  = os.getenv("CLOUDINARY_CLOUD_NAME", "")
CLOUD_KEY   = os.getenv("CLOUDINARY_API_KEY", "")
CLOUD_SEC   = os.getenv("CLOUDINARY_API_SECRET", "")

BASE = Path(__file__).parent
OUT  = BASE / "out"
OUT.mkdir(exist_ok=True)

# ====== AJUSTES VISUAIS – (você pode mexer SÓ AQUI) ======
W, H                 = 1080, 1920               # vídeo/arte 9:16
TOP_IMAGE_H          = int(H * 0.50)            # altura da imagem de topo
LOGO_PATH            = BASE / "logo_boca.png"   # seu logo (PNG)
LOGO_MAX_W           = 300                      # largura máx do logo
LOGO_Y_FROM_TOPIMG   = TOP_IMAGE_H - 90         # Y do logo (fica acima da faixa vermelha)
RED_BAR_H            = 260                      # altura da faixa vermelha (categoria) – “mais robusta”
CATEGORY_TXT_SIZE    = 58                       # fonte base da categoria (vai em branco)
CATEGORY_TXT_MARGINX = 36                       # margem horizontal dentro da faixa vermelha

WHITE_BOX_Y          = TOP_IMAGE_H + RED_BAR_H  # começa logo após faixa vermelha
WHITE_BOX_H          = 520                      # altura da caixa branca (texto)
WHITE_BOX_MARGIN     = 36                       # margem interna da caixa branca

TITLE_MAX_FONTSIZE   = 64                       # teto da fonte do título (Anton)
TITLE_MIN_FONTSIZE   = 42                       # piso para não ficar pequeno
TITLE_LINE_SPACING   = 1.05                     # espaçamento entre linhas
TITLE_MAX_LINES      = 6                        # limite de linhas
TITLE_COLOR          = (0, 0, 0)

RODAPE_TXT           = "@BOCANOTROMBONELITORAL" # rodapé
RODAPE_SIZE          = 42                       # tamanho da fonte do rodapé
RODAPE_Y             = H - 120                  # posição Y do rodapé

BG_FILL_COLOR        = (0, 0, 0)                # cor de fundo
RED_COLOR            = (229, 0, 0)              # vermelho da faixa
WHITE_COLOR          = (255, 255, 255)

FONT_ANTON_PATH      = BASE / "Anton-Regular.ttf"
FONT_ROBOTO_PATH     = BASE / "Roboto-Black.ttf"

VIDEO_SECONDS        = 10                       # duração do vídeo
SLEEP_BETWEEN_RUNS   = 300                      # loop: espera 5 min entre ciclos

# ====== LOG SIMPLES ======
def log(msg, level="INFO"):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} | {level} | {msg}", flush=True)

# ====== SESSÃO HTTP COM RETRY ======
SESSION = requests.Session()
ADAPTER = requests.adapters.HTTPAdapter(max_retries=3)
SESSION.mount("http://", ADAPTER)
SESSION.mount("https://", ADAPTER)
SESSION.headers.update({"User-Agent": "AutoReelsBot/1.0"})

# ====== CLOUDINARY ======
def cloudinary_init():
    if not (CLOUD_NAME and CLOUD_KEY and CLOUD_SEC):
        return False
    try:
        import cloudinary
        cloudinary.config(
            cloud_name=CLOUD_NAME,
            api_key=CLOUD_KEY,
            api_secret=CLOUD_SEC,
            secure=True
        )
        return True
    except Exception as e:
        log(f"Cloudinary init falhou: {e}", "ERROR")
        return False

def cloudinary_upload_video(path: Path) -> str:
    import cloudinary.uploader
    res = cloudinary.uploader.upload_large(
        str(path),
        resource_type="video",
        timeout=600,
        folder="auto_reels",
        overwrite=True
    )
    return res["secure_url"]

# ====== WP FETCH ======
def wp_latest_posts(limit=5):
    url = f"{WP_URL}/wp-json/wp/v2/posts?per_page={limit}&orderby=date&_fields=id,title,excerpt,featured_media,content,link,categories"
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    return r.json()

def pick_category_name(post):
    # tenta extrair alguma categoria textual do título/emoji; senão "Notícias"
    t = post.get("title", {}).get("rendered", "") or ""
    # se vier prefixos como "🚨", "‼️", etc, você pode mapear aqui:
    if "Polícia" in t or "🚔" in t or "🚨" in t:
        return "POLÍCIA"
    if "Pronto Falei" in t or "‼️" in t:
        return "PRONTO FALEI"
    return "NOTÍCIAS"

def extract_title_text(post) -> str:
    from html import unescape
    raw = post.get("title", {}).get("rendered", "") or ""
    # tira HTML simples
    soup = BeautifulSoup(raw, "html.parser")
    txt = soup.get_text(" ", strip=True)
    return unescape(txt)

def find_first_image_in_content(post) -> str | None:
    html = post.get("content", {}).get("rendered", "") or ""
    soup = BeautifulSoup(html, "html.parser")
    img = soup.find("img")
    if img and img.get("src"):
        return img["src"]
    return None

def download_image_rgb(url: str) -> Image.Image | None:
    try:
        r = SESSION.get(url, timeout=30)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content))
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        else:
            img = img.convert("RGB")
        return img
    except Exception as e:
        log(f"⚠️  Não baixei imagem: {e}", "INFO")
        return None

def object_fit_cover(src: Image.Image, box_w: int, box_h: int) -> Image.Image:
    # recorte tipo CSS object-fit: cover mantendo centro
    sw, sh = src.size
    scale = max(box_w / sw, box_h / sh)
    nw, nh = int(sw * scale), int(sh * scale)
    img = src.resize((nw, nh), Image.LANCZOS)
    x = (nw - box_w) // 2
    y = (nh - box_h) // 2
    return img.crop((x, y, x + box_w, y + box_h))

# ====== TIPOGRAFIA ======
def load_font(path: Path, size: int, fallback="DejaVuSans.ttf"):
    try:
        return ImageFont.truetype(str(path), size=size, layout_engine=ImageFont.LAYOUT_RAQM)
    except:
        return ImageFont.truetype(fallback, size=size)

def draw_centered_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, box, fill=(255,255,255), line_spacing=1.0):
    x0, y0, x1, y1 = box
    w_box = x1 - x0
    h_box = y1 - y0
    lines = text.split("\n")
    # calcula altura total
    line_h = font.getbbox("Ay")[3] - font.getbbox("Ay")[1]
    total_h = int(sum((line_h * line_spacing) for _ in lines))
    y = y0 + (h_box - total_h) // 2
    for line in lines:
        tw = font.getlength(line)
        tx = x0 + (w_box - int(tw)) // 2
        draw.text((tx, y), line, font=font, fill=fill)
        y += int(line_h * line_spacing)

def fit_title_in_box(draw, text, font_path, box, max_size, min_size, max_lines, line_spacing=1.05):
    x0, y0, x1, y1 = box
    Wb, Hb = x1 - x0, y1 - y0
    # tenta do maior pro menor
    for size in range(max_size, min_size - 1, -2):
        font = load_font(font_path, size)
        # tenta quebrar em linhas que caibam na largura
        wrapped = []
        for paragraph in text.split("\n"):
            # quebra por largura
            words = paragraph.strip().split()
            if not words:
                wrapped.append("")
                continue
            line = words[0]
            for w in words[1:]:
                if font.getlength(line + " " + w) <= (Wb - 2):
                    line += " " + w
                else:
                    wrapped.append(line)
                    line = w
            wrapped.append(line)
        # corta se exceder max_lines
        if len(wrapped) > max_lines:
            wrapped = wrapped[:max_lines-1] + [wrapped[max_lines-1] + "…"]
        # mede altura total
        line_h = font.getbbox("Ay")[3] - font.getbbox("Ay")[1]
        total_h = int(len(wrapped) * line_h * line_spacing)
        if total_h <= Hb:
            return font, "\n".join(wrapped)
    # fallback
    return load_font(font_path, min_size), textwrap.shorten(text, width=120, placeholder="…")

# ====== ARTE ======
def render_art(post, save_path: Path) -> Path:
    # base
    canvas = Image.new("RGB", (W, H), BG_FILL_COLOR)
    draw = ImageDraw.Draw(canvas)

    # imagem topo (cover)
    img_url = find_first_image_in_content(post)
    if img_url:
        bg = download_image_rgb(img_url)
    else:
        bg = None

    if bg is None:
        # fundo liso se faltar
        top = Image.new("RGB", (W, TOP_IMAGE_H), (20,20,20))
    else:
        top = object_fit_cover(bg, W, TOP_IMAGE_H)
    canvas.paste(top, (0, 0))

    # LOGO (acima da faixa vermelha)
    if LOGO_PATH.exists():
        try:
            logo = Image.open(LOGO_PATH).convert("RGBA")
            # escala
            w_ratio = min(LOGO_MAX_W / logo.width, 1.0)
            new_w = int(logo.width * w_ratio)
            new_h = int(logo.height * w_ratio)
            logo = logo.resize((new_w, new_h), Image.LANCZOS)
            lx = (W - new_w)//2
            ly = max(0, LOGO_Y_FROM_TOPIMG - new_h)  # garante que fica no topo da imagem
            canvas.paste(logo, (lx, ly), logo)
        except Exception as e:
            log(f"⚠️  Erro ao aplicar logo: {e}", "INFO")

    # FAIXA VERMELHA (categoria)
    y_red0 = TOP_IMAGE_H
    y_red1 = TOP_IMAGE_H + RED_BAR_H
    draw.rectangle([0, y_red0, W, y_red1], fill=RED_COLOR)

    categoria = pick_category_name(post)
    cat_font = load_font(FONT_ROBOTO_PATH, CATEGORY_TXT_SIZE)
    cat_w = cat_font.getlength(categoria.upper())
    cat_h = cat_font.getbbox("Ay")[3] - cat_font.getbbox("Ay")[1]
    cat_x = (W - int(cat_w))//2
    cat_y = y_red0 + (RED_BAR_H - cat_h)//2
    draw.text((cat_x, cat_y), categoria.upper(), font=cat_font, fill=WHITE_COLOR)

    # CAIXA BRANCA (título)
    y_white0 = WHITE_BOX_Y
    y_white1 = WHITE_BOX_Y + WHITE_BOX_H
    draw.rectangle([0, y_white0, W, y_white1], fill=WHITE_COLOR)

    title = extract_title_text(post)
    title_box = (WHITE_BOX_MARGIN,
                 y_white0 + WHITE_BOX_MARGIN,
                 W - WHITE_BOX_MARGIN,
                 y_white1 - WHITE_BOX_MARGIN)

    title_font, wrapped = fit_title_in_box(
        draw, title, FONT_ANTON_PATH, title_box,
        max_size=TITLE_MAX_FONTSIZE,
        min_size=TITLE_MIN_FONTSIZE,
        max_lines=TITLE_MAX_LINES,
        line_spacing=TITLE_LINE_SPACING
    )
    draw_centered_text(draw, wrapped, title_font, title_box, fill=TITLE_COLOR, line_spacing=TITLE_LINE_SPACING)

    # RODAPÉ
    rod_font = load_font(FONT_ROBOTO_PATH, RODAPE_SIZE)
    rod_w = rod_font.getlength(RODAPE_TXT)
    rod_h = rod_font.getbbox("Ay")[3] - rod_font.getbbox("Ay")[1]
    rx = (W - int(rod_w))//2
    ry = RODAPE_Y
    draw.text((rx, ry), RODAPE_TXT, font=rod_font, fill=WHITE_COLOR)

    canvas.save(save_path, "JPEG", quality=92, optimize=True, progressive=True)
    return save_path

# ====== VÍDEO (FFMPEG) ======
def make_video(jpg: Path, mp4_out: Path, seconds=10):
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(jpg)
    ]
    audio = BASE / "audio_fundo.mp3"
    if audio.exists():
        cmd += ["-i", str(audio), "-shortest"]
    cmd += [
        "-t", str(seconds),
        "-r", "25",
        "-c:v", "libx264", "-pix_fmt", "yuv420p"
    ]
    if audio.exists():
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    cmd += [str(mp4_out)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return mp4_out

# ====== FACEBOOK PAGE (VÍDEO) ======
def fb_publish_video(page_id: str, token: str, file_url: str, description: str) -> str | None:
    url = f"https://graph.facebook.com/{API_V}/{page_id}/videos"
    data = {
        "access_token": token,
        "file_url": file_url,
        "description": description[:2200]
    }
    r = SESSION.post(url, data=data, timeout=120)
    if r.status_code == 200:
        return r.json().get("id")
    log(f"❌ FB /videos falhou: {r.status_code} | {r.text}", "ERROR")
    return None

# ====== INSTAGRAM (REELS) ======
def ig_create_container(ig_id: str, token: str, video_url: str, caption: str) -> str | None:
    url = f"https://graph.facebook.com/{API_V}/{ig_id}/media"
    data = {
        "access_token": token,
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption[:2200]
    }
    r = SESSION.post(url, data=data, timeout=120)
    if r.status_code == 200:
        return r.json().get("id")
    log(f"❌ IG /media falhou: {r.status_code} | {r.text}", "ERROR")
    return None

def ig_wait_finished(container_id: str, token: str, max_wait=300) -> bool:
    url = f"https://graph.facebook.com/{API_V}/{container_id}"
    waited = 0
    while waited <= max_wait:
        r = SESSION.get(url, params={"access_token": token, "fields": "status_code,status"}, timeout=60)
        if r.status_code == 200:
            st = r.json().get("status_code")
            log(f"⏳ IG status: {st}", "INFO")
            if st == "FINISHED":
                return True
            if st == "ERROR":
                return False
        time.sleep(10)
        waited += 10
    return False

def ig_publish(ig_id: str, token: str, creation_id: str) -> bool:
    url = f"https://graph.facebook.com/{API_V}/{ig_id}/media_publish"
    data = {"access_token": token, "creation_id": creation_id}
    r = SESSION.post(url, data=data, timeout=120)
    if r.status_code == 200:
        return True
    log(f"❌ IG /media_publish falhou: {r.status_code} | {r.text}", "ERROR")
    return False

# ====== PROCESSADOS ======
PROC_FILE = OUT / "processed.json"
def load_processed():
    if PROC_FILE.exists():
        try:
            return set(json.loads(PROC_FILE.read_text(encoding="utf-8")))
        except:
            return set()
    return set()

def save_processed(s: set):
    PROC_FILE.write_text(json.dumps(sorted(list(s))), encoding="utf-8")

# ====== LOOP PRINCIPAL ======
def process_once():
    # valida env
    for k, v in [("WP_URL", WP_URL), ("TOKEN", TOKEN), ("PAGE_ID", PAGE_ID), ("IG_ID", IG_ID),
                 ("CLOUDINARY_CLOUD_NAME", CLOUD_NAME), ("CLOUDINARY_API_KEY", CLOUD_KEY), ("CLOUDINARY_API_SECRET", CLOUD_SEC)]:
        if not v:
            log(f"❌ Variável ausente: {k}", "ERROR")
            return

    posts = wp_latest_posts(limit=5)
    log(f"→ Recebidos {len(posts)} posts", "INFO")

    processed = load_processed()
    cloud_ok = cloudinary_init()

    for post in posts:
        pid = post["id"]
        if str(pid) in processed:
            continue

        try:
            log(f"🎨 Arte post {pid}…", "INFO")
            arte_path = OUT / f"arte_{pid}.jpg"
            render_art(post, arte_path)
            log(f"✅ Arte: {arte_path}", "INFO")

            mp4_path = OUT / f"reel_{pid}.mp4"
            log("🎬 Gerando vídeo 10s…", "INFO")
            make_video(arte_path, mp4_path, VIDEO_SECONDS)
            log(f"✅ Vídeo: {mp4_path}", "INFO")

            # sobe pro cloudinary
            if not cloud_ok:
                log("❌ Cloudinary não configurado.", "ERROR")
                return
            url_video = cloudinary_upload_video(mp4_path)

            # capção básica
            title = extract_title_text(post)
            link  = post.get("link", "")
            categoria = pick_category_name(post)
            caption = f"{title}\n\nCategoria: {categoria}\nLeia mais: {link}\n#BocaNoTrombone #Ilhabela"

            # 1) Facebook Page /videos
            vid_id = fb_publish_video(PAGE_ID, TOKEN, url_video, caption)
            if vid_id:
                log(f"📘 Publicado na Página (vídeo): id={vid_id}", "INFO")

            # 2) Instagram Reels com espera
            creation = ig_create_container(IG_ID, TOKEN, url_video, caption)
            if creation:
                if ig_wait_finished(creation, TOKEN, max_wait=480):
                    if ig_publish(IG_ID, TOKEN, creation):
                        log("🎬 IG Reels publicado!", "INFO")
                    else:
                        log("⚠️ IG publish falhou mesmo após FINISHED.", "ERROR")
                else:
                    log("⚠️ IG não ficou FINISHED a tempo.", "ERROR")

            processed.add(str(pid))
            save_processed(processed)
            time.sleep(2)

        except subprocess.CalledProcessError as e:
            log(f"❌ FFmpeg falhou: {e}", "ERROR")
        except requests.RequestException as e:
            log(f"❌ HTTP falhou: {e}", "ERROR")
        except Exception as e:
            log(f"❌ Falha post {pid}: {e}", "ERROR")

def main():
    log("🚀 Auto Reels (WP→FB+IG) iniciado", "INFO")
    while True:
        process_once()
        log("⏳ Fim do ciclo.", "INFO")
        time.sleep(SLEEP_BETWEEN_RUNS)

if __name__ == "__main__":
    main()

