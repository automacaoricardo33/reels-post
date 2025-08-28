# wp_probe.py
import os, sys, json, time
import requests
from urllib.parse import urljoin

WP_URL = os.getenv("WP_URL", "https://jornalvozdolitoral.com").strip().rstrip("/")

S = requests.Session()
S.headers.update({"User-Agent": "auto-reels/diag"})

def try_url(url):
    try:
        r = S.get(url, timeout=20)
        print(f"\nURL: {url}\nHTTP {r.status_code}")
        if r.status_code != 200:
            print(r.text[:400])
            return None
        data = r.json()
        if isinstance(data, dict) and "data" in data and "status" in data["data"]:
            # Erro JSON padrão WP
            print(f"WP error payload: {data}")
            return None
        if not isinstance(data, list):
            print(f"Resposta não é lista, tipo: {type(data)}")
            print(str(data)[:400])
            return None
        print(f"→ Recebidos {len(data)} posts")
        for p in data[:5]:
            pid = p.get("id")
            title = p.get("title", {}).get("rendered", "") if isinstance(p.get("title"), dict) else p.get("title")
            print(f"  - ID {pid} | {title[:100]}")
        return data
    except Exception as e:
        print(f"EXCEÇÃO: {e}")
        return None

def main():
    print(f"🔎 WP_URL = {WP_URL}")

    # Estratégia 1: endpoint normal
    u1 = f"{WP_URL}/wp-json/wp/v2/posts?per_page=5&orderby=date&_fields=id,title,excerpt,featured_media,content,link"
    data = try_url(u1)
    if data:
        print("\n✅ OK com /wp-json/wp/v2/posts")
        return

    # Estratégia 2: rest_route fallback
    u2 = f"{WP_URL}/?rest_route=/wp/v2/posts&per_page=5&orderby=date&_fields=id,title,excerpt,featured_media,content,link"
    data = try_url(u2)
    if data:
        print("\n✅ OK com ?rest_route=/wp/v2/posts")
        return

    # Estratégia 3: sem _fields (alguns bloqueiam), e sem orderby
    u3 = f"{WP_URL}/wp-json/wp/v2/posts?per_page=5"
    data = try_url(u3)
    if data:
        print("\n✅ OK com /wp-json/wp/v2/posts (simples)")
        return

    print("\n❌ Nenhuma estratégia retornou posts. Verifique:")
    print("  - O site expõe a REST API? (/wp-json)")
    print("  - Algum firewall/WAF bloqueando o User-Agent?")
    print("  - DNS/Internet do host (teste no navegador local esse mesmo URL)")

if __name__ == "__main__":
    main()
