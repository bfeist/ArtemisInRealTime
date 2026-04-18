"""Temp: test IO API with cols= and as= params — need real keyword."""
import json
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

IO_KEY = "002773CC-9413-D6D4-DF25C3C2D2487A41"
BASE = "https://io.jsc.nasa.gov/api/search"
H = {"Origin": "coda.fit.nasa.gov"}

tests = [
    # Use a real keyword + cols + as
    ("artemis + A2 VIDEO col + as=2", f"{BASE}/q=artemis&rpp=3&cols=2399266&as=2?key={IO_KEY}&format=json"),
    ("artemis + A2 parent col + as=2", f"{BASE}/q=artemis&rpp=3&cols=2380537&as=2?key={IO_KEY}&format=json"),
    ("artemis + A2 parent col + as=1", f"{BASE}/q=artemis&rpp=3&cols=2380537&as=1?key={IO_KEY}&format=json"),
    ("artemis no filter", f"{BASE}/q=artemis&rpp=3?key={IO_KEY}&format=json"),
    # Try with so= (sort order) like ISSiRT does
    ("artemis + A2 VIDEO + as=2 + so=7", f"{BASE}/q=artemis&rpp=3&cols=2399266&as=2&so=7?key={IO_KEY}&format=json"),
    # Try with empty q and cols
    ("q= empty + cols + as=2", f"{BASE}/q=&rpp=3&cols=2399266&as=2?key={IO_KEY}&format=json"),
    # Try with a date range like ISSiRT
    ("date + cols + as=2", f"{BASE}/q=&rpp=3&cols=2399266&as=2&dr=2026-03-22T00:00:00Z,2026-04-16T00:00:00Z?key={IO_KEY}&format=json"),
]

for label, url in tests:
    try:
        r = requests.get(url, verify=False, headers=H, timeout=15)
        ct = r.headers.get("content-type", "")
        if "json" not in ct and "text/plain" not in ct:
            print(f"  {label}: non-JSON (ct={ct}, len={len(r.text)})")
            continue
        data = r.json()
        n = data["results"]["response"]["numfound"]
        print(f"  {label}: {n} results")
        if n > 0:
            doc = data["results"]["response"]["docs"][0]
            print(f"    first: nasa_id={doc.get('nasa_id')}, type={doc.get('asset_type')}")
    except Exception as e:
        print(f"  {label}: ERROR {e}")
