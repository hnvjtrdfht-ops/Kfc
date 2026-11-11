"""데이터 소스 접속 진단 (임시)."""
import io, json, sys, urllib.request, zipfile
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124.0 Safari/537.36"}
NY = "https://markets.newyorkfed.org/api"
FS = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
C = [
    NY + "/rp/reverserepo/all/results/lastTwoWeeks.json",
    NY + "/rp/reverserepo/all/results/last/3.json",
    NY + "/rp/all/all/results/last/3.json",
    NY + "/rp/reverserepo/all/results/search.json?startDate=2026-10-01&endDate=2026-11-10",
    NY + "/rp/results/search.json?startDate=2026-10-01&endDate=2026-11-10",
    NY + "/rp/reverserepo/propositions/search.json?startDate=2026-10-01&endDate=2026-11-10",
    NY + "/rp/reverserepo/all/results/search.json?startDate=2022-01-01&endDate=2022-06-30",
    "https://api.stlouisfed.org/fred/series/observations?series_id=WALCL&api_key=abcdefghijklmnopqrstuvwxyz123456&file_type=json",
    "https://www.federalreserve.gov/releases/h41/current/h41.htm",
    "https://www.federalreserve.gov/datadownload/Choose.aspx?rel=H41",
    "https://www.federalreserve.gov/datadownload/Output.aspx?rel=H41&filetype=zip",
    FS + "/v1/accounting/od/upcoming_auctions?page[size]=3",
]
for u in C:
    print("=" * 8, u, flush=True)
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=25) as r:
            b = r.read()
            print("status", r.status, r.headers.get("Content-Type"), len(b))
            if b[:2] == b"PK":
                z = zipfile.ZipFile(io.BytesIO(b))
                for n in z.namelist():
                    print(" zip:", n, z.getinfo(n).file_size)
                first = z.read(z.namelist()[0]).decode("utf-8", "replace")
                print(first[:3000])
            else:
                t = b.decode("utf-8", "replace")
                if "h41.htm" in u or "Choose.aspx" in u:
                    import re
                    for kw in ["Reserve balances", "General Account", "Reverse repurchase", "Bills", "Total assets", "series=", "Package", "csv"]:
                        for m in list(re.finditer(re.escape(kw), t))[:3]:
                            print(f" [{kw}]", t[max(0, m.start()-200):m.start()+250].replace("\n", " "))
                else:
                    print(t[:1200])
    except Exception as e:
        body = ""
        if hasattr(e, "read"):
            try: body = e.read()[:400].decode("utf-8", "replace")
            except Exception: pass
        print("ERR", repr(e), body)
