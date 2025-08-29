# auto_reels_wp_publish.py
# ------------------------------------------------------------
# WP  -> arte 1080x1920 -> vídeo 10s -> Cloudinary -> FB + IG
# Layout padrão "BOCA": foto em cima, logo centro, pílula vermelha,
# caixa branca com manchete e @BOCANOTROMBONELITORAL em amarelo.
# ------------------------------------------------------------

import os, io, re, json, time, gc, textwrap, subprocess
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from PIL import Image, ImageDraw, ImageFont, ImageOps

from dotenv import load_dotenv
import cloudinary
import cloudinary.uploader

# ===================== CONFIG .ENV ===========================
load_dotenv()

WP_URL          = os.getenv("WP_URL", "https://jornalvozdolitoral.com").rstrip("/")
POSTS_PER_RUN   = int(os.getenv("POSTS_PER_RUN", "2"))
VIDEO_SECONDS   = int(os.getenv("VIDEO_SECONDS", "10"))

# Facebook & IG
TOKEN           = os.getenv("USER_ACCESS_TOKEN", "")
PAGE_ID         = os.getenv("FACEBOOK_PAGE_ID", "")
IG_ID           = os.getenv("INSTAGRAM_ID", "")
API_V           = os.getenv("API_VERSION", "v23.0")

# Cloudinary
CLOUD_NAME      = os.getenv("CLOUDINARY_CLOUD_NAME", "")
CLOUD_KEY       = os.getenv("CLOUDINARY_API_KEY", "")
CLOUD_SEC       = os.getenv("CLOUDINARY_API_SECRET", "")

# Paths
OUT_DIR         = Path("out"); OUT_DIR.mkdir(exist_ok=True)
LOGO_PATH       = Path("logo_boca.png")
FONT_ANTON      = Path("Anton-Regular.ttf")      # manchete
FONT_ROBOTO_B   = Path("Roboto-Black.ttf")       # categoria e @

PROCESSED_FILE  = Path("processed_post_ids.txt")

# Handle
HANDLE_TEXT     = "@BOCANOTROMBONELITORAL"

# ============================================================
# Sessão HTTP com retry + headers
# ============================================================
def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/126.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/avif,image/webp,image/apng,*/*;q=0.8"
    })
    return s

HTTP = make_session()

# ============================================================
# Util: texto em caps e quebra de linha
# ============================================================
def to_caps(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s.upper()

def wrap_multiline(draw: ImageDraw.ImageDraw, text, font, max_width_px, max_lines=6):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        w_px = draw.textlength(test, font=font)
        if w_px <= max_width_px:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
        if len(lines) >= max_lines:
            break
    if len(lines) < max_lines and cur:
        lines.append(cur)
    # Se ainda sobrou palavras, adiciona "…"
    if len(lines) == max_lines and (len(words) > len(" ".join(lines).split())):
        lines[-1] = lines[-1].rstrip(". ") + "…"
    return lines

# ============================================================
# Busca posts (inclui categories para resolver nome depois)
# ============================================================
def fetch_posts(n=POSTS_PER_RUN):
    url = (f"{WP_URL}/wp-json/wp/v2/posts?per_page={n}"
           f"&orderby=date&_fields=id,title,excerpt,featured_media,content,link,categories")
    r = HTTP.get(url, timeout=30)
    r.raise_for_status()
    return r.json()

def fetch_category_names(cat_ids):
    if not cat_ids:
        return {}
    ids = ",".join(str(x) for x in sorted(set(cat_ids)))
    url = f"{WP_URL}/wp-json/wp/v2/categories?include={ids}&per_page=100"
    r = HTTP.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()
    return {c["id"]: to_caps(c.get("name","")) for c in data}

# ============================================================
# Imagem: preferir <img> do conteúdo; tratar metroimg e headers
# ============================================================
IMG_EXT_OK = (".jpg",".jpeg",".png",".webp",".gif",".bmp",".avif")

def _metroimg_force_jpg(url: str) -> str:
    # troca f:avif por f:jpg (e mantém outros parâmetros)
    if "/f:avif/" in url:
        return url.replace("/f:avif/", "/f:jpg/")
    # às vezes vem .../f:avif,sem barra seguinte
    return re.sub(r"/f:avif([/]|$)", r"/f:jpg\1", url)

def pick_image_url(post):
    # 1) <img> no content
    content = post.get("content",{}).get("rendered") or ""
    if content:
        soup = BeautifulSoup(content, "html.parser")
        for imgtag in soup.find_all("img"):
            src = (imgtag.get("data-src") or imgtag.get("src") or "").strip()
            if not src: 
                continue
            if any(src.lower().endswith(ext) for ext in IMG_EXT_OK):
                # metroimg: força f:jpg
                if "metroimg.com" in src:
                    src = _metroimg_force_jpg(src)
                return src

    # 2) tenta pegar algo do excerpt (às vezes tem img)
    excerpt = post.get("excerpt",{}).get("rendered") or ""
    if excerpt:
        soup = BeautifulSoup(excerpt, "html.parser")
        imgtag = soup.find("img")
        if imgtag:
            src = (imgtag.get("data-src") or imgtag.get("src") or "").strip()
            if any(src.lower().endswith(ext) for ext in IMG_EXT_OK):
                if "metroimg.com" in src:
                    src = _metroimg_force_jpg(src)
                return src

    # 3) nada → None
    return None

def download_image(url: str) -> Image.Image | None:
    try:
        # headers já estão no Session
        r = HTTP.get(url, timeout=30)
        if r.status_code == 403 and "metroimg.com" in url:
            url2 = _metroimg_force_jpg(url)
            if url2 != url:
                r = HTTP.get(url2, timeout=30)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content))
        # Some formatos precisam converter
        if img.mode not in ("RGB","RGBA"):
            img = img.convert("RGBA")
        # Força RGB para composição
        return img.convert("RGB")
    except Exception:
        return None

# ============================================================
# Arte 1080x1920 no padrão solicitado
# ============================================================
W, H = 1080, 1920

# Áreas (ajustadas para ficar igual ao seu mock)
IMG_BOX = (0, 0, W, 820)                # foto no topo (cover)
LOGO_Y  = 830                           # y do centro do logo
PILL_Y  = 960                           # topo da pílula vermelha
PILL_H  = 64
PILL_PADX = 26

# Caixa branca do título
TITLE_BOX = (36, 1045, W-36, 1470)      # (x1,y1,x2,y2)
HANDLE_Y  = 1500                        # y do @
FOOT_STRIP_Y = 1220                     # (apenas referência estética do mock)

def load_font(path: Path, size: int, fallback="arial"):
    try:
        return ImageFont.truetype(str(path), size=size)
    except Exception:
        return ImageFont.truetype(fallback, size=size)

ANTON_55   = load_font(FONT_ANTON,   55)
ROBOTO_32  = load_font(FONT_ROBOTO_B,32)
ROBOTO_40  = load_font(FONT_ROBOTO_B,40)

def draw_centered_logo(card: Image.Image):
    if LOGO_PATH.exists():
        logo = Image.open(LOGO_PATH).convert("RGBA")
        # escala para ~200px de largura
        max_w = 280
        ratio = min(1.0, max_w / logo.width)
        lw, lh = int(logo.width*ratio), int(logo.height*ratio)
        logo = logo.resize((lw, lh), Image.LANCZOS)

        x = (W - lw) // 2
        y = LOGO_Y - lh//2
        card.paste(logo, (x,y), logo)

def draw_category_pill(card: Image.Image, text: str):
    d = ImageDraw.Draw(card)
    txt = to_caps(text or "ILHABELA")
    # calcula largura do texto + padding
    w_txt = d.textlength(txt, font=ROBOTO_32)
    pill_w = int(w_txt + PILL_PADX*2)
    x = (W - pill_w)//2
    y = PILL_Y
    # retângulo vermelho
    d.rounded_rectangle([x,y,x+pill_w,y+PILL_H], radius=10, fill=(228,33,23))
    # texto branco centralizado
    tx = x + (pill_w - w_txt)/2
    ty = y + (PILL_H - ROBOTO_32.size)//2 - 2
    d.text((tx,ty), txt, font=ROBOTO_32, fill="white")

def draw_title_box(card: Image.Image, title: str):
    d = ImageDraw.Draw(card)
    x1,y1,x2,y2 = TITLE_BOX
    # branco com borda preta bem leve
    d.rounded_rectangle([x1,y1,x2,y2], radius=10, fill="white")
    # texto (Anton 55), centralizado, até 6 linhas
    txt = to_caps(title)
    max_w = (x2-x1) - 40
    lines = wrap_multiline(d, txt, ANTON_55, max_w, max_lines=6)
    line_h = ANTON_55.size + 8
    total_h = len(lines)*line_h
    cy = y1 + ((y2 - y1) - total_h)//2
    for i, line in enumerate(lines):
        w = d.textlength(line, font=ANTON_55)
        d.text((x1+(x2-x1-w)/2, cy + i*line_h), line, font=ANTON_55, fill="black")

def draw_handle(card: Image.Image):
    d = ImageDraw.Draw(card)
    txt = HANDLE_TEXT
    w = d.textlength(txt, font=ROBOTO_40)
    d.text(((W - w)/2, HANDLE_Y), txt, font=ROBOTO_40, fill=(252,211,3))

def paste_cover(card: Image.Image, bg: Image.Image):
    # Cobre a região IMG_BOX mantendo "cover"
    x1,y1,x2,y2 = IMG_BOX
    bw,bh = bg.size
    box_w, box_h = (x2-x1), (y2-y1)

    # escala para cobrir
    scale = max(box_w / bw, box_h / bh)
    new_w, new_h = int(bw*scale), int(bh*scale)
    img = bg.resize((new_w,new_h), Image.LANCZOS)

    # recorta centralizado
    left = (new_w - box_w)//2
    top  = (new_h - box_h)//2
    img = img.crop((left, top, left+box_w, top+box_h))

    card.paste(img, (x1,y1))

def make_card(bg: Image.Image, title: str, category: str) -> Image.Image:
    # fundo preto
    card = Image.new("RGB", (W,H), "black")
    paste_cover(card, bg)
    draw_centered_logo(card)
    draw_category_pill(card, category)
    draw_title_box(card, title)
    draw_handle(card)
    return card

# ============================================================
# Vídeo (otimizado para memória no Render 512 MiB)
# ============================================================
def make_video_from_image(jpg_path: str, mp4_path: str, seconds=VIDEO_SECONDS, audio="audio_fundo.mp3"):
    vf = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2"

    if Path(audio).exists():
        cmd = [
            "ffmpeg","-nostdin","-y",
            "-loop","1","-t",str(seconds),"-i",jpg_path,
            "-stream_loop","-1","-i",audio,"-shortest",
            "-vf", vf,
            "-c:v","libx264","-preset","veryfast","-tune","stillimage",
            "-pix_fmt","yuv420p","-r","25","-threads","1",
            "-maxrate","1500k","-bufsize","1000k",
            "-c:a","aac","-b:a","96k",
            "-movflags","+faststart",
            mp4_path
        ]
    else:
        cmd = [
            "ffmpeg","-nostdin","-y",
            "-loop","1","-t",str(seconds),"-i",jpg_path,
            "-f","lavfi","-i","anullsrc=channel_layout=stereo:sample_rate=44100","-shortest",
            "-vf", vf,
            "-c:v","libx264","-preset","veryfast","-tune","stillimage",
            "-pix_fmt","yuv420p","-r","25","-threads","1",
            "-maxrate","1500k","-bufsize","1000k",
            "-c:a","aac","-b:a","96k",
            "-movflags","+faststart",
            mp4_path
        ]
    subprocess.run(cmd, check=True)

# ============================================================
# Cloudinary + FB + IG
# ============================================================
def cloudinary_setup():
    if CLOUD_NAME and CLOUD_KEY and CLOUD_SEC:
        cloudinary.config(
            cloud_name=CLOUD_NAME,
            api_key=CLOUD_KEY,
            api_secret=CLOUD_SEC,
            secure=True
        )

def cloudinary_upload_video(mp4_path: str) -> str:
    cloudinary_setup()
    res = cloudinary.uploader.upload(
        mp4_path,
        resource_type="video",
        folder="reels",
        overwrite=True
    )
    return res["secure_url"]

def fb_publish_video(page_id: str, token: str, video_url: str, message: str) -> str | None:
    url = f"https://graph.facebook.com/{API_V}/{page_id}/videos"
    r = HTTP.post(url, data={"file_url": video_url, "description": message, "access_token": token}, timeout=60)
    if r.status_code//100 == 2:
        data = r.json()
        return data.get("id")
    return None

def ig_create_container(ig_id: str, token: str, video_url: str, caption: str) -> str | None:
    url = f"https://graph.facebook.com/{API_V}/{ig_id}/media"
    payload = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": "true",
        "access_token": token
    }
    r = HTTP.post(url, data=payload, timeout=60)
    if r.status_code//100 == 2:
        return r.json().get("id")
    return None

def ig_get_status(container_id: str, token: str) -> str:
    url = f"https://graph.facebook.com/{API_V}/{container_id}"
    r = HTTP.get(url, params={"fields":"status_code","access_token": token}, timeout=30)
    if r.status_code//100 == 2:
        return r.json().get("status_code","")
    return ""

def ig_publish(ig_id: str, token: str, container_id: str) -> bool:
    url = f"https://graph.facebook.com/{API_V}/{ig_id}/media_publish"
    r = HTTP.post(url, data={"creation_id": container_id, "access_token": token}, timeout=60)
    return r.status_code//100 == 2

# ============================================================
# Processados
# ============================================================
def load_processed():
    if not PROCESSED_FILE.exists(): return set()
    return set(int(x.strip()) for x in PROCESSED_FILE.read_text("utf-8").splitlines() if x.strip().isdigit())

def mark_processed(post_id: int):
    with PROCESSED_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{post_id}\n")

# ============================================================
# Categoria fallback simples
# ============================================================
CITIES = ["ILHABELA","CARAGUATATUBA","SÃO SEBASTIÃO","UBATUBA","PARATY"]
def guess_category(title: str) -> str:
    T = to_caps(title)
    for c in CITIES:
        if c in T: return c
    return "ILHABELA"

# ============================================================
# Pipeline por post
# ============================================================
def process_post(post, cats_map):
    pid   = post["id"]
    title = post.get("title",{}).get("rendered") or ""
    link  = post.get("link","")
    cat_name = ""
    cat_ids = post.get("categories") or []
    for cid in cat_ids:
        if cid in cats_map:
            cat_name = cats_map[cid]; break
    if not cat_name:
        cat_name = guess_category(title)

    print(f"💡 Post {pid} | {to_caps(title)[:70]}...")
    # imagem
    img_url = pick_image_url(post)
    bg = None
    if img_url:
        bg = download_image(img_url)
    if not bg:
        print(f"   ⚠️  Sem imagem válida, pulando.")
        return False

    # arte
    card = make_card(bg, title, cat_name)
    jpg_path = OUT_DIR / f"arte_{pid}.jpg"
    card.save(jpg_path, "JPEG", quality=85, optimize=True, progressive=True)
    print(f"   ✅ Arte: {jpg_path}")

    # vídeo
    mp4_path = OUT_DIR / f"reel_{pid}.mp4"
    make_video_from_image(str(jpg_path), str(mp4_path), VIDEO_SECONDS)
    print(f"   ✅ Vídeo: {mp4_path}")

    # upload cloudinary
    vurl = cloudinary_upload_video(str(mp4_path))
    print(f"   ☁️  Cloudinary: {vurl}")

    caption = f"{to_caps(title)}\n\n{link}\n{HANDLE_TEXT}"

    # FB
    if PAGE_ID and TOKEN:
        vid = fb_publish_video(PAGE_ID, TOKEN, vurl, caption)
        if vid:
            print(f"   📘 Página FB OK: video_id={vid}")
        else:
            print(f"   ❌ Falhou FB")

    # IG: container -> aguardar -> publish
    if IG_ID and TOKEN:
        cid = ig_create_container(IG_ID, TOKEN, vurl, caption)
        if not cid:
            print("   ❌ IG: falha ao criar container")
        else:
            # polling leve
            for _ in range(16):  # até ~80s
                st = ig_get_status(cid, TOKEN)
                if st in ("FINISHED","ERROR","EXPIRED"):
                    break
                time.sleep(5)
            if st == "FINISHED":
                if ig_publish(IG_ID, TOKEN, cid):
                    print("   📷 IG Reels OK")
                else:
                    print("   ❌ IG /media_publish falhou")
            else:
                print(f"   ❌ IG status={st}")

    # libera memória
    del card, bg
    gc.collect()
    return True

# ============================================================
# MAIN
# ============================================================
def main():
    processed = load_processed()
    posts = fetch_posts(POSTS_PER_RUN)

    # carregar nomes de categorias
    all_cat_ids = []
    for p in posts:
        all_cat_ids.extend(p.get("categories") or [])
    cats_map = fetch_category_names(all_cat_ids)

    for p in posts:
        pid = p["id"]
        if pid in processed:
            print(f"↪️  Já processado {pid}, pulando.")
            continue
        ok = process_post(p, cats_map)
        if ok:
            mark_processed(pid)
        # pequena pausa para baixar uso de memória/CPU
        time.sleep(2)

if __name__ == "__main__":
    print("🚀 Auto Reels (WP→FB+IG) iniciado")
    try:
        main()
    except Exception as e:
        print("❌ Erro fatal:", e)
        raise
