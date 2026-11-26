"""임시: 새 지표 소스 접속·형식 진단."""
import io, json, re, urllib.request, zipfile
import xml.etree.ElementTree as ET
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124.0 Safari/537.36"}
def get(u, t=60):
    return urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=t).read()
for u in ["https://stablecoins.llama.fi/stablecoincharts/all", "https://stablecoins.llama.fi/stablecoincharts/all?stablecoin=1"]:
    try:
        j = json.loads(get(u)); print("OK", u, type(j), len(j)); print(" first", json.dumps(j[0])[:400]); print(" last", json.dumps(j[-1])[:400])
    except Exception as e: print("ERR", u, e)
for u in ["https://www.okx.com/api/v5/public/funding-rate-history?instId=BTC-USDT-SWAP&limit=3", "https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=2&startTime=1568102400000"]:
    try: print("OK", u, get(u, 20)[:300])
    except Exception as e: print("ERR", u, e)
try:
    z = zipfile.ZipFile(io.BytesIO(get("https://www.federalreserve.gov/datadownload/Output.aspx?rel=H10&filetype=zip", 90)))
    print("H10 files", z.namelist())
    name = [n for n in z.namelist() if n.endswith("_data.xml")][0]
    for _, el in ET.iterparse(z.open(name), events=("end",)):
        if el.tag.split("}")[-1] != "Series": continue
        texts = [t.text for t in el.iter() if t.tag.split("}")[-1] == "AnnotationText" and t.text]
        d = texts[0] if texts else ""
        if re.search(r"broad|index", d, re.I):
            obs = [(o.attrib.get("TIME_PERIOD"), o.attrib.get("OBS_VALUE")) for o in el if o.tag.split("}")[-1] == "Obs"]
            print("SER", dict(el.attrib), "|", d[:150], "| n", len(obs), obs[:1], obs[-1:], flush=True)
        el.clear()
except Exception as e: print("ERR H10", e)
