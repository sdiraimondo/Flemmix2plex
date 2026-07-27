#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh_flemmix.py
─────────────────
Enchaîne : scrape Flemmix → build le catalog → ( Plex rescan si token dispo)

Usage :
    python refresh_flemmix.py

Variables d'environnement :
    PLEX_TOKEN       : token Plex (optionnel — rescan auto si fourni)
    PLEX_LIB_KEY     : clé de la bibliothèque Films (optionnel)
    SCRAPER_SCRIPT   : nom du script scraper (défaut : flemmix-scraper-v4.py)
    M3U_FILE         : chemin du M3U source (défaut : flemmix_v4.m3u)
    CATALOG_FILE     : chemin du catalogue JSON (doit matcher CATALOG_PATH du proxy)
    STREAMING_DIR    : dossier de sortie .strm (doit matcher celui du build_catalog)
"""

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

APP_DIR         = Path(os.environ.get("APP_DIR", "/app"))
PLEX_URL        = os.environ.get("PLEX_URL", "http://localhost:32400")
PLEX_TOKEN      = os.environ.get("PLEX_TOKEN", "")
PLEX_LIB_KEY    = os.environ.get("PLEX_LIB_KEY", "")
SCRAPER_SCRIPT  = os.environ.get("SCRAPER_SCRIPT", "flemmix-scraper-v4.py")
CATALOG_FILE    = os.environ.get("CATALOG_FILE", "/app/flemmix_proxy_catalog.json")
STREAMING_DIR   = os.environ.get("STREAMING_DIR", "/flemmix-streaming/Films - Streaming")


def run(cmd, cwd=None):
    """Exécute une commande shell ; sort en erreur si retour != 0."""
    cwd = str(cwd or APP_DIR)
    print(f"\n▶  {cmd}")
    try:
        res = subprocess.run(
            cmd, shell=True, cwd=cwd,
            capture_output=True, text=True, timeout=3600,
        )
    except subprocess.TimeoutExpired:
        print("❌ Timeout (>1h)")
        sys.exit(1)
    if res.stdout:
        print(res.stdout)
    if res.returncode != 0:
        print(f"❌ Échec (code {res.returncode})")
        if res.stderr:
            print(res.stderr)
        sys.exit(res.returncode)
    return res


def notify_proxy():
    """Demande au proxy de recharger le catalogue (best effort)."""
    print("\n" + "=" * 60)
    print("  3/4  RECHARGEMENT DU PROXY")
    print("=" * 60)
    try:
        with urllib.request.urlopen(
            "http://localhost:8181/refresh", timeout=5
        ) as r:
            data = json.loads(r.read())
            print(f"   ✅ Proxy rechargé : {data.get('status', 'ok')}")
    except urllib.error.URLError as e:
        print(f"   ⚠  Proxy non joignable : {e}")
        print("   (le watcher du proxy rechargera automatiquement à la prochaine modification du JSON)")
    except Exception as e:
        print(f"   ⚠  Erreur inattendue : {e}")


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════
def main():
    os.chdir(APP_DIR)

    # 1) Scraper Flemmix
    print("=" * 60)
    print("  1/4  SCRAPING FLEMMIX")
    print("=" * 60)
    scraper_path = APP_DIR / SCRAPER_SCRIPT
    if not scraper_path.exists():
        print(f"❌ Script introuvable : {scraper_path}")
        sys.exit(1)
    run(f"python {SCRAPER_SCRIPT}")

    # 2) Générer .strm + catalog
    print("\n" + "=" * 60)
    print("  2/4  BUILD DU CATALOG")
    print("=" * 60)
    build_path = APP_DIR / "flemmix_build_catalog.py"
    if not build_path.exists():
        print(f"❌ Script introuvable : {build_path}")
        sys.exit(1)
    run(f"python flemmix_build_catalog.py")

    # 3) Notifier le proxy (reload automatique via watcher + appel HTTP)
    notify_proxy()

    # 4) Plex rescan (si token dispo)
    print("\n" + "=" * 60)
    print("  4/4  RESCAN PLEX")
    print("=" * 60)
    if PLEX_TOKEN and PLEX_LIB_KEY:
        try:
            url = (
                f"{PLEX_URL}/library/sections/{PLEX_LIB_KEY}/refresh"
                f"?X-Plex-Token={PLEX_TOKEN}"
            )
            with urllib.request.urlopen(url, timeout=10) as r:
                print(f"   ✅ Plex résan : HTTP {r.status}")
        except Exception as e:
            print(f"   ⚠  Plex : {e} (non bloquant)")
    else:
        print("   ℹ  PLEX_TOKEN / PLEX_LIB_KEY non définis.")
        print("      Pour activer le rescan auto :")
        print("      docker exec -e PLEX_TOKEN=xxx -e PLEX_LIB_KEY=2 flemmix-proxy \\")
        print("          python refresh_flemmix.py")

    print("\n" + "=" * 60)
    print("  ✅ TERMINÉ")
    print("=" * 60)


if __name__ == "__main__":
    main()
