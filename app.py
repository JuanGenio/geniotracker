#!/usr/bin/env python3
"""
Flight Tracker — Backend Flask v3
Rastrea vuelos vía PocketWorld + OpenSky como fallback
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
        "callsign_variants": ["IBE418", "IBE0418"],
        "airline": "Iberia",
        "dep": {"code": "BCN", "name": "Barcelona", "lat": 41.2971, "lon": 2.0785},
        "arr": {"code": "MAD", "name": "Madrid", "lat": 40.4722, "lon": -3.5634},
        "dep_time": "18:15", "arr_time": "19:40",
        "date": "2026-07-06", "status": "scheduled"
    },
    "IB123": {
        "flight_iata": "IB123",
        "callsign_variants": ["IBE123", "IBE0123"],
        "airline": "Iberia",
        "dep": {"code": "MAD", "name": "Madrid", "lat": 40.4722, "lon": -3.5634},
        "arr": {"code": "LIM", "name": "Lima", "lat": -12.0219, "lon": -77.1143},
        "dep_time": "23:59", "arr_time": "05:25",
        "date": "2026-07-06", "status": "scheduled"
    }
}

_cache = {"data": None, "ts": 0, "ttl": 30}


def fetch_pocketworld():
    """Obtiene vuelos desde PocketWorld"""
    try:
        r = requests.get("https://pocketworld.org/api/flights", timeout=25,
                        headers={"User-Agent": "GenioTracker/1.0"})
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                log.info(f"PocketWorld: {len(data)} flights (array)")
                return data
            elif isinstance(data, dict) and "flights" in data:
                flights = data["flights"]
                log.info(f"PocketWorld: {len(flights)} flights (object)")
                return flights
            else:
                log.warning(f"PocketWorld: formato inesperado: {type(data).__name__}")
                return None
        else:
            log.warning(f"PocketWorld HTTP {r.status_code}")
            return None
    except Exception as e:
        log.warning(f"PocketWorld error: {e}")
        return None


def fetch_opensky():
    """
    Fallback: OpenSky Network (gratuito, sin API key).
    Rate limit: 10s entre requests, 400/día.
    """
    try:
        r = requests.get("https://opensky-network.org/api/states/all", timeout=25,
                        headers={"User-Agent": "GenioTracker/1.0"})
        if r.status_code == 200:
            data = r.json()
            states = data.get("states") or []
            log.info(f"OpenSky: {len(states)} aircraft")
            return states
        elif r.status_code == 429:
            log.warning("OpenSky: rate limited (429)")
            return None
        else:
            log.warning(f"OpenSky HTTP {r.status_code}")
            return None
    except Exception as e:
        log.warning(f"OpenSky error: {e}")
        return None


def match_pocketworld(flights, callsign_variants):
    """Busca vuelo en datos de PocketWorld"""
    if not flights:
        return None
    clean = [cs.strip().upper() for cs in callsign_variants]
    for f in flights:
        cs = (f.get("callsign") or "").strip().upper()
        if cs in clean:
            vel = f.get("velocity")
            alt = f.get("baro_alt") or f.get("alt")
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
                "updated": datetime.now(timezone.utc).isoformat(),
                "source": "PocketWorld"
            }
    return None


def match_opensky(states, callsign_variants):
    """
    Busca vuelo en datos de OpenSky.
    Los states son arrays indexados:
      [0]icao24 [1]callsign [5]lon [6]lat [7]baro_alt
      [8]on_ground [9]velocity [10]heading [11]vertical_rate [13]geo_alt
    """
    if not states:
        return None
    clean = [cs.strip().upper() for cs in callsign_variants]
    for s in states:
        if not s or len(s) < 12:
            continue
        cs = (s[1] or "").strip().upper()
        if cs in clean:
            vel = s[9]  # m/s
            alt = s[7] or s[13] or 0  # baro_alt or geo_alt
            return {
                "icao24": s[0] or "",
                "callsign": cs,
                "lat": s[6],
                "lon": s[5],
                "altitude_m": alt,
                "velocity_kmh": round(vel * 3.6, 1) if vel else None,
                "direction": s[10],
                "vertical_rate_ms": s[11],
                "on_ground": bool(s[8]) if s[8] is not None else False,
                "updated": datetime.now(timezone.utc).isoformat(),
                "source": "OpenSky"
            }
    return None


def build_pos(flight_key, flight, pos):
    """Envuelve posición encontrada con metadatos del vuelo"""
    pos["flight"] = flight_key
    pos["found"] = True
    pos["status"] = "en-route" if not pos.get("on_ground") else "landed"
    return pos


def build_no_pos(flight_key, flight, reason="Avión aún no localizado"):
    return {
        "flight": flight_key,
        "found": False,
        "message": reason,
        "dep": flight["dep"],
        "arr": flight["arr"],
        "status": flight["status"]
    }


@app.route("/")
def index():
    return render_template("index.html", flights=FLIGHTS)


@app.route("/api/track")
def track():
    flight_key = request.args.get("flight", "IB418").upper()
    if flight_key not in FLIGHTS:
        return jsonify({"error": "Flight not found"}), 404

    flight = FLIGHTS[flight_key]
    now = time.time()

    # Cache check
    if _cache["data"] and (now - _cache["ts"]) < _cache["ttl"]:
        cached = _cache["data"].get(flight_key)
        if cached and cached.get("found"):
            return jsonify(cached)

    # Fuente 1: PocketWorld
    pw = fetch_pocketworld()
    pos = match_pocketworld(pw, flight["callsign_variants"])

    # Fuente 2 (fallback): OpenSky si PocketWorld no encontró nada
    if not pos:
        log.info(f"{flight_key}: PocketWorld no encontró, probando OpenSky...")
        os = fetch_opensky()
        pos = match_opensky(os, flight["callsign_variants"])

    if not pos:
        reason = "No se pudo conectar con el radar" if pw is None else "Avión aún no localizado"
        return jsonify(build_no_pos(flight_key, flight, reason))

    pos = build_pos(flight_key, flight, pos)

    if not _cache["data"]:
        _cache["data"] = {}
    _cache["data"][flight_key] = pos
    _cache["ts"] = now

    return jsonify(pos)


@app.route("/api/all")
def all_flights_endpoint():
    pw = fetch_pocketworld()
    os_states = None
    results = {}
    for key, flight in FLIGHTS.items():
        pos = match_pocketworld(pw, flight["callsign_variants"])
        if not pos:
            if os_states is None:
                os_states = fetch_opensky()
            pos = match_opensky(os_states, flight["callsign_variants"])
        if pos:
            results[key] = build_pos(key, flight, pos)
        else:
            results[key] = build_no_pos(key, flight, "No localizado")
    return jsonify(results)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
