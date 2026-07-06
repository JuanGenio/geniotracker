#!/usr/bin/env python3
"""
Flight Tracker — Backend Flask v2
Rastrea vuelos vía PocketWorld (sin API key, sin rate limit)
"""
import json, time, logging
from datetime import datetime, timezone
from flask import Flask, jsonify, render_template, request
import requests

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__)

FLIGHTS = {
    "IB418": {
        "flight_iata": "IB418",
        "callsign_variants": ["IBE418", "IBE0418", "IBE418 ", "IBE0418 "],
        "airline": "Iberia",
        "dep": {"code": "BCN", "name": "Barcelona", "lat": 41.2971, "lon": 2.0785},
        "arr": {"code": "MAD", "name": "Madrid", "lat": 40.4722, "lon": -3.5634},
        "dep_time": "18:15", "arr_time": "19:40",
        "date": "2026-07-06", "status": "scheduled"
    },
    "IB121": {
        "flight_iata": "IB121",
        "callsign_variants": ["IBE121", "IBE0121", "IBE121 ", "IBE0121 "],
        "airline": "Iberia",
        "dep": {"code": "MAD", "name": "Madrid", "lat": 40.4722, "lon": -3.5634},
        "arr": {"code": "LIM", "name": "Lima", "lat": -12.0219, "lon": -77.1143},
        "dep_time": "00:05", "arr_time": "05:30",
        "date": "2026-07-07", "status": "scheduled"
    }
}

_cache = {"data": None, "ts": 0, "ttl": 30}

def fetch_pocketworld():
    """Obtiene vuelos desde PocketWorld (endpoint único, más simple)"""
    try:
        r = requests.get("https://pocketworld.org/api/flights", timeout=25, 
                        headers={"User-Agent": "GenioTracker/1.0"})
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                log.info(f"PocketWorld: {len(data)} flights")
                return data
        else:
            log.warning(f"PocketWorld HTTP {r.status_code}")
            return None
    except Exception as e:
        log.warning(f"PocketWorld error: {e}")
        return None

def match_flight(flights, callsign_variants):
    """Busca un vuelo por variantes de callsign en los datos de PocketWorld"""
    if not flights:
        return None
    clean = [cs.strip().upper() for cs in callsign_variants]
    for f in flights:
        cs = (f.get("callsign") or "").strip().upper()
        if cs in clean:
            vel = f.get("velocity")  # m/s
            alt = f.get("baro_alt") or f.get("alt")  # metros
            return {
                "icao24": f.get("icao24", ""),
                "callsign": cs,
                "lat": f.get("lat"),
                "lon": f.get("lng"),
                "altitude_m": alt,
                "velocity_kmh": round(vel * 3.6, 1) if vel else None,
                "direction": f.get("heading"),
                "vertical_rate_ms": f.get("vertical_rate"),
                "on_ground": f.get("on_ground", False),
                "updated": datetime.now(timezone.utc).isoformat()
            }
    return None

@app.route("/")
def index():
    return render_template("index.html", flights=FLIGHTS)

@app.route("/api/ping")
def ping():
    """Endpoint de prueba para verificar conectividad"""
    import socket
    try:
        ip = socket.gethostbyname("pocketworld.org")
        r = requests.get("https://pocketworld.org/api/flights", timeout=10,
                        headers={"User-Agent": "GenioTracker/1.0"})
        body = r.text[:500] if r.status_code == 200 else r.text[:200]
        return jsonify({"status": "ok", "pocketworld_ip": ip, "http": r.status_code, "body_start": body})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)})

@app.route("/api/track")
def track():
    flight_key = request.args.get("flight", "IB418").upper()
    if flight_key not in FLIGHTS:
        return jsonify({"error": "Flight not found"}), 404
    
    flight = FLIGHTS[flight_key]
    
    now = time.time()
    if _cache["data"] and (now - _cache["ts"]) < _cache["ttl"]:
        cached_pos = _cache["data"].get(flight_key)
        if cached_pos:
            return jsonify(cached_pos)
    
    all_flights = fetch_pocketworld()
    pos = match_flight(all_flights, flight["callsign_variants"])
    
    if not pos:
        reason = "No se pudo conectar con el radar" if all_flights is None else "Avión aún no localizado"
        return jsonify({
            "flight": flight_key,
            "found": False,
            "message": reason,
            "dep": flight["dep"],
            "arr": flight["arr"],
            "status": flight["status"]
        })
    
    pos["flight"] = flight_key
    pos["found"] = True
    pos["status"] = "en-route" if not pos.get("on_ground") else "landed"
    
    if not _cache["data"]:
        _cache["data"] = {}
    _cache["data"][flight_key] = pos
    _cache["ts"] = now
    
    return jsonify(pos)

@app.route("/api/all")
def all_flights_endpoint():
    flights = fetch_pocketworld()
    results = {}
    for key, flight in FLIGHTS.items():
        pos = match_flight(flights, flight["callsign_variants"])
        if pos:
            pos["flight"] = key
            pos["found"] = True
            pos["status"] = "en-route" if not pos.get("on_ground") else "landed"
        else:
            pos = {"flight": key, "found": False, "message": "No localizado",
                   "dep": flight["dep"], "arr": flight["arr"], "status": flight["status"]}
        results[key] = pos
    return jsonify(results)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
