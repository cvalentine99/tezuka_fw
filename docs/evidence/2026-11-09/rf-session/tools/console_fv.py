"""Open the console's classifier page in headless Chrome (CDP on 9333), read the
Decision panel's Matched window / Whole-span peak / Peak inside window fields a
few times, and take a screenshot. Read-only: nothing on the page is clicked."""
import asyncio, base64, json, sys, time, urllib.request
import websockets
OUT = sys.argv[1]
READ = """(() => {
  const dims = [...document.querySelectorAll('.dim')];
  const get = (w) => { const el = dims.find(d => d.textContent.trim() === w); return el && el.nextElementSibling ? el.nextElementSibling.textContent.trim() : null; };
  const o = {};
  for (const w of ['Matched window','Whole-span residual peak','Peak inside window','Best score','Observation','Templates','Config binding']) o[w] = get(w);
  o.pill = [...document.querySelectorAll('.pill')].map(p => p.textContent.trim());
  o.has_legend = document.body.innerText.includes('Whole-span residual peak (amber dashed marker)');
  return JSON.stringify(o);
})()"""
async def main():
    t = [x for x in json.load(urllib.request.urlopen("http://127.0.0.1:9333/json")) if x["type"] == "page"][0]
    async with websockets.connect(t["webSocketDebuggerUrl"], max_size=50_000_000) as ws:
        n = [0]
        async def call(m, **p):
            n[0] += 1; i = n[0]
            await ws.send(json.dumps({"id": i, "method": m, "params": p}))
            while True:
                r = json.loads(await ws.recv())
                if r.get("id") == i:
                    if "error" in r: raise RuntimeError(r["error"])
                    return r.get("result", {})
        await call("Page.enable"); await call("Runtime.enable")
        await call("Emulation.setDeviceMetricsOverride", width=1400, height=1500, deviceScaleFactor=1, mobile=False)
        await call("Page.navigate", url=f"http://192.168.50.25/?v={int(time.time())}#classifier")
        await asyncio.sleep(14)
        reads = []
        for k in range(4):
            r = await call("Runtime.evaluate", expression=READ, returnByValue=True)
            reads.append({"t_utc": time.strftime("%H:%M:%SZ", time.gmtime()), **json.loads(r["result"]["value"])})
            sh = await call("Page.captureScreenshot", format="png", clip={"x": 262, "y": 220, "width": 1118, "height": 560, "scale": 1})
            open(f"{OUT}/read{k}.png", "wb").write(base64.b64decode(sh["data"]))
            await asyncio.sleep(3)
        shot = await call("Page.captureScreenshot", format="png", captureBeyondViewport=True)
        open(OUT + "/console_positive_check.png", "wb").write(base64.b64decode(shot["data"]))
        logs = await call("Runtime.evaluate", expression="document.title", returnByValue=True)
        json.dump({"title": logs["result"]["value"], "reads": reads}, open(OUT + "/console_positive_check.json", "w"), indent=1)
        print(json.dumps(reads, indent=1))
asyncio.run(main())
