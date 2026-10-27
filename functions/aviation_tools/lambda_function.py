"""AgentCore Gateway tool handlers: weather, flights, ground transport, currency, flight status and bookings."""
import json
import os
import random
import string
import time
import urllib.error
import re
import uuid
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import boto3

UA = {"User-Agent": "Airxpact/1.0 (aviation assistant)"}
_secrets = boto3.client("secretsmanager")
_s3 = boto3.client("s3")
_dynamodb = boto3.resource("dynamodb")
_sns = boto3.client("sns")
_duffel_key = None
_providers = None


def _http_json(url, method="GET", headers=None, body=None, timeout=25):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw.strip() else None


def get_aviation_weather(args):
    codes = re.sub(r"[^A-Za-z0-9,]", "", str(args.get("icao_codes", ""))).upper()
    if not codes:
        return {"error": "Provide icao_codes, e.g. 'DNMM,EGLL'"}
    base = "https://aviationweather.gov/api/data"
    metar = _http_json(f"{base}/metar?ids={codes}&format=json") or []
    taf = _http_json(f"{base}/taf?ids={codes}&format=json") or []
    out = {}
    for m in metar:
        out.setdefault(m.get("icaoId"), {})["metar"] = {
            "raw": m.get("rawOb"), "observed": m.get("reportTime"), "temp_c": m.get("temp"),
            "wind_dir": m.get("wdir"), "wind_kt": m.get("wspd"), "gust_kt": m.get("wgst"),
            "visibility": m.get("visib"), "flight_category": m.get("fltCat"), "name": m.get("name"),
        }
    for t in taf:
        out.setdefault(t.get("icaoId"), {})["taf"] = {"raw": t.get("rawTAF"), "issued": t.get("issueTime")}
    return {"airports": out, "note": "Explain these reports in plain language and highlight delay risks."}


def get_destination_forecast(args):
    city = str(args.get("city", "")).strip()
    if not city:
        return {"error": "Provide a city name"}
    geo = _http_json("https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode({"name": city, "count": 1}))
    if not geo or not geo.get("results"):
        return {"error": f"Could not find '{city}'"}
    g = geo["results"][0]
    params = {
        "latitude": g["latitude"], "longitude": g["longitude"], "timezone": "auto",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max",
        "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,visibility",
        "forecast_days": 16,
    }
    fc = _http_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params))
    daily = fc.get("daily", {})
    days = [
        {"date": d, "max_c": daily["temperature_2m_max"][i], "min_c": daily["temperature_2m_min"][i],
         "rain_chance_pct": daily["precipitation_probability_max"][i], "max_wind_kmh": daily["wind_speed_10m_max"][i],
         "wmo_code": daily["weather_code"][i]}
        for i, d in enumerate(daily.get("time", []))
    ]
    start, end = args.get("start_date"), args.get("end_date")
    if start:
        days = [d for d in days if d["date"] >= start and (not end or d["date"] <= end)]
    result = {"place": f'{g.get("name")}, {g.get("country")}', "timezone": fc.get("timezone"),
              "current": fc.get("current"), "days": days, "source": "Open-Meteo (live)"}
    if start and not days:
        result["note"] = ("Requested dates are beyond the 16-day forecast. Give typical seasonal "
                          "weather from general knowledge and say it is not a forecast.")
    return result


def _duffel_api_key():
    global _duffel_key
    if _duffel_key is None:
        sec = _secrets.get_secret_value(SecretId=os.environ["DUFFEL_SECRET_ARN"])["SecretString"]
        _duffel_key = json.loads(sec)["api_key"]
    return _duffel_key


def _iso_duration_minutes(d):
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?", d or "")
    if not m:
        return None
    days, h, mins = (int(x or 0) for x in m.groups())
    return days * 1440 + h * 60 + mins


def search_flights(args):
    origin = str(args.get("origin", "")).upper()
    destination = str(args.get("destination", "")).upper()
    dep = args.get("departure_date")
    if not (origin and destination and dep):
        return {"error": "origin, destination (IATA codes) and departure_date (YYYY-MM-DD) are required"}
    slices = [{"origin": origin, "destination": destination, "departure_date": dep}]
    if args.get("return_date"):
        slices.append({"origin": destination, "destination": origin, "departure_date": args["return_date"]})
    adults = max(1, int(args.get("adults", 1)))
    body = {"data": {
        "slices": slices,
        "passengers": [{"type": "adult"} for _ in range(adults)],
        "cabin_class": args.get("cabin_class", "economy"),
        "max_connections": int(args.get("max_connections", 1)),
    }}
    resp = _http_json(
        "https://api.duffel.com/air/offer_requests?return_offers=true&supplier_timeout=20000",
        method="POST", body=body, timeout=40,
        headers={"Authorization": f"Bearer {_duffel_api_key()}", "Duffel-Version": "v2",
                 "Accept": "application/json", "Content-Type": "application/json"},
    )
    offers = (resp or {}).get("data", {}).get("offers", [])
    max_price = args.get("max_price")
    results = []
    for o in sorted(offers, key=lambda x: float(x["total_amount"])):
        if max_price and float(o["total_amount"]) > float(max_price):
            continue
        sl_out = []
        for s in o.get("slices", []):
            segs = s.get("segments", [])
            layovers = []
            for a, b in zip(segs, segs[1:]):
                gap = datetime.fromisoformat(b["departing_at"]) - datetime.fromisoformat(a["arriving_at"])
                layovers.append({"airport": a["destination"]["iata_code"], "minutes": int(gap.total_seconds() // 60)})
            bags = (segs[0].get("passengers") or [{}])[0].get("baggages", []) if segs else []
            sl_out.append({
                "from": s["origin"]["iata_code"], "to": s["destination"]["iata_code"],
                "duration_minutes": _iso_duration_minutes(s.get("duration")),
                "stops": len(segs) - 1, "layovers": layovers, "fare_brand": s.get("fare_brand_name"),
                "baggage": bags,
                "segments": [{
                    "flight": f'{g["marketing_carrier"]["iata_code"]}{g.get("marketing_carrier_flight_number", "")}',
                    "carrier": g["marketing_carrier"].get("name"),
                    "from": g["origin"]["iata_code"], "to": g["destination"]["iata_code"],
                    "departs_local": g["departing_at"], "arrives_local": g["arriving_at"],
                    "aircraft": (g.get("aircraft") or {}).get("name"),
                } for g in segs],
            })
        cond = o.get("conditions") or {}
        results.append({
            "offer_id": o["id"], "price": o["total_amount"], "currency": o["total_currency"],
            "airline": (o.get("owner") or {}).get("name"), "slices": sl_out,
            "change_before_departure": cond.get("change_before_departure"),
            "refund_before_departure": cond.get("refund_before_departure"),
            "expires_at": o.get("expires_at"),
        })
        if len(results) >= int(args.get("max_results", 6)):
            break
    test_mode = _duffel_api_key().startswith("duffel_test_")
    return {"offers": results, "count": len(results),
            "data_mode": "TEST DATA - sample airline and prices, NOT real fares" if test_mode else "LIVE fares",
            "note": ("Duffel is in TEST mode: tell the traveller these are sample results, not real prices or "
                     "airlines, and suggest checking the airline's website." if test_mode else
                     "Prices are live. Booking and payment must be completed with the airline or partner.")}


def plan_trip_timeline(args):
    tz = ZoneInfo(args.get("timezone", "UTC"))
    dep = datetime.fromisoformat(args["departure_time_local"]).replace(tzinfo=tz)
    international = bool(args.get("international", True))
    bags = bool(args.get("checked_bags", True))
    travel = int(args.get("travel_minutes_to_airport", 60))
    buffer = int(args.get("traffic_buffer_minutes", 30))
    arrive_before = 180 if international else 120
    if not bags:
        arrive_before -= 30
    arrive_airport = dep - timedelta(minutes=arrive_before)
    steps = [
        ("Leave home", arrive_airport - timedelta(minutes=travel + buffer)),
        ("Arrive at airport", arrive_airport),
        ("Check-in / bag drop closes (typical)", dep - timedelta(minutes=60 if international else 45)),
        ("Be through security" + (" and immigration" if international else ""), dep - timedelta(minutes=50 if international else 40)),
        ("Boarding starts (typical)", dep - timedelta(minutes=45 if international else 35)),
        ("Gate closes (typical)", dep - timedelta(minutes=20 if international else 15)),
        ("Departure", dep),
    ]
    return {"timezone": str(tz), "airport": args.get("departure_airport"),
            "timeline": [{"step": s, "time_local": t.strftime("%a %d %b %Y %H:%M")} for s, t in steps],
            "note": "Typical times; tell the user to confirm check-in and gate closing times with their airline."}


def create_calendar_file(args):
    events = args.get("events") or []
    if not events:
        return {"error": "Provide events: [{title, start_local, end_local, timezone, location}]"}
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Airxpact//Trip//EN", "CALSCALE:GREGORIAN"]
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for e in events:
        tz = ZoneInfo(e.get("timezone", "UTC"))
        start = datetime.fromisoformat(e["start_local"]).replace(tzinfo=tz).astimezone(ZoneInfo("UTC"))
        end = datetime.fromisoformat(e.get("end_local") or e["start_local"]).replace(tzinfo=tz).astimezone(ZoneInfo("UTC"))
        if end <= start:
            end = start + timedelta(minutes=30)
        lines += [
            "BEGIN:VEVENT", f"UID:{uuid.uuid4()}@airxpact", f"DTSTAMP:{now}",
            f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}", f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}",
            f"SUMMARY:{e.get('title', 'Trip event')}", f"LOCATION:{e.get('location', '')}",
            "BEGIN:VALARM", "TRIGGER:-PT2H", "ACTION:DISPLAY", f"DESCRIPTION:{e.get('title', 'Reminder')}", "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    key = f"trips/{uuid.uuid4()}.ics"
    bucket = os.environ["TRIP_FILES_BUCKET"]
    _s3.put_object(Bucket=bucket, Key=key, Body="\r\n".join(lines).encode(), ContentType="text/calendar")
    url = _s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=6 * 3600)
    return {"download_url": url, "events": len(events),
            "note": "Tell the user to open the link soon (it expires within a few hours) to add the trip and reminders to their calendar."}


def _provider_keys():
    global _providers
    if _providers is None:
        arn = os.environ.get("PROVIDERS_SECRET_ARN", "").strip()
        _providers = json.loads(_secrets.get_secret_value(SecretId=arn)["SecretString"]) if arn else {}
    return _providers


def get_country_info(args):
    name = str(args.get("country", "")).strip()
    if not name:
        return {"error": "Provide country, e.g. 'France' or 'GB'"}
    fields = "name,cca2,cca3,currencies,languages,capital,timezones,idd,region,car,flags"
    q = urllib.parse.quote(name)
    url = (f"https://restcountries.com/v3.1/alpha/{q}?fields={fields}" if len(name) in (2, 3) and name.isalpha()
           else f"https://restcountries.com/v3.1/name/{q}?fields={fields}")
    try:
        data = _http_json(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"error": f"No country found for '{name}'"}
        raise
    rows = data if isinstance(data, list) else [data]
    out = []
    for c in rows[:3]:
        idd = c.get("idd") or {}
        out.append({
            "country": (c.get("name") or {}).get("common"), "official_name": (c.get("name") or {}).get("official"),
            "iso2": c.get("cca2"), "iso3": c.get("cca3"), "capital": c.get("capital"),
            "currencies": [{"code": k, "name": v.get("name"), "symbol": v.get("symbol")}
                           for k, v in (c.get("currencies") or {}).items()],
            "languages": list((c.get("languages") or {}).values()),
            "timezones": c.get("timezones"),
            "calling_code": (idd.get("root", "") + (idd.get("suffixes") or [""])[0]) if idd else None,
            "drives_on": (c.get("car") or {}).get("side"),
        })
    return {"results": out, "source": "restcountries.com"}


def get_exchange_rate(args):
    base = str(args.get("from_currency", "USD")).upper()[:3]
    target = str(args.get("to_currency", "NGN")).upper()[:3]
    amount = float(args.get("amount", 1) or 1)
    data = _http_json(f"https://open.er-api.com/v6/latest/{base}")
    if not data or data.get("result") != "success":
        return {"error": f"Exchange rate service failed for {base}: {(data or {}).get('error-type')}"}
    rate = (data.get("rates") or {}).get(target)
    if rate is None:
        return {"error": f"No rate for {target}"}
    return {"from": base, "to": target, "rate": rate, "amount": amount, "converted": round(amount * rate, 2),
            "rate_updated": data.get("time_last_update_utc"),
            "source": "open.er-api.com (daily reference rate; bureau and bank rates differ)"}


def search_ground_transport(args):
    key = _provider_keys().get("google_maps_api_key")
    if not key:
        return {"error": "Ground transport search is not configured (google_maps_api_key missing)"}
    origin, destination = str(args.get("origin", "")), str(args.get("destination", ""))
    if not origin or not destination:
        return {"error": "origin and destination are required (addresses, stations or cities)"}
    body = {"origin": {"address": origin}, "destination": {"address": destination}, "travelMode": "TRANSIT",
            "computeAlternativeRoutes": True, "languageCode": args.get("language", "en")}
    if args.get("departure_time_utc"):
        body["departureTime"] = args["departure_time_utc"]
    modes = args.get("modes") or ["BUS"]
    body["transitPreferences"] = {"allowedTravelModes": [m.upper() for m in modes]}
    fields = ("routes.duration,routes.distanceMeters,routes.localizedValues,routes.travelAdvisory.transitFare,"
              "routes.legs.steps.transitDetails,routes.legs.steps.travelMode,routes.legs.steps.localizedValues")
    try:
        data = _http_json("https://routes.googleapis.com/directions/v2:computeRoutes", method="POST", body=body,
                          headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": fields,
                                   "Content-Type": "application/json"})
    except urllib.error.HTTPError as e:
        return {"error": f"Routes API HTTP {e.code}: {e.read().decode()[:300]}"}
    routes = []
    for r in (data or {}).get("routes", [])[:4]:
        legs = []
        for leg in r.get("legs", []):
            for st in leg.get("steps", []):
                td = st.get("transitDetails")
                if td:
                    line = td.get("transitLine") or {}
                    legs.append({
                        "operator": ", ".join(a.get("name", "") for a in line.get("agencies", [])),
                        "operator_website": ", ".join(a.get("uri", "") for a in line.get("agencies", [])),
                        "line": line.get("nameShort") or line.get("name"),
                        "vehicle": (line.get("vehicle") or {}).get("type"),
                        "from_stop": ((td.get("stopDetails") or {}).get("departureStop") or {}).get("name"),
                        "to_stop": ((td.get("stopDetails") or {}).get("arrivalStop") or {}).get("name"),
                        "departs": ((td.get("localizedValues") or {}).get("departureTime") or {}).get("time", {}).get("text"),
                        "arrives": ((td.get("localizedValues") or {}).get("arrivalTime") or {}).get("time", {}).get("text"),
                    })
        fare = (r.get("travelAdvisory") or {}).get("transitFare")
        routes.append({"duration": (r.get("localizedValues") or {}).get("duration", {}).get("text"),
                       "distance": (r.get("localizedValues") or {}).get("distance", {}).get("text"),
                       "fare": f'{fare.get("units")} {fare.get("currencyCode")}' if fare else None, "legs": legs})
    if not routes:
        return {"routes": [], "note": ("No scheduled transit found for this route in Google's data. Many intercity "
                                       "buses (for example in Nigeria) are not listed: use web search for operators "
                                       "serving this route and give their official booking links.")}
    return {"routes": routes, "source": "Google Routes API (live schedules where published)",
            "note": "To book, offer create_booking_request or the operator's website."}


def get_flight_status(args):
    key = _provider_keys().get("aviationstack_api_key")
    if not key:
        return {"error": "Flight status is not configured (aviationstack_api_key missing)"}
    flight = re.sub(r"\s", "", str(args.get("flight_number", ""))).upper()
    if not flight:
        return {"error": "Provide flight_number, e.g. P47120 or BA74"}
    params = {"access_key": key, "flight_iata": flight}
    if args.get("date"):
        params["flight_date"] = args["date"]
    base = os.environ.get("AVIATIONSTACK_BASE", "https://api.aviationstack.com/v1").rstrip("/")
    data = _http_json(f"{base}/flights?" + urllib.parse.urlencode(params))
    if (data or {}).get("error"):
        return {"error": f"aviationstack: {data['error'].get('message') or data['error']}"}
    out = []
    for f in (data or {}).get("data", [])[:3]:
        dep, arr = f.get("departure") or {}, f.get("arrival") or {}
        out.append({"date": f.get("flight_date"), "status": f.get("flight_status"),
                    "airline": (f.get("airline") or {}).get("name"),
                    "from": dep.get("iata"), "scheduled_departure": dep.get("scheduled"),
                    "estimated_departure": dep.get("estimated"), "departure_delay_min": dep.get("delay"),
                    "terminal": dep.get("terminal"), "gate": dep.get("gate"),
                    "to": arr.get("iata"), "scheduled_arrival": arr.get("scheduled"),
                    "estimated_arrival": arr.get("estimated"), "arrival_delay_min": arr.get("delay")})
    return {"flights": out, "source": "aviationstack (times in UTC unless stated)",
            "note": "Confirm gate and time with the airline app or airport screens."} if out else \
        {"flights": [], "note": f"No status found for {flight}. Check the airline's website."}


def create_booking_request(args):
    table = os.environ.get("BOOKINGS_TABLE", "").strip()
    if not table:
        return {"error": "Booking requests are not configured (BOOKINGS_TABLE missing)"}
    kind = str(args.get("booking_type", "")).lower()
    if kind not in ("bus", "flight", "airport_transfer", "translator", "hotel", "tour", "other"):
        return {"error": "booking_type must be bus, flight, airport_transfer, translator, hotel, tour or other"}
    name, contact = str(args.get("traveller_name", "")).strip(), str(args.get("contact", "")).strip()
    if not name or not contact:
        return {"error": "traveller_name and contact (phone or email) are required"}
    ref = "AXP-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    item = {"booking_id": ref, "status": "REQUESTED", "booking_type": kind, "traveller_name": name[:100],
            "contact": contact[:100], "details": json.dumps(args.get("details") or {})[:3000],
            "passengers": int(args.get("passengers", 1) or 1), "travel_date": str(args.get("travel_date", ""))[:20],
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "expires_at": int(time.time()) + 180 * 86400}
    _dynamodb.Table(table).put_item(Item=item)
    topic = os.environ.get("BOOKINGS_TOPIC_ARN", "").strip()
    if topic:
        _sns.publish(TopicArn=topic, Subject=f"Airxpact booking request {ref} ({kind})"[:99],
                     Message=json.dumps(item, indent=2))
    return {"booking_reference": ref, "status": "REQUESTED",
            "note": ("This is a booking REQUEST, not a confirmed ticket. The Airxpact team will contact the "
                     "traveller to confirm price and availability. No payment is taken in chat.")}


def get_booking_status(args):
    table = os.environ.get("BOOKINGS_TABLE", "").strip()
    ref = str(args.get("booking_reference", "")).strip().upper()
    if not table or not ref:
        return {"error": "booking_reference is required"}
    item = _dynamodb.Table(table).get_item(Key={"booking_id": ref}).get("Item")
    if not item:
        return {"error": f"No booking request {ref} found"}
    return {k: item.get(k) for k in ("booking_id", "status", "booking_type", "travel_date", "created_at",
                                      "status_note") if item.get(k) is not None}


TOOLS = {
    "get_aviation_weather": get_aviation_weather,
    "get_destination_forecast": get_destination_forecast,
    "search_flights": search_flights,
    "plan_trip_timeline": plan_trip_timeline,
    "create_calendar_file": create_calendar_file,
    "get_country_info": get_country_info,
    "get_exchange_rate": get_exchange_rate,
    "search_ground_transport": search_ground_transport,
    "get_flight_status": get_flight_status,
    "create_booking_request": create_booking_request,
    "get_booking_status": get_booking_status,
}


def lambda_handler(event, context):
    full_name = ""
    try:
        full_name = context.client_context.custom["bedrockAgentCoreToolName"]
    except Exception:
        full_name = (event or {}).pop("tool_name", "")
    tool = full_name.split("___")[-1]
    fn = TOOLS.get(tool)
    if not fn:
        return {"error": f"Unknown tool '{full_name}'"}
    try:
        return fn(event or {})
    except Exception as exc:
        detail = ""
        if isinstance(exc, urllib.error.HTTPError):
            try:
                detail = " " + exc.read().decode()[:300]
            except Exception:
                pass
        print(f"Tool {tool} failed: {exc!r}{detail}")
        return {"error": f"{tool} failed: {exc}{detail}. Tell the traveller this data is unavailable right now "
                         "and give them the official website to check."}
