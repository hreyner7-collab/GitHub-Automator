"""Local executor table for the FreeTheAI tool shim.

These are the built-in data tools the shim runs ITSELF while the native
planner plans: open-meteo weather, web page fetch, local date/time, and
sandboxed arithmetic. Every executor returns a plain string; failures are
returned as "error: ..." strings, never raised, and unknown tools get the
honest no-fake-data capability list instead of invented results.
"""
import ast, json, re, time, urllib.parse, urllib.request

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# Marker inside the honest unknown-tool record; the shim recognizes it to
# answer such requests directly (no gateway round, no filter to trip).
UNKNOWN_TOOL_MARKER = "not available in this assistant"

def _norm_args(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            v = json.loads(raw)
            if isinstance(v, dict):
                return v
        except Exception:
            pass
        # models sometimes emit 'city=Berlin' or 'city = Berlin, units = metric'
        pairs = {k.strip(): v.strip().strip("\"'")
                 for k, v in re.findall(r"([A-Za-z_][\w -]*?)\s*=\s*([^,;]+)", raw)
                 if k.strip()}
        if pairs:
            return pairs
        return {"input": raw}
    return {"input": str(raw)}

def _http_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

_WMO = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
        45: "fog", 48: "rime fog", 51: "light drizzle", 53: "drizzle",
        55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
        71: "light snow", 73: "snow", 75: "heavy snow", 80: "rain showers",
        81: "heavy rain showers", 82: "violent rain showers",
        95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm"}

def builtin_get_weather(args):
    city = str(args.get("city") or args.get("location") or args.get("place")
               or args.get("input") or "").strip()
    if not city:
        return "error: no city provided"
    try:
        geo = _http_json("https://geocoding-api.open-meteo.com/v1/search?name="
                         + urllib.parse.quote(city) + "&count=1&language=en&format=json")
        if not geo.get("results"):
            return f"error: no location named '{city}'"
        p = geo["results"][0]
        fc = _http_json("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
                        "&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
                        "&timezone=auto" % (p["latitude"], p["longitude"]))
        cur = fc.get("current", {})
        try:
            desc = _WMO.get(int(cur.get("weather_code", -1)), "unknown sky")
        except Exception:
            desc = "unknown sky"
        return (f"current weather in {p.get('name', city)}: {cur.get('temperature_2m')} C, "
                f"{desc}, humidity {cur.get('relative_humidity_2m')}%, "
                f"wind {cur.get('wind_speed_10m')} km/h")
    except Exception as e:
        return f"error fetching weather: {e}"

def builtin_fetch_url(args):
    url = str(args.get("url") or args.get("input") or "").strip()
    if not url.startswith(("http://", "https://")):
        return "error: need an absolute http(s) url"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            txt = r.read().decode("utf-8", "replace")
        txt = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", txt, flags=re.I)
        txt = re.sub(r"<[^>]+>", " ", txt)
        txt = re.sub(r"\s+", " ", txt).strip()
        return "page text: " + txt[:2500]
    except Exception as e:
        return f"error fetching page: {e}"

def builtin_now(args):
    return time.strftime("current local date/time: %Y-%m-%d %H:%M:%S (%A)")

def builtin_calc(args):
    expr = str(args.get("expression") or args.get("input") or "").strip()
    try:
        node = ast.parse(expr, mode="eval")
        allowed = (ast.Expression, ast.Constant, ast.UnaryOp, ast.BinOp,
                   ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod,
                   ast.USub, ast.UAdd, ast.Load, ast.operator, ast.unaryop)
        for x in ast.walk(node):
            if not isinstance(x, allowed):
                raise ValueError("only plain arithmetic is allowed")
        return f"{expr} = {eval(compile(node, '<calc>', 'eval'))}"
    except Exception as e:
        return f"error computing '{expr}': {e}"

def builtin_exec(name, raw_args):
    a = _norm_args(raw_args)
    n = (name or "").lower()
    if "weather" in n:
        return builtin_get_weather(a)
    if any(k in n for k in ("fetch", "url", "open", "browse", "read_page", "web")):
        return builtin_fetch_url(a)
    if "time" in n or "date" in n:
        return builtin_now(a)
    if "calc" in n or "math" in n:
        return builtin_calc(a)
    return (f"Tool '{name}' is not available in this assistant. Capabilities that DO work: "
            "weather lookup (city), web page fetch (url), current date/time, arithmetic. "
            "Answer the user without this tool, or restate the task using one of those.")
