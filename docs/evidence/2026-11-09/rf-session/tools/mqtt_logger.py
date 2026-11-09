"""Log every classifier observation, background summary, template status and
receiver-state message from the sensor's broker to JSONL, each with the host
receive time and the retained flag. Read-only subscriber."""
import json, sys, time
import paho.mqtt.client as mqtt

OUT = sys.argv[1]
TOPICS = ["state/classifier/observation", "state/classifier/background", "state/classifier/templates_status",
          "state/classifier/enabled", "state/rx/#", "state/spectro/#"]
f = open(OUT, "a", buffering=1)
last = {}


def on_connect(c, u, flags, rc, props=None):
    for t in TOPICS:
        c.subscribe(t, qos=0)
    f.write(json.dumps({"t": round(time.time(), 3), "event": "connected", "rc": str(rc)}) + "\n")


def on_message(c, u, m):
    p = m.payload.decode(errors="replace")
    # receiver topics are republished every 2 s: keep only changes (and the first copy)
    if m.topic.startswith("state/rx/") or m.topic.startswith("state/spectro/"):
        if m.topic in ("state/rx/rate", "state/rx/rssi"):
            return
        if last.get(m.topic) == p:
            return
        last[m.topic] = p
    rec = {"t": round(time.time(), 3), "topic": m.topic, "retained": bool(m.retain)}
    try:
        rec["json"] = json.loads(p) if m.topic.startswith("state/classifier/") and p[:1] == "{" else None
    except ValueError:
        rec["json"] = None
    if rec["json"] is None:
        rec["payload"] = p
    f.write(json.dumps(rec) + "\n")


c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"rf_session_logger_{int(time.time())}")
c.on_connect = on_connect
c.on_message = on_message
c.connect("192.168.50.25", 1883, 30)
c.loop_forever(retry_first_connection=True)
