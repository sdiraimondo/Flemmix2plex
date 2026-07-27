#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scraper Flemmix → M3U  (v5)
─────────────────────────────
v5 : filtre par ".html" pour exclure les catégories de navigation
v4 : clean_title() supprime "wiflix", MAX_FILMS=50
v3 : un seul flux/film, ordre préférence Vidara>Voe>Uqload>Vidmoly
v2 : fix URL embed (urljoin cassé)
v1 : initial

INSTALLATION :
    pip install httpx beautifulsoup4 playwright
    playwright install chromium

Usage :
    python flemmix-scraper-v4.py
"""

import asyncio
import re
import os
import sys
import logging
from datetime import datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import httpx
from playwright.async_api import async_playwright

# ═══════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════
BASE_URL = "https://flemmix.voto"
MAX_FILMS = 50
OUTPUT_M3U = "flemmix_v4.m3u"
LOG_FILE = "flemmix_v4.log"

# Ordre de préférence (la 1ère qui marche gagne)
PREFERENCE_ORDRE = ["Vidara", "Voe", "Uqload", "Vidmoly"]

PLATEFORMES_VALIDES = {
    "Voe": {
        "pattern": r"(voe\.sx|jessicayeahcatch|matthewhotelscience|[\w-]+\.com/e/)",
        "referer_base": "",
    },
    "Vidara": {
        "pattern": r"vidara\.to",
        "referer_base": "https://vidara.to",
    },
    "Uqload": {
        "pattern": r"uqload\.is",
        "referer_base": "https://uqload.is",
    },
    "Vidmoly": {
        "pattern": r"vidmoly\.biz",
        "referer_base": "https://vidmoly.biz",
    },
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

VLC_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

SKIP_URLS = ["ping.gif", "ad.", "bigbuckbunny", "test", "analytics", "tracking", "doubleclick"]

# ═══════════════════════════════════════════════════════════
# NETTOYAGE DU TITRE
# ═══════════════════════════════════════════════════════════
def clean_title(titre):
    if not titre:
        return ""

    mi = len(titre) // 2
    if mi > 10 and titre[:mi].strip().lower() == titre[mi:].strip().lower():
        titre = titre[:mi]

    titre = re.sub(r"\s*\[.*?\]\s*", " ", titre)

    termes_a_retirer = [
        "wiflix", "flemmix", "flustream",
        "en streaming", "en streaming vf", "en vf",
        "vf", "vostfr", "streaming",
        "hdlight", "bluray", "hdtv", "web-dl", "webrip",
        "1080p", "720p", "2160p", "4k",
        "hdrip", "french", "multi", "truefrench",
        "ts-screener", "cam", "dvdrip", "full",
    ]
    for mot in termes_a_retirer:
        titre = re.sub(rf"[\s\-]*\b{re.escape(mot)}\b[\s]*", " ", titre, flags=re.I)

    titre = titre.replace("  ", " ").strip()
    titre = titre.strip(" -–—_:|")
    while " -  -" in titre or " - -" in titre:
        titre = titre.replace(" -  -", " - ").replace(" - -", " - ")
    titre = titre.strip(" -")

    return titre

# ═══════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════
def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# SCRAPING HOMEPAGE
# ═══════════════════════════════════════════════════════════
async def fetch_homepage_films(client, logger):
    logger.info("🌐 Récupération de la homepage Flemmix...")
    logger.info(f"   URL : {BASE_URL}")

    try:
        response = await client.get(BASE_URL, headers=HEADERS, timeout=15.0)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"   ❌ Impossible de charger la homepage : {e}")
        return []

    html = response.text
    logger.info(f"   ✅ Homepage chargée ({len(html)} octets)")

    soup = BeautifulSoup(html, "html.parser")
    films = []

    links = soup.find_all("a", href=re.compile(r"/film-en-streaming/"))
    logger.info(f"   📋 Méthode 1 - Liens /film-en-streaming/ : {len(links)} trouvé(s)")

    ignored_cats = 0
    for link in links:
        href = link.get("href", "")

        if not href.endswith(".html"):
            ignored_cats += 1
            continue

        titre = (
            link.get("title")
            or link.get("data-title")
            or (link.find("img") and (link.find("img").get("alt") or link.find("img").get("title")))
            or link.get_text(strip=True)
        )
        titre = titre.strip() if titre else "Sans titre"
        full_url = urljoin(BASE_URL, href)

        if href and titre and titre != "Sans titre":
            if not any(f["url"] == full_url for f in films):
                films.append({"titre": clean_title(titre), "url": full_url})

    if ignored_cats:
        logger.info(f"   🚫 Catégories ignorées (URL non .html) : {ignored_cats}")

    if len(films) < 3:
        logger.warning(f"   ⚠  Méthode 1 insuffisante ({len(films)}), essaie méthode 2...")
        films = []
        for selector in ["article a", ".movie-item a", ".film-item a", ".swiper-slide a",
                         ".post-item a", ".item a", ".post a", ".entry a"]:
            elements = soup.select(selector)
            if elements:
                logger.info(f"    Méthode 2 - Sélecteur '{selector}' : {len(elements)}")
                for el in elements:
                    href = el.get("href", "")
                    if not href:
                        a_tag = el.find("a")
                        href = a_tag.get("href", "") if a_tag else ""

                    if not href.endswith(".html"):
                        continue

                    titre = (
                        el.get("title") or el.get("data-title")
                        or (el.find("img") and el.find("img").get("alt"))
                        or el.get_text(strip=True)
                    )
                    titre = titre.strip() if titre else ""
                    if href and titre:
                        full_url = urljoin(BASE_URL, href)
                        if not any(f["url"] == full_url for f in films):
                            films.append({"titre": clean_title(titre), "url": full_url})
                if len(films) >= 3:
                    break

    if len(films) < 3:
        logger.warning(f"     Méthode 2 insuffisante ({len(films)}), scan HTML...")
        pattern = r'href="(/film-en-streaming/[^"]+\.html)"[^>]*>([^<]+)<'
        matches = re.findall(pattern, html)
        for href, titre in matches:
            full_url = urljoin(BASE_URL, href)
            titre = BeautifulSoup(titre, "html.parser").get_text(strip=True)
            if titre and not any(f["url"] == full_url for f in films):
                films.append({"titre": clean_title(titre), "url": full_url})

    films = films[:MAX_FILMS]
    logger.info(f"   ✅ {len(films)} film(s) extrait(s)")
    for i, f in enumerate(films, 1):
        logger.info(f"      [{i:02d}] {f['titre']} → {f['url']}")
    return films

# ═══════════════════════════════════════════════════════════
# SCRAPING PLATEFORMES D'UN FILM
# ═══════════════════════════════════════════════════════════
async def fetch_plateformes(client, film_url, logger):
    logger.info(f"   🔍 Scraping plateformes : {film_url}")

    try:
        response = await client.get(film_url, headers=HEADERS, timeout=15.0)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"   ❌ Erreur chargement page film : {e}")
        return {}

    html = response.text
    soup = BeautifulSoup(html, "html.parser")
    plateformes = {}

    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        texte = link.get_text(strip=True).lower()
        for nom, cfg in PLATEFORMES_VALIDES.items():
            if re.search(cfg["pattern"], href, re.I) or re.search(cfg["pattern"], texte, re.I):
                if nom not in plateformes:
                    plateformes[nom] = href
                    logger.info(f"      ✅ {nom} : {href}")

    if len(plateformes) < 2:
        for el in soup.find_all(attrs={"data-href": True}):
            href = el["data-href"]
            for nom, cfg in PLATEFORMES_VALIDES.items():
                if re.search(cfg["pattern"], href, re.I) and nom not in plateformes:
                    plateformes[nom] = href
                    logger.info(f"      ✅ {nom} [data-href] : {href}")

    if len(plateformes) < 2:
        embed_domains = {
            "Voe":     r"jessicayeahcatch\.com/e/[\w-]+|matthewhotelscience\.com/e/[\w-]+",
            "Vidara":  r"vidara\.to/e/[\w-]+",
            "Uqload":  r"uqload\.is/embed-[\w-]+\.html",
            "Vidmoly": r"vidmoly\.biz/embed-[\w-]+\.html",
        }
        for nom, regex in embed_domains.items():
            for m in re.findall(regex, html):
                if nom not in plateformes:
                    full_url = m if m.startswith("http") else f"https://{m}"
                    if full_url.startswith("http"):
                        plateformes[nom] = full_url
                        logger.info(f"      ✅ {nom} [regex] : {full_url}")
                        break

    logger.info(f"      📊 Plateformes valides : {len(plateformes)}")
    return plateformes

# ═══════════════════════════════════════════════════════════
# EXTRACTION VIDEO VIA PLAYWRIGHT
# ═══════════════════════════════════════════════════════════
async def extract_video_url(embed_url, plateforme_nom, logger):
    logger.info(f"       Extraction {plateforme_nom} : {embed_url[:80]}...")

    video_urls = []

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            def handle_response(response):
                url = response.url
                if (".m3u8" in url or ".mp4" in url) and not any(
                    skip in url.lower() for skip in SKIP_URLS
                ):
                    video_urls.append(url)

            page.on("response", handle_response)
            await page.goto(embed_url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)

            try:
                jw_url = await page.evaluate("""() => {
                    try {
                        if (typeof jwplayer !== 'undefined') {
                            const p = jwplayer();
                            if (p && p.getPlaylistItem) {
                                const item = p.getPlaylistItem();
                                if (item && item.sources && item.sources[0])
                                    return item.sources[0].file;
                            }
                        }
                    } catch(e) {}
                    return null;
                }""")
                if jw_url and ".m3u8" in jw_url:
                    video_urls.insert(0, jw_url)
                    logger.info(f"      🏆 JWPlayer direct : {jw_url[:80]}...")
            except Exception:
                pass

            await browser.close()

    except Exception as e:
        logger.error(f"      ❌ Playwright : {e}")
        return None, None

    if not video_urls:
        logger.warning(f"        Aucun flux {plateforme_nom}")
        return None, None

    master = [u for u in video_urls if "master.m3u8" in u]
    m3u8s  = [u for u in video_urls if ".m3u8" in u]
    video_url = master[0] if master else (m3u8s[0] if m3u8s else video_urls[0])

    logger.info(f"      ✅ Flux capturé : {video_url[:100]}...")
    return video_url, embed_url

# ═══════════════════════════════════════════════════════════
# TRAITEMENT D'UN FILM (un seul flux, ordre de préférence)
# ═══════════════════════════════════════════════════════════
async def traiter_film(client, sem, film, logger):
    async with sem:
        logger.info(f"🎬 Film : {film['titre']}")
        logger.info(f"   URL : {film['url']}")

        result = {"titre": film["titre"], "url_film": film["url"], "streams": {}}

        plateformes = await fetch_plateformes(client, film["url"], logger)
        if not plateformes:
            logger.warning("   ⚠  Aucune plateforme valide")
            return result

        for nom_plat in PREFERENCE_ORDRE:
            embed_url = plateformes.get(nom_plat)
            if not embed_url:
                continue
            full_embed_url = embed_url if embed_url.startswith("http") else urljoin(BASE_URL, embed_url)
            video_url, referer_url = await extract_video_url(full_embed_url, nom_plat, logger)
            if video_url:
                result["streams"][nom_plat] = {
                    "video_url": video_url,
                    "referer_url": referer_url,
                }
                logger.info(f"      ✅ {nom_plat} → OK, on s'arrête ici")
                break
            else:
                logger.warning(f"        {nom_plat} → KO, on essaye la suivante")

        nb = len(result["streams"])
        logger.info(f"   📊 Streams récupérés : {nb}")
        return result

# ═══════════════════════════════════════════════════════════
# GENERATION M3U
# ═══════════════════════════════════════════════════════════
def generer_m3u(resultats, logger):
    lignes = [
        "#EXTM3U",
        "# Généré par Flemmix Scraper v5 — " + datetime.now().strftime("%Y-%m-%d %H:%M"),
        "",
    ]
    total = 0

    for film in resultats:
        titre = film["titre"]
        for plateforme, data in film["streams"].items():
            video_url = data["video_url"]
            referer_url = data.get("referer_url", "")

            if referer_url:
                referer = referer_url
            elif plateforme == "Voe":
                referer = video_url.split("/engine/")[0] if "/engine/" in video_url else "https://voe.sx"
            else:
                referer = PLATEFORMES_VALIDES.get(plateforme, {}).get("referer_base", BASE_URL)

            lignes.append(f'#EXTINF:-1 group-title="Films",{titre}')
            lignes.append(f"#EXTVLCOPT:http-referrer={referer}")
            lignes.append(f"#EXTVLCOPT:http-user-agent={VLC_USER_AGENT}")
            lignes.append(video_url)
            lignes.append("")
            total += 1

    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("\n".join(lignes))

    logger.info(f"✅ Fichier M3U généré : {OUTPUT_M3U}")
    logger.info(f"    Total : {total} films")
    return OUTPUT_M3U

# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════
async def main():
    logger = setup_logging()

    logger.info("=" * 60)
    logger.info("  FLEMMIX SCRAPER v5 → M3U")
    logger.info("=" * 60)
    logger.info(f"  Site      : {BASE_URL}")
    logger.info(f"  Films max : {MAX_FILMS}")
    logger.info(f"  Output    : {OUTPUT_M3U}")
    logger.info(f"  Log       : {LOG_FILE}")
    logger.info("=" * 60)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        films = await fetch_homepage_films(client, logger)
        if not films:
            logger.error("❌ Aucun film trouvé.")
            return 1

        sem = asyncio.Semaphore(3)
        logger.info(f"\n🚀 Traitement de {len(films)} film(s)...")

        tasks = [traiter_film(client, sem, film, logger) for film in films]
        resultats = await asyncio.gather(*tasks, return_exceptions=True)

        resultats_filtres = []
        for r in resultats:
            if isinstance(r, Exception):
                logger.error(f"   ❌ Exception : {r}")
            else:
                resultats_filtres.append(r)

        logger.info(f"\n Génération M3U...")
        m3u_file = generer_m3u(resultats_filtres, logger)

        nb_ok = sum(1 for f in resultats_filtres if f["streams"])
        total_streams = sum(len(f["streams"]) for f in resultats_filtres)

        logger.info("\n" + "=" * 60)
        logger.info("  RÉSUMÉ")
        logger.info("=" * 60)
        logger.info(f"  Films traités : {len(resultats_filtres)}/{len(films)}")
        logger.info(f"  Films OK      : {nb_ok}")
        logger.info(f"  Streams total : {total_streams}")
        logger.info(f"  Fichier M3U   : {os.path.abspath(m3u_file)}")
        logger.info(f"  Log           : {os.path.abspath(LOG_FILE)}")
        logger.info("=" * 60)

        for film in resultats_filtres:
            plats = ", ".join(film["streams"].keys()) or "aucun"
            logger.info(f"  • {film['titre'][:55]:<55} → {plats}")

    logger.info("\n✅ Terminé !")
    return 0

if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n⚠  Interruption.")
        sys.exit(1)
