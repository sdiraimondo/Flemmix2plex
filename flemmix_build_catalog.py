#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lit flemmix_v4.m3u, génère :
   - flemmix_proxy_catalog.json   (lu par le proxy)
   - dossier "Films - Streaming/" (fichiers .strm → http://<IP>:8181/play/<id>)

Usage :
    python flemmix_build_catalog.py [port] [ip_prox]
       port       : 8181 par défaut
       ip_prox    : IP de la machine qui héberge le proxy
                    (par défaut : IP locale auto-détectée)

Variables d'environnement (optionnel) :
    STREAMING_DIR  : chemin du dossier de sortie pour les .strm
                     défaut : /flemmix-streaming/Films - Streaming
    CATALOG_FILE   : chemin du fichier catalogue JSON
                     défaut : /app/flemmix_proxy_catalog.json
    M3U_FILE       : chemin du fichier M3U source
                     défaut : flemmix_v4.m3u (dans APP_DIR)
"""

import json
import os
import re
import socket
import sys
import unicodedata
from pathlib import Path

# ── Répertoire de l'application (où sont les scripts et le M3U)
APP_DIR = Path(__file__).parent.resolve()

# ── Fichier M3U source
M3U_FILE = os.environ.get("M3U_FILE", str(APP_DIR / "flemmix_v4.m3u"))

# ── Port par défaut du proxy
PROXY_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8181

# ── IP du proxy (auto-détection si non précisée)
PROXY_IP = sys.argv[2] if len(sys.argv) > 2 else None
if PROXY_IP is None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(1)
    try:
        s.connect(("8.8.8.8", 80))
        PROXY_IP = s.getsockname()[0]
    except Exception:
        PROXY_IP = "127.0.0.1"
    finally:
        s.close()

# ── Dossier de sortie pour les .strm (Plex y pointe)
OUTPUT_DIR = os.environ.get("STREAMING_DIR", "/flemmix-streaming/Films - Streaming")

# ── Catalogue JSON lu par le proxy
CATALOG_FILE = os.environ.get("CATALOG_FILE", "/app/flemmix_proxy_catalog.json")

# ═══════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════
def slug(t: str) -> str:
    """Génère un nom de fichier safe depuis le titre."""
    t = t.replace(":", " ").replace("'", " ").replace('"', " ")
    t = t.replace("/", "-").replace("\\", "-")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"\s+", " ", t).strip()
    return t

def parser_m3u(path: Path):
    """
    Parse un fichier M3U.
    Retourne une liste de dicts : {"titre": str, "url": str}
    Les lignes #EXTVLCOPT (referer, user-agent) sont ignorées.
    """
    films = []
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF:"):
            titre = line.split(",", 1)[-1].strip()
            i += 1
            # Saute les lignes #EXTVLCOPT qui suivent
            while i < len(lines) and lines[i].strip().startswith("#EXTVLCOPT"):
                i += 1
            # Ligne suivante = URL du flux
            if i < len(lines):
                url = lines[i].strip()
                if url and not url.startswith("#"):
                    films.append({"titre": titre, "url": url})
        i += 1
    return films

# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════
def main():
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    m3u_path = Path(M3U_FILE)
    if not m3u_path.exists():
        print(f"❌ {m3u_path} introuvable")
        return 1

    films = parser_m3u(m3u_path)
    if not films:
        print(f"❌ Aucun film dans {m3u_path}")
        return 1

    print(f"  📋 {len(films)} film(s) trouvé(s) dans {m3u_path.name}")

    catalog = {}
    for idx, film in enumerate(films):
        fid = f"f{idx:03d}"
        url_l = film["url"].lower()

        # Détection de la plateforme source pour le referer
        if "vidara" in url_l:
            platform = "Vidara"
        elif "vidmoly" in url_l:
            platform = "Vidmoly"
        elif "uqload" in url_l:
            platform = "Uqload"
        elif "voe" in url_l or ".com/e/" in url_l:
            platform = "Voe"
        else:
            platform = "unknown"

        safe = slug(film["titre"])
        filename = f"{safe}.strm"
        filepath = os.path.join(OUTPUT_DIR, filename)

        proxy_url = f"http://{PROXY_IP}:{PROXY_PORT}/stream/{fid}"
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(proxy_url)
            print(f"   ✅ {filename}")
        except OSError as e:
            print(f"   ❌ Erreur écriture {filename} : {e}")
            continue

        catalog[fid] = {
            "id":       fid,
            "titre":    film["titre"],
            "url":      film["url"],
            "platform": platform,
        }

    # Écriture du catalogue JSON
    try:
        Path(CATALOG_FILE).write_text(
            json.dumps(catalog, indent=2, ensure_ascii=False)
        )
        print(f"\n  📄 Catalogue JSON → {CATALOG_FILE}")
    except OSError as e:
        print(f"\n  ❌ Erreur écriture catalogue : {e}")

    print(f"  📁 {len(catalog)} fichier(s) .strm créé(s) dans '{OUTPUT_DIR}/'")
    print(f"\n   ⚠  IMPORTANT :")
    print(f"       Proxy IP  = {PROXY_IP}:{PROXY_PORT}")
    print(f"       IP à déclarer dans Plex = {PROXY_IP}")
    print("\n   LANCE LE PROXY :")
    print(f"       python flemmix_proxy.py")
    print("\n   PLEX :")
    print("       Bibliothèque → Films → 'Films - Streaming'")
    print(f"       Dossier : {Path(OUTPUT_DIR).resolve()}")
    print("       Agent : Plex Movie")
    return 0

if __name__ == "__main__":
    sys.exit(main())
