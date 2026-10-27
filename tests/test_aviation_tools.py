
class Context:
    def __init__(self, name):
        self.client_context = type("CC", (), {"custom": {"bedrockAgentCoreToolName": f"AviationTools___{name}"}})()


def call(tools, name, **args):
    return tools.lambda_handler(args, Context(name))


def test_unknown_tool(tools):
    assert "Unknown tool" in call(tools, "nope")["error"]


def test_timeline_orders_steps(tools):
    out = call(tools, "plan_trip_timeline", departure_time_local="2026-12-12T21:40",
               timezone="Africa/Lagos", international=True, checked_bags=True, travel_minutes_to_airport=60)
    steps = [s["step"] for s in out["timeline"]]
    assert steps[0] == "Leave home" and steps[-1] == "Departure"
    assert out["timeline"][1]["time_local"].endswith("18:40")


def test_country_info_parses_currency(tools, monkeypatch):
    monkeypatch.setattr(tools, "_http_json", lambda *a, **k: [{
        "name": {"common": "Morocco", "official": "Kingdom of Morocco"}, "cca2": "MA", "cca3": "MAR",
        "capital": ["Rabat"], "currencies": {"MAD": {"name": "Moroccan dirham", "symbol": "د.م."}},
        "languages": {"ara": "Arabic", "ber": "Berber"}, "timezones": ["UTC"], "idd": {"root": "+2", "suffixes": ["12"]},
        "car": {"side": "right"}}])
    result = call(tools, "get_country_info", country="Morocco")["results"][0]
    assert result["currencies"][0]["code"] == "MAD"
    assert result["calling_code"] == "+212"


def test_exchange_rate_converts(tools, monkeypatch):
    monkeypatch.setattr(tools, "_http_json", lambda *a, **k: {
        "result": "success", "rates": {"NGN": 160.0}, "time_last_update_utc": "today"})
    out = call(tools, "get_exchange_rate", from_currency="MAD", to_currency="NGN", amount=1000)
    assert out["converted"] == 160000.0


def test_ground_transport_empty_route_has_fallback_note(tools, monkeypatch):
    monkeypatch.setattr(tools, "_providers", {"google_maps_api_key": "k"})
    monkeypatch.setattr(tools, "_http_json", lambda *a, **k: {"routes": []})
    out = call(tools, "search_ground_transport", origin="Lagos", destination="Abuja")
    assert out["routes"] == [] and "web search" in out["note"]


def test_booking_request_requires_contact(tools, monkeypatch):
    monkeypatch.setenv("BOOKINGS_TABLE", "airxpact-bookings")
    out = call(tools, "create_booking_request", booking_type="bus", traveller_name="Ada", contact="")
    assert "required" in out["error"]


def test_booking_request_saves_and_notifies(tools, monkeypatch):
    saved, published = [], []
    table = type("T", (), {"put_item": lambda self, Item: saved.append(Item)})()
    monkeypatch.setenv("BOOKINGS_TABLE", "airxpact-bookings")
    monkeypatch.setenv("BOOKINGS_TOPIC_ARN", "arn:aws:sns:us-east-1:111122223333:airxpact-booking-requests")
    monkeypatch.setattr(tools, "_dynamodb", type("D", (), {"Table": lambda self, name: table})())
    monkeypatch.setattr(tools, "_sns", type("S", (), {"publish": lambda self, **k: published.append(k)})())
    out = call(tools, "create_booking_request", booking_type="bus", traveller_name="Ada Obi",
               contact="+2348000000000", passengers=2, travel_date="2026-11-02", details="Lagos to Abuja")
    assert out["booking_reference"].startswith("AXP-")
    assert saved[0]["status"] == "REQUESTED" and saved[0]["passengers"] == 2
    assert out["booking_reference"] in published[0]["Subject"]
