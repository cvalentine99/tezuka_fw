"""capture_waterfall.py - read-only capture of a sensor's /waterfall feed.

Writes the raw float32 websocket messages (step word included) back to back:
exactly the sequence the classifier daemon replays with -r, and the layout
rf_compare.py --recorded expects. The receiver settings (/api) are read before
and after and stored with the file's SHA-256 in <out>.meta.json. Nothing on
the sensor is changed.

    python3 capture_waterfall.py <host> <frames> <out.raw>
"""
import asyncio, json, struct, sys, time, hashlib, urllib.request
import websockets
host, n, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
api = json.load(urllib.request.urlopen(f"http://{host}/api", timeout=10))
meta = {"host": host, "captured_unix_s": time.time(), "api": {k: api.get(k) for k in ("ad9361", "spectrometer", "ddc", "versions")}, "frames": 0, "frame_sizes": {}}
async def main():
    got = 0
    with open(out, "wb") as f:
        async with websockets.connect(f"ws://{host}:80/waterfall", max_size=None) as ws:
            while got < n:
                msg = await ws.recv()
                if not isinstance(msg, (bytes, bytearray)): continue
                f.write(msg); got += 1
                meta["frame_sizes"][str(len(msg))] = meta["frame_sizes"].get(str(len(msg)), 0) + 1
    meta["frames"] = got
asyncio.run(main())
meta["api_after"] = json.load(urllib.request.urlopen(f"http://{host}/api", timeout=10)).get("ad9361")
meta["sha256"] = hashlib.sha256(open(out, "rb").read()).hexdigest()
json.dump(meta, open(out + ".meta.json", "w"), indent=1)
print(json.dumps(meta))
