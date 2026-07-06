#!/usr/bin/env python3
"""
Flight Tracker — Backend Flask
Rastrea vuelos de Iberia en tiempo real via OpenSky Network (gratis, sin API key)
"""
import json, time, logging
from datetime import datetime, timezone
from flask import Flask, jsonify, render_template, request
import requests

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__)

# ── Datos de los vuelos ──────────────────────────────────────────────────
FLIGHTS = {
    "IB418": {
        "flight_iata": "IB418",
        "callsign_variants": ["IBE418", "IBE0418", "IBE418 ", "IBE0418 "],
        "airline": "Iberia",
        "dep": {"code": "BCN", "name": "Barcelona", "lat": 41.2971, "lon": 2.0785},
        "arr": {"code": "MAD", "name": "Madrid", "lat": 40.4722, "lon": -3.5634},
        "dep_time": "18:15",
        "arr_time": "19:40",
        "date": "2026-07-06",
        "status": "scheduled"
    },
    "IB121": {
        "flight_iata": "IB121",
        "callsign_variants": ["IBE121", "IBE0121", "IBE121 ", "IBE0121 "],
        "airline": "Iberia",
        "dep": {"code": "MAD", "name": "Madrid", "lat": 40.4722, "lon": -3.5634},
        "arr": {"code": "LIM", "name": "Lima", "lat": -12.0219, "lon": -77.1143},
        "dep_time": "00:05",
        "arr_time": "05:30",
        "date": "2026-07-07",
        "status": "scheduled"
    }
}

# ── Caché simple ────────────────────────────────────────────────────────
_cache = {"data": None, "ts": 0, "ttl": 120}  # 120s de caché para no quemar rate limit

# ── Cuenta de intentos fallidos ──────────────────────────────────────────
_failures = 0

# ── Autenticación OpenSky (gratis, 60000 req/día en vez de 4000) ────────
OPENSLY_USER = None  # Dejar None para anónimo, o poner tu email
OPENSLY_PASS = None  # Contraseña de cuenta OpenSky

def fetch_opensky():
    """Obtiene estados de aeronaves desde múltiples zonas"""
    # Zonas de búsqueda progresiva:
    # 1. Europa (BCN→MAD)
    # 2. Atlántico Norte (ruta hacia América)
    # 3. Caribe/América Central (aproximación a Sudamérica)
    # 4. Sudamérica (Perú/Brasil)
    ZONES = [
        (35, 46, -10, 5),     # Europa Occidental
        (20, 36, -30, -10),   # Atlántico Norte
        (10, 25, -60, -30),   # Caribe / Atlántico Oeste
        (-15, 10, -85, -50),  # Sudamérica
    ]
    
    all_states = []
    for lamin, lamax, lomin, lomax in ZONES:
        try:
            url = f"https://opensky-network.org/api/states/all?lamin={lamin}&lamax={lamax}&lomin={lomin}&lomax={lomax}"
            auth = (OPENSLY_USER, OPENSLY_PASS) if OPENSLY_USER else None
            r = requests.get(url, timeout=8, auth=auth)
            if r.status_code == 200:
                states = r.json().get("states", [])
                all_states.extend(states)
        except Exception as e:
            log.warning(f"OpenSky zone ({lamin},{lamax},{lomin},{lomax}): {e}")
    
    return all_states if all_states else None

def match_flight(states, callsign_variants):
    """Busca un vuelo por variantes de callsign"""
    if not states:
        return None
    for s in states:
        if not s[1]:  # callsign puede ser None
            continue
        cs = s[1].strip()
        if cs in callsign_variants or cs in [v.strip() for v in callsign_variants]:
            return {
                "icao24": s[0],
                "callsign": s[1].strip(),
                "lat": s[6],
                "lon": s[5],
                "altitude_m": s[7],
                "velocity_kmh": s[9] * 3.6 if s[9] else None,  # m/s → km/h
                "direction": s[10],
                "vertical_rate_ms": s[11],
                "on_ground": s[8],
                "updated": datetime.now(timezone.utc).isoformat()
            }
    return None

@app.route("/")
def index():
    return render_template("index.html", flights=FLIGHTS)

@app.route("/api/track")
def track():
    flight_key = request.args.get("flight", "IB418").upper()
    
    if flight_key not in FLIGHTS:
        return jsonify({"error": "Flight not found"}), 404
    
    flight = FLIGHTS[flight_key]
    
    # Cache check
    now = time.time()
    if _cache["data"] and (now - _cache["ts"]) < _cache["ttl"]:
        cached_pos = _cache["data"].get(flight_key)
        if cached_pos:
            return jsonify(cached_pos)
    
    # Fetch fresh data
    states = fetch_opensky()
    pos = match_flight(states, flight["callsign_variants"])
    
    if not pos:
        # No data from OpenSky for this flight
        msg = "Avión aún no localizado en el radar"
        if _failures > 3:
            msg = "Radar agotado por hoy (límite gratis). Vuelve a intentar mañana o más tarde"
        return jsonify({
            "flight": flight_key,
            "found": False,
            "message": msg,
            "dep": flight["dep"],
            "arr": flight["arr"],
            "status": flight["status"]
        })
    
    pos["flight"] = flight_key
    pos["found"] = True
    pos["status"] = "en-route"
    
    # Update cache
    if not _cache["data"]:
        _cache["data"] = {}
    _cache["data"][flight_key] = pos
    _cache["ts"] = now
    
    return jsonify(pos)

@app.route("/api/all")
def all_flights():
    """Devuelve datos de todos los vuelos configurados"""
    states = fetch_opensky()
    results = {}
    
    for key, flight in FLIGHTS.items():
        pos = match_flight(states, flight["callsign_variants"])
        if pos:
            pos["flight"] = key
            pos["found"] = True
            pos["status"] = "en-route"
        else:
            pos = {
                "flight": key,
                "found": False,
                "message": "Aún no localizado en el radar",
                "dep": flight["dep"],
                "arr": flight["arr"],
                "status": flight["status"]
            }
        results[key] = pos
    
    return jsonify(results)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
