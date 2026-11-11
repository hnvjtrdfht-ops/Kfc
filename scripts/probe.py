"""H.4.1 전체 XML에서 필요한 시리즈 이름 찾기 (임시)."""
import io, re, urllib.request, zipfile
import xml.etree.ElementTree as ET
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124.0 Safari/537.36"}
u = "https://www.federalreserve.gov/datadownload/Output.aspx?rel=H41&filetype=zip"
b = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60).read()
z = zipfile.ZipFile(io.BytesIO(b))
f = z.open("H41_data.xml")
# 첫 4KB 원문 구조
head = z.open("H41_data.xml").read(4000).decode("utf-8", "replace")
print(head)
KW = re.compile(r"total assets|reserve balances|general account|bills|reverse repurchase", re.I)
n = 0
for ev, el in ET.iterparse(f, events=("end",)):
    tag = el.tag.split("}")[-1]
    if tag == "Series":
        texts = [t.text for t in el.iter() if t.tag.split("}")[-1] == "AnnotationText" and t.text]
        desc = " | ".join(texts)
        obs = [o for o in el if o.tag.split("}")[-1] == "Obs"]
        if KW.search(desc) and ("Wednesday" in desc or "week" in desc.lower()):
            last = obs[-1].attrib if obs else {}
            print("SER", dict(el.attrib), "| DESC:", desc[:300], "| nobs", len(obs), "| first", obs[0].attrib if obs else None, "| last", last)
            n += 1
        el.clear()
print("matched", n)
