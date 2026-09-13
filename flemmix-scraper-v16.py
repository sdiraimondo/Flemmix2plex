#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Flemmix -> fichiers .strm (v16).

Le scraper ne contourne pas les challenges anti-bot. Il utilise uniquement les
pages/URLs accessibles par HTTP et abandonne proprement un hébergeur bloqué.
Dépendances: requests, beautifulsoup4.
"""
import argparse, json, logging, os, re, sys, time
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8", "Accept-Language":"fr-FR,fr;q=0.9,en;q=0.7"}
HOST_ORDER = ["Vidara", "Voe", "LuLuTV", "Uqload", "Vidmoly", "Filelions"]
HOST_PATTERNS = {
 "Vidara": r"vidara\.to(?:/|$)", "Voe": r"(?:voe\.|rebeccapracticeloss\.com|eugenemakedraw\.com|johnfullwonder\.com|chaliceguzzlerlandlord\.com)/",
 "LuLuTV": r"luluvdo\.com/", "Uqload": r"uqload\.(?:vc|is)/", "Vidmoly": r"vidmoly\.(?:org|biz)/", "Filelions": r"(?:morencius\.com|filelions\.to)/"
}
PARKING = ("neufneuf.space", "parking", "cloudflare", "challenge")

class Scraper:
 def __init__(self, args):
  self.a=args; self.s=requests.Session(); self.s.headers.update(HEADERS); self.unresolved=[]; self.results=[]
  self.log=logging.getLogger("flemmix")
 def get(self,url,**kw):
  return self.s.get(url, timeout=self.a.timeout, allow_redirects=True, **kw)
 def title(self,soup):
  seo=(soup.title.get_text(" ",strip=True) if soup.title else "")
  # Correction: SEO sans séparateur » n'est pas considéré comme titre film.
  if "»" in seo: raw=seo.split("»",1)[0]
  else:
   h=soup.find(["h1","h2"], class_=re.compile("title|film|movie",re.I)) or soup.find("h1")
   raw=h.get_text(" ",strip=True) if h else seo
  return clean_title(raw)
 def base(self):
  if self.a.base_url: return self.a.base_url.rstrip("/")
  try:
   r=self.get(self.a.mirror); html=r.text
   candidates=re.findall(r"https?://(?:www\.)?([\w.-]+)",html,re.I)
   candidates += ["flemmix.cloud"]
   for d in dict.fromkeys(candidates):
    u="https://"+d
    try:
     x=self.get(u)
     if x.status_code==200 and not any(p in x.url.lower() or p in x.text[:10000].lower() for p in PARKING): return u
    except requests.RequestException: pass
  except requests.RequestException as e: self.log.warning("Miroir inaccessible: %s",e)
  return "https://flemmix.cloud"
 def catalogue(self,base):
  try: r=self.get(base+"/"); r.raise_for_status()
  except requests.RequestException as e: self.log.error("Catalogue inaccessible: %s",e); return []
  if is_challenge(r.text): self.log.error("Catalogue bloqué par Cloudflare/challenge (%s)",r.status_code); return []
  soup=BeautifulSoup(r.text,"html.parser"); out=[]; seen=set()
  for a in soup.find_all("a",href=True):
   href=a["href"]; full=urljoin(base,href)
   if not re.search(r"/(?:film-en-streaming|film-ancien)/[^/?]+\.html(?:$|\?)",href,re.I): continue
   text=(a.get("title") or a.get("data-title") or (a.find("img").get("alt") if a.find("img") else "") or a.get_text(" ",strip=True))
   if full not in seen and text.strip(): seen.add(full); out.append({"titre":clean_title(text),"url":full})
  return out[:self.a.limit]
 def hosts(self,film):
  r=self.get(film["url"]); r.raise_for_status(); soup=BeautifulSoup(r.text,"html.parser"); out=[]
  for a in soup.find_all("a"):
   raw=a.get("onclick","") or a.get("href",""); m=re.search(r"(?:loadVideo\s*\(\s*['\"]|href=['\"])(https?://[^'\" )]+)",raw,re.I)
   u=m.group(1) if m else (a.get("href","") if a.get("href","").startswith("http") else "")
   label=a.get_text(" ",strip=True)
   if not u: continue
   host=next((n for n,p in HOST_PATTERNS.items() if re.search(p,u,re.I)),None)
   if host and (host,label,u) not in out: out.append((host,label,u))
  return out
 def resolve(self,host,url):
  self.log.info("    essai %s: %s",host,url)
  try:
   if host=="Vidara":
    code=urlparse(url).path.rstrip("/").split("/")[-1]
    j=self.s.post("https://vidara.to/api/stream",json={"filecode":code},headers={"Referer":url},timeout=self.a.timeout).json()
    return j.get("streaming_url") if usable(j.get("streaming_url")) else None
   if host=="Uqload":
    # TODO: Cloudflare bloque l'embed réel; ne pas supposer d'API.
    return None
   r=self.get(url); html=r.text
   # Voe expose actuellement une variable source après les redirections; les
   # URLs de test/placeholder sont rejetées.
   found=extract_media(html)
   if found: return found[0]
   # Les lecteurs Lulu/Filelions/Vidmoly utilisent un packer JS; décodage
   # générique des eval(function(p,a,c,k,e,d)...), sans format hard-codé.
   for decoded in unpack_packed(html):
    found=extract_media(decoded)
    if found: return found[0]
   return None
  except (requests.RequestException, ValueError, KeyError, json.JSONDecodeError) as e:
   self.log.debug("    %s erreur: %s",host,e); return None
 def run(self):
  base=self.base(); self.log.info("Site cible: %s",base)
  films=self.catalogue(base); self.log.info("Films trouvés: %d",len(films))
  for f in films:
   try: hs=self.hosts(f)
   except requests.RequestException as e: self.log.warning("%s: fiche inaccessible: %s",f["titre"],e); self.unresolved.append(f); continue
   ordered=[]
   for h,l,u in hs:
    if re.search(r"vostfr|vo\b",l,re.I): continue
    if h not in [x[0] for x in ordered]: ordered.append((h,l,u))
   # Vidara is always attempted first; the remaining hosts retain page order.
   ordered = ([x for x in ordered if x[0] == "Vidara"] +
              [x for x in ordered if x[0] != "Vidara"])
   resolved=None
   for h,l,u in ordered:
    stream=self.resolve(h,u)
    if stream: resolved=(h,stream); break
   if resolved:
    f.update(host=resolved[0],stream=resolved[1]); self.results.append(f); self.log.info("  OK %s [%s]",f["titre"],resolved[0])
   else: self.unresolved.append(f); self.log.warning("  NON RÉSOLU: %s — %s",f["titre"],f["url"])

  # --- Écriture des .strm : à plat dans --output-dir (obligatoire) ---
  out=Path(self.a.output_dir)
  out.mkdir(parents=True,exist_ok=True)
  self.log.info("Répertoire de sortie des .strm : %s", out.resolve())


  # --- Nettoyage : supprime uniquement les anciens .strm avant recréation ---
  out=Path(self.a.output_dir)
  out.mkdir(parents=True,exist_ok=True)
  old_strm = list(out.glob("*.strm"))
  for p in old_strm:
    p.unlink()
  self.log.info("Nettoyage : %d ancien(s) .strm supprimé(s) dans %s", len(old_strm), out.resolve())


  for f in self.results:
   p=out/(safe_name(f["titre"])+".strm")
   p.write_text(f["stream"]+"\n",encoding="utf-8")

  logp=Path(self.a.summary_log)
  logp.parent.mkdir(parents=True,exist_ok=True)
  logp.write_text("\n".join(f"NON RÉSOLU\t{f['titre']}\t{f['url']}" for f in self.unresolved)+("\n" if self.unresolved else ""),encoding="utf-8")
  self.log.info("RÉSUMÉ: %d film(s), %d .strm, %d non résolu(s)",len(films),len(self.results),len(self.unresolved))
  return 0

def clean_title(t):
 t=re.sub(r"\s+"," ",t or "").strip(); t=re.sub(r"\s*\[.*?\]","",t)
 t=re.sub(r"\s*[-|:»].*$","",t) if "»" not in t else t
 return re.sub(r"\s+"," ",t).strip(" -_")
def safe_name(t):
 t=re.sub(r'[<>:"/\\|?*\x00-\x1f]','',clean_title(t)); return re.sub(r"[-\s]+","-",t).strip("-")[:150] or "film-sans-titre"
def is_challenge(h): return bool(re.search(r"Just a moment|cf-mitigated|cf-hcaptcha|Enable JavaScript and cookies",h,re.I))
def usable(u): return isinstance(u,str) and bool(re.match(r"https?://",u)) and bool(re.search(r"\.(?:m3u8|mp4)(?:[?&#]|$)",u,re.I)) and "test-videos.co.uk" not in u

def extract_media(h):
 # Direct URLs in HTML or JS player config; intentionally no guessed URL.
 return [u.replace("\\/","/") for u in re.findall(r"https?://[^\"'\s<>\\]+",h) if re.search(r"\.(?:m3u8|mp4)(?:[?&#]|$)",u,re.I) and usable(u)]

def unpack_packed(h):
 out=[]
 for m in re.finditer(r"eval\(function\(p,a,c,k,e,d\)\{.*?\}\((['\"])(.*?)\1,(\d+),(\d+),(['\"])(.*?)\4\.split\('\| '\)|\|'.*?\'\)\)\)",h,re.S):
  try:
   p=m.group(2); a=int(m.group(3)); c=int(m.group(4)); k=m.group(5).split('|')
   def base(n):
    chars='0123456789abcdefghijklmnopqrstuvwxyz'; s=''
    while n: s=chars[n%a]+s; n//=a
    return s or '0'
   for i in range(c-1,-1,-1):
    if i<len(k) and k[i]: p=re.sub(r'\b'+re.escape(base(i))+r'\b',re.escape(k[i]),p)
   out.append(p)
  except Exception: pass
 return out

def main():
 ap=argparse.ArgumentParser()
 ap.add_argument('--limit',type=int,default=50)
 ap.add_argument('--verbose',action='store_true')
 ap.add_argument('--base-url')
 ap.add_argument('--mirror',default='https://www.neufneuf.space/')
 ap.add_argument('--output-dir', required=True,
                  help="Répertoire où écrire les fichiers .strm (obligatoire, à plat, sans sous-dossier)")
 ap.add_argument('--summary-log',default='./output/non-resolus.log')
 ap.add_argument('--timeout',type=float,default=12)
 a=ap.parse_args()
 logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,format='%(asctime)s [%(levelname)s] %(message)s')
 return Scraper(a).run()

if __name__=='__main__': sys.exit(main())
