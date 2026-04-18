import requests
key = "323fc6dee889f650076013db2a16398a"
url = "https://www.flickr.com/services/rest/"

# Look up the user who owns the Artemis II photoset
r = requests.get(url, params={
    "method": "flickr.photosets.getInfo",
    "api_key": key,
    "photoset_id": "72177720307234654",
    "format": "json",
    "nojsoncallback": "1",
}, timeout=15)
data = r.json()
print("A2 album:", data)

# Try Artemis I album too
r2 = requests.get(url, params={
    "method": "flickr.photosets.getInfo",
    "api_key": key,
    "photoset_id": "72177720303788800",
    "format": "json",
    "nojsoncallback": "1",
}, timeout=15)
print("A1 album:", r2.json())

# Also try looking up NASA's user by URL
r3 = requests.get(url, params={
    "method": "flickr.urls.lookupUser",
    "api_key": key,
    "url": "https://www.flickr.com/photos/nasahqphoto/",
    "format": "json",
    "nojsoncallback": "1",
}, timeout=15)
print("nasahqphoto:", r3.json())

r4 = requests.get(url, params={
    "method": "flickr.urls.lookupUser",
    "api_key": key,
    "url": "https://www.flickr.com/photos/naabordenofflight/",
    "format": "json",
    "nojsoncallback": "1",
}, timeout=15)
print("naabordenofflight:", r4.json())
