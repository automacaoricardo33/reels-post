from PIL import Image, ImageDraw, ImageFont

def desenhar_arte(img_path, titulo, categoria, saida_path):
    img = Image.open(img_path).convert("RGB").resize((1080, 1080))
    draw = ImageDraw.Draw(img)

    # === LOGO ===
    logo = Image.open("logo_boca.png").convert("RGBA")
    logo_w = 280
    ratio = logo_w / logo.width
    logo_h = int(logo.height * ratio)
    logo = logo.resize((logo_w, logo_h))
    img.paste(logo, (400, 880), logo)  # encostado na faixa preta inferior

    # === FAIXA VERMELHA (CATEGORIA) ===
    cat_font = ImageFont.truetype("Roboto-Black.ttf", 42)
    cat_w, cat_h = draw.textsize(categoria.upper(), font=cat_font)
    faixa_h = cat_h + 20
    draw.rectangle([0, 730, 1080, 730 + faixa_h], fill="red")
    draw.text(((1080 - cat_w) // 2, 730 + 10), categoria.upper(), font=cat_font, fill="white")

    # === TITULO (MANCHETE) ===
    title_font = ImageFont.truetype("Anton-Regular.ttf", 60)
    max_w = 1000
    x, y = 40, 800
    for linha in quebrar_texto(titulo.upper(), title_font, max_w, draw):
        draw.text((x, y), linha, font=title_font, fill="black")
        y += 70  # espaçamento entre linhas

    # === ASSINATURA ===
    sig_font = ImageFont.truetype("Roboto-Black.ttf", 36)
    sig_txt = "@BOCANOTROMBONELITORAL"
    sig_w, sig_h = draw.textsize(sig_txt, font=sig_font)
    draw.text(((1080 - sig_w) // 2, 1040 - sig_h), sig_txt, font=sig_font, fill="#FFD700")

    img.save(saida_path, "JPEG", quality=95)

def quebrar_texto(texto, font, largura_max, draw):
    palavras = texto.split()
    linhas, linha = [], ""
    for p in palavras:
        teste = (linha + " " + p).strip()
        w, _ = draw.textsize(teste, font=font)
        if w <= largura_max:
            linha = teste
        else:
            linhas.append(linha)
            linha = p
    if linha:
        linhas.append(linha)
    return linhas
