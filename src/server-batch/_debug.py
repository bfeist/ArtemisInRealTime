import json, re

with open("F:/_repos/ArtemisInRealTime_assets/artemis-ii/processed/ia_video_catalog.json") as f:
    items = json.load(f)

patterns = [r"jsc\d{4}m\d+", r"art\d+m\d+"]

for item in items:
    ident = item["identifier"]
    nasa_id = None
    for p in patterns:
        m = re.search(p, ident, re.IGNORECASE)
        if m:
            nasa_id = m.group(0)
            break
    print(f"  {ident:70s} -> {nasa_id or 'NO MATCH'}")
