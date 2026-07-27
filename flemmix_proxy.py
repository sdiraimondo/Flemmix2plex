#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Flemmix Proxy — v4 (avec auto-refresh + watcher de catalogue)
--------------------------------------------------------------
- Proxy HTTP pour flux vidéo Flemmix (protection Referer contournée)
- Catalogue JSON chargé depuis /app/flemmix_proxy_catalog.json
- Thread de rafraîchissement autonome (lance refresh_flemmix.py)
- Thread watcher : reload le catalogue à chaque modification du fichier JSON
- Compatible ARMv7 + aarch64 + amd64

Variables d'env :
    REFRESH_ENABLED=1          # 1=on, 0=off
    REFRESH_INTERVAL_HOURS=12  # période entre refresh
    REFRESH_AT_HOUR=6          # premier run à cette heure du matin
    CATALOG_PATH               # chemin du catalogue JSON
    FLASK_PORT                 # port HTTP du proxy (défaut 8181)
    PLEX_TOKEN=xxx             # (optionnel) token Plex
    PLEX_LIB_KEY=2             # (optionnel) clé de la bibliothèque Plex
"""

import os
import sys
import json
import time
import logging
import threading
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from flask import Flask, Response, jsonify, request, abort

# ─────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────
CATALOG_PATH = Path(os.environ.get("CATALOG_PATH", "/app/flemmix_proxy_catalog.json"))
FLASK_PORT   = int(os.environ.get("FLASK_PORT", "8181"))

REFRESH_ENABLED    = os.environ.get("REFRESH_ENABLED", "1") == "1"
REFRESH_INTERVAL_H = int(os.environ.get("REFRESH_INTERVAL_HOURS", "12"))
REFRESH_AT_HOUR    = int(os.environ.get("REFRESH_AT_HOUR", "6"))

PLEX_URL     = os.environ.get("PLEX_URL", "http://localhost:32400")
PLEX_TOKEN   = os.environ.get("PLEX_TOKEN", "")
PLEX_LIB_KEY = os.environ.get("PLEX_LIB_KEY", "")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
)
log = logging.getLogger("flemmix-proxy")

# ─────────────────────────────────────────────────────────
# Catalog
# ─────────────────────────────────────────────────────────
_catalog = {"films": [], "_loaded_at": None, "_mtime": None}
_catalog_lock = threading.Lock()


def load_catalog(force=False):
    """Recharge le catalogue JSON si le fichier a changé (ou si force=True)."""
    global _catalog
    try:
        mtime = CATALOG_PATH.stat().st_mtime
    except FileNotFoundError:
        log.warning(f"Catalogue introuvable : {CATALOG_PATH}")
        return
    if not force and _catalog.get("_mtime") == mtime:
        return
    try:
        with CATALOG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error(f"Erreur lecture catalogue : {e}")
        return

    films = data if isinstance(data, list) else data.get("films", data.get("movies", []))

    with _catalog_lock:
        _catalog = {
            "films": films,
            "_loaded_at": datetime.now().isoformat(),
            "_mtime": mtime,
        }
    log.info(f"Catalogue chargé : {len(films)} film(s)")


def get_index() -> dict:
    """Index {id: film} en O(1)."""
    load_catalog()
    with _catalog_lock:
        return {f.get("id"): f for f in _catalog["films"] if f.get("id")}


# ─────────────────────────────────────────────────────────
# Auto-refresh thread (planifié par heure)
# ─────────────────────────────────────────────────────────
_REFRESH_SCRIPT = Path(__file__).with_name("refresh_flemmix.py")


def _run_refresh():
    """Lance refresh_flemmix.py en subprocess."""
    if not _REFRESH_SCRIPT.exists():
        log.warning(f"[auto-refresh] script introuvable : {_REFRESH_SCRIPT}")
        return
    log.info("[auto-refresh] démarrage")
    try:
        r = subprocess.run(
            [sys.executable, str(_REFRESH_SCRIPT)],
            capture_output=True, text=True, timeout=1800,
            cwd=_REFRESH_SCRIPT.parent,
        )
        if r.returncode == 0:
            log.info("[auto-refresh] terminé avec succès")
        else:
            log.error(f"[auto-refresh] échec (code {r.returncode}): {r.stderr[-500:]}")
        load_catalog(force=True)
    except subprocess.TimeoutExpired:
        log.error("[auto-refresh] timeout (>30 min)")
    except Exception as e:
        log.error(f"[auto-refresh] exception: {e}")


def _refresher_loop():
    """Boucle infinie : aligne sur REFRESH_AT_HOUR puis tourne à intervalle."""
    now = datetime.now()
    next_run = now.replace(hour=REFRESH_AT_HOUR, minute=0, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)
    wait_first = (next_run - now).total_seconds()
    log.info(
        f"[auto-refresh] premier run prévu à {next_run:%Y-%m-%d %H:%M} "
        f"(dans {wait_first / 3600:.1f}h)"
    )
    time.sleep(wait_first)
    _run_refresh()
    interval_s = max(REFRESH_INTERVAL_H, 1) * 3600
    while True:
        time.sleep(interval_s)
        _run_refresh()


# ─────────────────────────────────────────────────────────
# File-watcher thread (reload auto sur modification du JSON)
# ─────────────────────────────────────────────────────────
def _catalog_watcher_loop():
    """
    Surveille le mtime du catalogue JSON.
    Recharge dès qu'une modification est détectée.
    """
    log.info("[watcher] démarré")
    last_mtime = None
    while True:
        try:
            current_mtime = CATALOG_PATH.stat().st_mtime
        except FileNotFoundError:
            time.sleep(5)
            continue

        if last_mtime is not None and current_mtime != last_mtime:
            log.info(f"[watcher] modification détectée — reload du catalogue")
            load_catalog(force=True)
        last_mtime = current_mtime
        time.sleep(5)


# ─────────────────────────────────────────────────────────
# Helpers HTTP / Plex
# ─────────────────────────────────────────────────────────
def _proxy_get(url: str, *, extra_headers: dict | None = None, stream: bool = True):
    """GET avec spoofing du Referer vers l'origine de l'URL."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    headers = {
        "User-Agent": UA,
        "Referer": origin + "/",
        "Origin": origin,
    }
    if extra_headers:
        headers.update(extra_headers)
    client = httpx.Client(follow_redirects=True, timeout=30.0)
    if stream:
        return client.stream("GET", url, headers=headers)
    return client.get(url, headers=headers)


def _notify_plex():
    """Demande à Plex de rescanner la bibliothèque (best effort)."""
    if not PLEX_TOKEN or not PLEX_LIB_KEY:
        return
    try:
        httpx.get(
            f"{PLEX_URL}/library/sections/{PLEX_LIB_KEY}/refresh",
            headers={"X-Plex-Token": PLEX_TOKEN},
            timeout=10.0,
        )
        log.info("[plex] refresh demandé")
    except Exception as e:
        log.warning(f"[plex] refresh échoué : {e}")


# ─────────────────────────────────────────────────────────
# Flask app
# ─────────────────────────────────────────────────────────
app = Flask("flemmix-proxy")


@app.before_request
def _before():
    load_catalog()


@app.get("/")
def index():
    with _catalog_lock:
        n = len(_catalog["films"])
    return jsonify({
        "service": "flemmix-proxy",
        "version": "4",
        "films": n,
        "loaded_at": _catalog.get("_loaded_at"),
        "endpoints": {
            "catalog": "/catalog",
            "stream":  "/stream/<id>",
            "health":  "/health",
            "refresh": "/refresh",
        },
    })


@app.get("/health")
def health():
    with _catalog_lock:
        n = len(_catalog["films"])
    return jsonify({"status": "ok", "films": n})


@app.get("/catalog")
def catalog():
    with _catalog_lock:
        return jsonify({
            "count": len(_catalog["films"]),
            "loaded_at": _catalog.get("_loaded_at"),
            "films": _catalog["films"],
        })


@app.post("/refresh")
def trigger_refresh():
    """Force un refresh immédiat (utile pour cron / docker exec)."""
    if _REFRESH_SCRIPT.exists():
        threading.Thread(target=_run_refresh, daemon=True).start()
        return jsonify({"status": "started", "script": str(_REFRESH_SCRIPT)})
    return jsonify({"status": "missing_script"}), 404


@app.get("/stream/<film_id>")
def stream(film_id):
    """
    Proxy du flux vidéo.
    Le .strm contient une URL du type :  http://host:8181/stream/<id>
    Plex fait un GET dessus → on récupère le flux distant avec Referer spoofé
    → on pipe la réponse vers Plex.
    """
    idx = get_index()
    film = idx.get(film_id)
    if not film:
        abort(404, f"Film inconnu : {film_id}")

    stream_url = film.get("url") or film.get("stream_url")
    if not stream_url:
        abort(404, f"Pas d'URL de flux pour : {film_id}")

    log.info(f"[stream] {film_id} → {stream_url[:80]}...")

    try:
        with _proxy_get(stream_url, stream=True) as resp:
            if resp.status_code >= 400:
                log.error(f"[stream] remote status {resp.status_code}")
                abort(502)

            headers = {}
            for h in ("Content-Type", "Content-Length", "Accept-Ranges"):
                if resp.headers.get(h):
                    headers[h] = resp.headers[h]

            return Response(
                resp.iter_bytes(65536),
                status=resp.status_code,
                headers=headers,
            )
    except httpx.HTTPError as e:
        log.error(f"[stream] erreur HTTP : {e}")
        abort(502)
    except Exception as e:
        log.error(f"[stream] erreur inattendue : {e}")
        abort(502)


@app.get("/stream/<film_id>/hls")
def stream_hls(film_id):
    """
    Proxy HLS : lit le .m3u8 distant, réécrit les URL de segments
    pour qu'elles passent par /proxy.
    """
    idx = get_index()
    film = idx.get(film_id)
    if not film:
        abort(404)
    url = film.get("url") or film.get("stream_url")
    if not url:
        abort(404)

    from urllib.parse import urljoin, quote

    try:
        with _proxy_get(url, stream=False) as resp:
            if resp.status_code >= 400:
                abort(502)
            body = resp.text
    except httpx.HTTPError:
        abort(502)

    lines = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            if stripped.startswith("http"):
                lines.append(f"/proxy?url={quote(stripped, safe='')}")
            else:
                abs_url = urljoin(url, stripped)
                lines.append(f"/proxy?url={quote(abs_url, safe='')}")
        else:
            lines.append(line)

    return Response("\n".join(lines), mimetype="application/vnd.apple.mpegurl")


@app.get("/proxy")
def proxy_passthrough():
    """Proxy générique pour les segments HLS (appelé par /stream/<id>/hls)."""
    url = request.args.get("url")
    if not url:
        abort(400, "missing url")

    try:
        with _proxy_get(url, stream=True) as resp:
            if resp.status_code >= 400:
                abort(502)
            headers = {}
            for h in ("Content-Type", "Content-Length", "Cache-Control"):
                if resp.headers.get(h):
                    headers[h] = resp.headers[h]
            return Response(
                resp.iter_bytes(65536),
                status=resp.status_code,
                headers=headers,
            )
    except httpx.HTTPError:
        abort(502)


# ─────────────────────────────────────────────────────────
# Entrée
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    load_catalog(force=True)

    if REFRESH_ENABLED:
        t = threading.Thread(target=_refresher_loop, daemon=True, name="refresher")
        t.start()
        log.info(
            f"[auto-refresh] activé — toutes les {REFRESH_INTERVAL_H}h, "
            f"heure de base {REFRESH_AT_HOUR:02d}:00"
        )
    else:
        log.info("[auto-refresh] désactivé (REFRESH_ENABLED=0)")

    t_watcher = threading.Thread(
        target=_catalog_watcher_loop, daemon=True, name="catalog-watcher"
    )
    t_watcher.start()
    log.info("[watcher] actif — reload automatique du catalogue sur modification")

    log.info(
        f"Flemmix Proxy v4 démarré — port {FLASK_PORT} — "
        f"{len(_catalog['films'])} film(s) chargé(s)"
    )
    app.run(host="0.0.0.0", port=FLASK_PORT, threaded=True)
