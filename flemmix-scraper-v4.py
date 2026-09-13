cat > /app/flemmix-scraper-v7.py << 'PYTHON_EOF'
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scraper Flemmix --> M3U + STRM  (v7)
─────────────────────────────────────
v7 : genere aussi un .strm par film dans /app/flemmix_movies/
v6 : auto-detection du domaine actif via miroir + fallback DNS
v5 : filtre par ".html" pour exclure les categories
v4 : clean_title() supprime "wiflix", MAX_FILMS=50
"""

import asyncio, re, os, sys, logging, socket, requests
from datetime import datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import httpx
from playwright.async_api import async_playwright

# ═══════════════════════════ CONFIG ═══════════════════════════
BASE_URL          = "https://flemmix.voto"
MAX_FILMS         = 50
OUTPUT_M3U        = "flemmix_v4.m3u"
LOG_FILE          = "flemmix_v4.log"
STRM_OUTPUT_DIR   = "/app/flemmix_movies"
MIRROR_URL        = "https://ww1.wiflix-adresses.fun/"
FALLBACK_DOMAINS  = ["flemmix.men","flemmix.net","flemmix.org","wiflix.men","wiflix.net"]
PREFERENCE_ORDRE  = ["Vidara","Voe","Uqload","Vidmoly"]

PLATEFORMES_VALIDES = {
    "Voe":     {"pattern": r"(voe\.sx|jessicayeahcatch|matthewhotelscience|[\w-]+\.com/e/)", "referer_base": ""},
    "Vidara":  {"pattern": r"vidara\.to",   "referer_base": "https://vidara.to"},
    "Uqload":  {"pattern": r"uqload\.is",   "referer_base": "https://uqload.is"},
    "Vidmoly": {"pattern": r"vidmoly\.biz", "referer_base": "https://vidmoly.biz"},
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}
SKIP_URLS = ["ping.gif","ad.","bigbuckbunny","test","analytics","tracking","doubleclick"]

# ═══════════════════════════ UTILS ════════════════════════════
def clean_title(titre):
    if not titre: return ""
    mi = len(titre)//2
    if mi>10 and titre[:mi].strip().lower()==titre[mi:].strip().lower():
        titre = titre[:mi]
    titre = re.sub(r"\s*\[.*?\]\s*"," ",titre)
    for mot in ["wiflix","flemmix","flustream","en streaming","en streaming vf",
                "en vf","vf","vostfr","streaming","hdlight","bluray","hdtv",
                "web-dl","webrip","1080p","720p","2160p","4k","hdrip",
                "french","multi","truefrench","ts-screener","cam","dvdrip","full"]:
        titre = re.sub(rf"[\s\-]*\b{re.escape(mot)}\b[\s]*"," ",titre,flags=re.I)
    titre = titre.replace("  "," ").strip().strip(" -–—_:|")
    while " -  -" in titre or " - -" in titre:
        titre = titre.replace(" -  -"," - ").replace(" - -"," - ")
    return titre.strip(" -")

def sanitize_filename(titre):
    t = clean_title(titre)
    t = re.sub(r'[<>:"/\\|?*\x00-\x1f]','',t)
    t = re.sub(r'[\s\-]+','-',t).strip('-')
    return (t[:150] if len(t)>150 else t) or "film_sans_titre"

def setup_logging():
    logging.basicConfig(level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(LOG_FILE,encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)])
    return logging.getLogger(__name__)

# ═══════════════════════ RESOLUTION DOMAINE ════════════════
def test_dns(d):
    try: socket.getaddrinfo(d,443,socket.AF_INET); return True
    except: return False

def test_http(d,timeout=5):
    for p in ("https://","http://"):
        try:
            r = requests.get(p+d,headers=HEADERS,timeout=timeout,allow_redirects=True,verify=False)
            if r.status_code==200: return p
        except requests.exceptions.SSLError: return p
        except: continue
    return None

def resolve_base_url(logger):
    global BASE_URL
    logger.info("Recherche du domaine actif...")
    candidats=[]
    try:
        logger.info(f"   Scraping miroir : {MIRROR_URL}")
        r = requests.get(MIRROR_URL,headers=HEADERS,timeout=8,verify=False)
        if r.status_code==200:
            for m in re.findall(r"https?://([\w.-]+\.(?:voto|men|net|org|com|cc|stream|me|sx))",r.text):
                if ("flemmix" in m or "wiflix" in m) and m not in candidats:
                    candidats.append(m)
            logger.info(f"   Miroir OK - {len(candidats)} candidat(s)")
    except Exception as e:
        logger.warning(f"   Miroir inaccessible : {e}")
    for d in candidats:
        if test_dns(d):
            pr = test_http(d)
            if pr:
                BASE_URL = f"{pr}{d}"
                logger.info(f"   Domaine retenu : {BASE_URL} [MIROIR]"); return BASE_URL
    logger.info("   Miroir non concluant, fallback DNS...")
    for d in FALLBACK_DOMAINS:
        if test_dns(d):
            pr = test_http(d)
            if pr:
                BASE_URL = f"{pr}{d}"
                logger.info(f"   Domaine retenu : {BASE_URL} [FALLBACK]"); return BASE_URL
    logger.warning(f"   Aucun domaine valide, conservation : {BASE_URL}")
    return BASE_URL

# ═══════════════════════ HOMEPAGE ═══════════════════════════
async def fetch_homepage_films(client, logger):
    logger.info(f"🌐 Recuperation de la homepage : {BASE_URL}")
    try:
        response = await client.get(BASE_URL,headers=HEADERS,timeout=15.0)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"   Impossible de charger la homepage : {e}"); return []
    html = response.text; logger.info(f"   Homepage chargee ({len(html)} octets)")
    soup = BeautifulSoup(html,"html.parser"); films=[]
    links = soup.find_all("a",href=re.compile(r"/film-en-streaming/"))
    logger.info(f"   Methode 1 - Liens /film-en-streaming/ : {len(links)}")
    ignored=0
    for link in links:
        href = link.get("href","")
        if not href.endswith(".html"): ignored+=1; continue
        titre = (link.get("title") or link.get("data-title")
                 or (link.find("img") and (link.find("img").get("alt") or link.find("img").get("title")))
                 or link.get_text(strip=True))
        titre = titre.strip() if titre else "Sans titre"
        full_url = urljoin(BASE_URL,href)
        if href and titre and titre!="Sans titre" and not any(f["url"]==full_url for f in films):
            films.append({"titre":clean_title(titre),"url":full_url})
    if ignored: logger.info(f"   Categories ignorees (non .html) : {ignored}")
    if len(films)<3:
        logger.warning(f"   Methode 1 insuffisante ({len(films)}), methode 2...")
        films=[]
        for sel in ["article a",".movie-item a",".film-item a",".swiper-slide a",".post-item a",".item a"]:
            for el in soup.select(sel):
                href = el.get("href","") or (el.find("a",href=True).get("href","") if el.find("a",href=True) else "")
                if not href.endswith(".html"): continue
                titre = (el.get("title") or el.get("data-title")
                         or (el.find("img") and el.find("img").get("alt"))
                         or el.get_text(strip=True)).strip()
                if href and titre:
                    full_url = urljoin(BASE_URL,href)
                    if not any(f["url"]==full_url for f in films):
                        films.append({"titre":clean_title(titre),"url":full_url})
            if len(films)>=3: break
    films = films[:MAX_FILMS]
    logger.info(f"   {len(films)} film(s) extrait(s)")
    for i,f in enumerate(films,1): logger.info(f"      [{i:02d}] {f['titre']} -> {f['url']}")
    return films

# ═══════════════════════ PLATEFORMES ════════════════════════
async def fetch_plateformes(client, film_url, logger):
    logger.info(f"   Scraping plateformes : {film_url}")
    try:
        response = await client.get(film_url,headers=HEADERS,timeout=15.0)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"   Erreur chargement page film : {e}"); return {}
    html = response.text; soup = BeautifulSoup(html,"html.parser"); plateformes={}
    for link in soup.find_all("a",href=True):
        href=link.get("href",""); texte=link.get_text(strip=True).lower()
        for nom,cfg in PLATEFORMES_VALIDES.items():
            if (re.search(cfg["pattern"],href,re.I) or re.search(cfg["pattern"],texte,re.I)) and nom not in plateformes:
                plateformes[nom]=href
    if len(plateformes)<2:
        for el in soup.find_all(attrs={"data-href":True}):
            href=el["data-href"]
            for nom,cfg in PLATEFORMES_VALIDES.items():
                if re.search(cfg["pattern"],href,re.I) and nom not in plateformes:
                    plateformes[nom]=href; break
    if len(plateformes)<2:
        for nom,rgx in {"Voe":r"jessicayeahcatch\.com/e/[\w-]+|matthewhotelscience\.com/e/[\w-]+",
                        "Vidara":r"vidara\.to/e/[\w-]+",
                        "Uqload":r"uqload\.is/embed-[\w-]+\.html",
                        "Vidmoly":r"vidmoly\.biz/embed-[\w-]+\.html"}.items():
            for m in re.findall(rgx,html):
                if nom not in plateformes:
                    plateformes[nom] = m if m.startswith("http") else f"https://{m}"; break
    logger.info(f"      Plateformes valides : {len(plateformes)}")
    return plateformes

# ═══════════════════════ PLAYWRIGHT ═════════════════════════
async def extract_video_url(embed_url, plateforme_nom, logger):
    logger.info(f"       Extraction {plateforme_nom} : {embed_url[:80]}...")
    video_urls=[]
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            def handle(resp):
                u=resp.url
                if (".m3u8" in u or ".mp4" in u) and not any(s in u.lower() for s in SKIP_URLS):
                    video_urls.append(u)
            page.on("response",handle)
            await page.goto(embed_url,wait_until="networkidle",timeout=30000)
            await asyncio.sleep(2)
            try:
                jw = await page.evaluate("""() => {
                    try { if (typeof jwplayer!=='undefined') {
                        var p=jwplayer(); if (p&&p.getPlaylistItem) {
                            var i=p.getPlaylistItem();
                            if (i&&i.sources&&i.sources[0]) return i.sources[0].file; } } }
                    catch(e){} return null; }""")
                if jw and ".m3u8" in jw: video_urls.insert(0,jw)
            except: pass
            await browser.close()
    except Exception as e:
        logger.error(f"      Playwright : {e}"); return None,None
    if not video_urls:
        logger.warning(f"        Aucun flux {plateforme_nom}"); return None,None
    master=[u for u in video_urls if "master.m3u8" in u]
    m3u8s =[u for u in video_urls if ".m3u8"    in u]
    video = master[0] if master else (m3u8s[0] if m3u8s else video_urls[0])
    logger.info(f"      Flux capture : {video[:100]}...")
    return video, embed_url

# ═══════════════════════ UN FILM ═══════════════════════════
async def traiter_film(client, sem, film, logger):
    async with sem:
        logger.info(f"🎬 Film : {film['titre']}")
        logger.info(f"   URL : {film['url']}")
        result = {"titre":film["titre"],"url_film":film["url"],"streams":{}}
        plateformes = await fetch_plateformes(client, film["url"], logger)
        for nom in PREFERENCE_ORDRE:
            if nom not in plateformes: continue
            video,r = await extract_video_url(plateformes[nom], nom, logger)
            if video:
                result["streams"][nom] = {"url":video,"referer":r}
                break
        return result

# ═══════════════════════ M3U ═══════════════════════════════
def generate_m3u(films, m3u_file, logger):
    logger.info(f"📝 Generation M3U : {m3u_file}")
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(m3u_file,"w",encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        f.write(f"# Generé par Flemmix Scraper — {today}\n")
        for film in films:
            if not film["streams"]: continue
            for plat, info in film["streams"].items():
                url, ref = info["url"], info["referer"]
                grp = f"Flemmix/{plat}"
                f.write(f'#EXTINF:-1 group-title="{grp}" tvg-name="{film["titre"]}",{film["titre"]} [{plat}]\n')
                if ref: f.write(f'#EXTVLCOPT:http-referrer={ref}\n{url}\n')
                else:   f.write(f'{url}\n')
    logger.info(f"✅ M3U genere : {m3u_file}")

# ═══════════════════════ STRM ══════════════════════════════
def generate_strm(films, out_dir, logger):
    """Genere un .strm par film dans out_dir."""
    logger.info(f"📁 Generation fichiers .strm : {out_dir}")
    os.makedirs(out_dir, exist_ok=True)
    nb_crees = 0
    for film in films:
        if not film["streams"]: continue
        # Choisir la meilleure plateforme presente (ordre de preference)
        plateau_choisi = None
        for nom in PREFERENCE_ORDRE:
            if nom in film["streams"]:
                plateau_choisi = nom; break
        if not plateau_choisi: continue
        info = film["streams"][plateau_choisi]
        url  = info["url"]
        fname = f"{sanitize_filename(film['titre'])}.strm"
        path  = os.path.join(out_dir, fname)
        try:
            with open(path,"w",encoding="utf-8") as f:
                f.write(url)
            nb_crees += 1
            logger.info(f"   ✅ {fname}  ({plateau_choisi})")
        except Exception as e:
            logger.error(f"   ❌ Erreur ecriture {fname} : {e}")
    logger.info(f"✅ {nb_crees} fichier(s) .strm genere(s) dans {out_dir}")

# ═══════════════════════ MAIN ══════════════════════════════
async def main():
    logger = setup_logging()
    logger.info("="*60)
    logger.info("  FLEMMIX SCRAPER v7 -> M3U + STRM")
    logger.info("="*60)
    resolve_base_url(logger)
    logger.info(f"   Site      : {BASE_URL}")
    logger.info(f"   Films max : {MAX_FILMS}")
    logger.info(f"   M3U out   : {OUTPUT_M3U}")
    logger.info(f"   STRM out  : {STRM_OUTPUT_DIR}")
    logger.info("="*60)

    async with httpx.AsyncClient(verify=False,headers=HEADERS,follow_redirects=True,timeout=30) as client:
        films       = await fetch_homepage_films(client, logger)
        if not films:
            logger.error("Aucun film trouve. Abandon."); return 1
        sem         = asyncio.Semaphore(3)
        tasks       = [traiter_film(client,sem,f,logger) for f in films]
        resultats   = await asyncio.gather(*tasks, return_exceptions=True)
        res_ok      = [r for r in resultats if isinstance(r,dict)]

    generate_m3u(res_ok, OUTPUT_M3U, logger)
    generate_strm(res_ok, STRM_OUTPUT_DIR, logger)

    nb_ok = sum(1 for f in res_ok if f["streams"])
    total_streams = sum(len(f["streams"]) for f in res_ok)
    logger.info(""); logger.info("="*60); logger.info("  RESUME"); logger.info("="*60)
    logger.info(f"  Films traites : {len(res_ok)}/{len(films)}")
    logger.info(f"  Films OK      : {nb_ok}")
    logger.info(f"  Streams total : {total_streams}")
    logger.info(f"  Fichier M3U   : {os.path.abspath(OUTPUT_M3U)}")
    logger.info(f"  Repertoire STRM: {os.path.abspath(STRM_OUTPUT_DIR)}")
    logger.info("="*60)
    logger.info("\nTermine !"); return 0

if __name__ == "__main__":
    try:    sys.exit(asyncio.run(main()))
    except KeyboardInterrupt: print("\nInterruption."); sys.exit(1)
PYTHON_EOF
