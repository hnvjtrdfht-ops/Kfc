"""임시: H.4.1에서 지급준비금 관련 시리즈와 값이 있는 기간 확인."""
import io, re, urllib.request, zipfile
import xml.etree.ElementTree as ET
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124.0 Safari/537.36"}
b = urllib.request.urlopen(urllib.request.Request("https://www.federalreserve.gov/datadownload/Output.aspx?rel=H41&filetype=zip", headers=UA), timeout=90).read()
f = zipfile.ZipFile(io.BytesIO(b)).open("H41_data.xml")
KW = re.compile(r"reserve balances|depository institutions", re.I)
for _, el in ET.iterparse(f, events=("end",)):
    if el.tag.split("}")[-1] != "Series":
        continue
    texts = [t.text for t in el.iter() if t.tag.split("}")[-1] == "AnnotationText" and t.text]
    desc = texts[0] if texts else ""
    if KW.search(desc) and "District" not in desc and "change" not in desc and "Wednesday level" in desc:
        obs = [(o.attrib.get("TIME_PERIOD"), o.attrib.get("OBS_VALUE")) for o in el if o.tag.split("}")[-1] == "Obs"]
        nz = [o for o in obs if o[1] not in (None, "ND", "0")]
        pick = {d: v for d, v in obs if d in ("2017-01-04", "2019-09-18", "2020-12-30", "2021-06-30", "2026-11-04")}
        print("SER", el.attrib.get("SERIES_NAME"), "|", desc[:160], "| nonzero", nz[0][0] if nz else None, "~", nz[-1][0] if nz else None, "|", pick, flush=True)
    el.clear()
print("done")
