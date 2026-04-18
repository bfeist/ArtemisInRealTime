import requests

# Test the IA collection query directly
url = "https://archive.org/advancedsearch.php"
params = {
    "q": "collection:Artemis-I-Still-Imagery",
    "fl[]": ["identifier", "title"],
    "rows": 10,
    "page": 1,
    "output": "json",
}
r = requests.get(url, params=params, timeout=30)
data = r.json()
print("numFound:", data.get("response", {}).get("numFound"))
print("docs:", data.get("response", {}).get("docs", [])[:5])

# Try alternate queries
params2 = {
    "q": "collection:Artemis-I-Still-Imagery OR collection:\"Artemis-I-Still-Imagery\"",
    "fl[]": ["identifier", "title"],
    "rows": 10,
    "page": 1,
    "output": "json",
}
r2 = requests.get(url, params=params2, timeout=30)
data2 = r2.json()
print("\nAlt query numFound:", data2.get("response", {}).get("numFound"))

# Search for Artemis I on IA
params3 = {
    "q": "Artemis I Still Imagery",
    "fl[]": ["identifier", "title", "collection", "mediatype"],
    "rows": 10,
    "page": 1,
    "output": "json",
}
r3 = requests.get(url, params=params3, timeout=30)
data3 = r3.json()
print("\nKeyword search numFound:", data3.get("response", {}).get("numFound"))
for d in data3.get("response", {}).get("docs", [])[:5]:
    print(f"  {d.get('identifier')}: {d.get('title')} [{d.get('mediatype')}] collections={d.get('collection')}")

# Try it as a single item identifier
import requests
meta_url = f"https://archive.org/metadata/Artemis-I-Still-Imagery"
r4 = requests.get(meta_url, timeout=30)
data4 = r4.json()
if "metadata" in data4:
    m = data4["metadata"]
    print(f"\nDirect item: {m.get('identifier')} - {m.get('title')} [{m.get('mediatype')}]")
    files = data4.get("files", [])
    print(f"  Files: {len(files)}")
    for f in files[:5]:
        print(f"    {f.get('name')} ({f.get('format')})")
else:
    print("\nNot a direct item")
