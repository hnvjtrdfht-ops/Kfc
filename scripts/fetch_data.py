#!/usr/bin/env python3
"""TGA 주간 트래킹 데이터 수집기.

읽는 순서 (논리 구조):
  1) 역레포(ON RRP)가 비어 있는가?        -> 완충재
  2) 연준이 단기채(T-bill)를 사고 있는가? -> 상쇄 장치
  3) TGA가 올랐는가, 내렸는가?            -> 마지막에 본다
그 결과가 지급준비금(reserves)과 단기금리(SOFR-IORB)에 어떻게 나타나는지,
그리고 비트코인(BTC/USDT) 가격과 어떻게 함께 움직이는지 주간 단위로 묶는다.

표준 라이브러리만 사용한다 (GitHub Actions에서 그대로 실행).
출력: data/tga.json
"""
import csv
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

START = date(2017, 1, 1)  # BTC/USDT(바이낸스)는 2017-08-17부터
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "tga.json")
PRED_LOG = os.path.join(os.path.dirname(OUT), "predictions.json")
MIN_SAMPLE = 8  # 같은 판정 표본이 이보다 적으면 전체 기준
UA = {"User-Agent": "tga-weekly-tracker/1.0"}

# FRED 시리즈 (API 키 불필요한 fredgraph.csv 사용)
FRED = {
    "tga": "WTREGEN",       # TGA, 수요일 잔액
    "rrp": "RRPONTSYD",     # 익일물 역레포, 일간
    "reserves": "WRESBAL",  # 지급준비금, 주간 평균
    "bills": "WSHOBL",      # 연준 보유 T-bill, 수요일 잔액
    "walcl": "WALCL",       # 연준 총자산, 수요일 잔액
    "sofr": "SOFR",         # SOFR, 일간 (%)
    "iorb": "IORB",         # 지준부리 금리, 일간 (%)
}
# 단위 정규화를 하지 않을 금리 시리즈
RATE_SERIES = {"sofr", "iorb"}

# 부채한도로 발행이 막혔던 구간 (TGA 감소 = 나중에 되돌려야 하는 돈)
DEBT_CEILING = [
    ("2017-03-16", "2017-09-08"),
    ("2019-03-02", "2019-08-02"),
    ("2021-08-01", "2021-12-16"),
    ("2023-01-19", "2023-06-03"),
    ("2025-01-21", "2025-07-04"),
]

# 분기 법인세/개인 추정세 납부일 (월, 일) — 국채 결제일과 겹치면 레포 스파이크 위험
TAX_DATES = [(1, 15), (3, 15), (4, 15), (6, 15), (9, 15), (12, 15)]

# 판정 임계값 (단위: 십억 달러)
TH = {
    "tga_move": 25.0,        # 주간 TGA 변화가 이보다 크면 '증가/감소'
    "rrp_ample": 500.0,      # 이상이면 역레포 '넉넉'
    "rrp_floor": 100.0,      # 미만이면 역레포 '바닥'
    "fed_buy_4w": 20.0,      # 연준 T-bill 4주 순증이 이 이상이면 '매입 중'
    "sofr_stress_bp": 0.0,   # 주중 SOFR 최고치 - IORB 가 이 bp 초과면 스트레스
}


BROWSER_UA = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/csv,application/json,*/*",
}


def get(url, tries=4, timeout=40, headers=None):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers or UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8")
        except Exception as e:  # noqa: BLE001
            last = e
            if i < tries - 1:
                time.sleep(2 ** (i + 1))
    raise RuntimeError(f"GET 실패 {url}: {last}")


def _fred_api(series_id, key):
    url = "https://api.stlouisfed.org/fred/series/observations?" + urllib.parse.urlencode(
        {"series_id": series_id, "api_key": key, "file_type": "json", "observation_start": START.isoformat()}
    )
    out = {}
    for o in json.loads(get(url, tries=3, timeout=40)).get("observations", []):
        try:
            out[o["date"]] = float(o["value"])
        except (KeyError, ValueError):
            continue
    return out


def _fred_csv(series_id):
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?" + urllib.parse.urlencode(
        {"id": series_id, "cosd": START.isoformat()}
    )
    rows = list(csv.reader(io.StringIO(get(url, tries=2, timeout=30, headers=BROWSER_UA))))
    out = {}
    for row in rows[1:]:
        if len(row) < 2 or row[1] in ("", "."):
            continue
        try:
            out[row[0]] = float(row[1])
        except ValueError:
            continue
    return out


_FRED_DOWN = False


def fred(series_id):
    """FRED_API_KEY 가 있으면 공식 API, 없으면 fredgraph.csv. 실패하면 빈 dict."""
    global _FRED_DOWN
    key = os.environ.get("FRED_API_KEY", "").strip()
    try:
        if key:
            return _fred_api(series_id, key)
        if _FRED_DOWN:
            return {}
        return _fred_csv(series_id)
    except Exception as e:  # noqa: BLE001
        print(f"FRED {series_id} 실패: {e}", file=sys.stderr)
        if not key:
            _FRED_DOWN = True  # 같은 원인으로 나머지도 막힐 테니 시간 낭비하지 않음
        return {}


def to_billions(series):
    """FRED 단위가 백만 달러면 십억 달러로 바꾼다 (최댓값 크기로 판별)."""
    if series and max(series.values()) > 50_000:
        return {k: v / 1000.0 for k, v in series.items()}
    return series


# ---------- FRED 대체 소스 (키 없이 쓰는 공공 API) ----------
NYFED = "https://markets.newyorkfed.org/api"


def _chunks(days=360):
    s = START
    end = date.today()
    while s <= end:
        e = min(end, s + timedelta(days=days))
        yield s.isoformat(), e.isoformat()
        s = e + timedelta(days=1)


def nyfed_rrp():
    """ON RRP 일간 낙찰액 (십억 달러)."""
    out = {}
    for a, b in _chunks():
        j = json.loads(get(f"{NYFED}/rp/reverserepo/propositions/search.json?startDate={a}&endDate={b}"))
        for op in (j.get("repo") or {}).get("operations", []):
            d = op.get("operationDate")
            v = op.get("totalAmtAccepted")
            if d and v is not None:
                out[d] = out.get(d, 0.0) + float(v) / 1e9
    return out


def nyfed_rates(kind):
    """kind: 'secured/sofr' 또는 'unsecured/effr'. effr 는 목표범위 상단도 함께."""
    rate, top = {}, {}
    for a, b in _chunks():
        j = json.loads(get(f"{NYFED}/rates/{kind}/search.json?startDate={a}&endDate={b}"))
        for r in j.get("refRates", []):
            d = r.get("effectiveDate")
            if not d:
                continue
            if r.get("percentRate") is not None:
                rate[d] = float(r["percentRate"])
            if r.get("targetRateTo") is not None:
                top[d] = float(r["targetRateTo"])
    return rate, top


def dts_tga():
    """재무부 DTS 일간 TGA 마감잔액 (십억 달러)."""
    out = {}
    page = 1
    while True:
        rows = fiscal(
            "/v1/accounting/dts/operating_cash_balance",
            {"filter": f"record_date:gte:{START.isoformat()}", "sort": "record_date",
             "page[size]": "10000", "page[number]": str(page)},
        )
        for r in rows:
            acct = (r.get("account_type") or "").lower()
            if "closing balance" in acct and ("tga" in acct or "general account" in acct):
                for f in ("open_today_bal", "close_today_bal"):
                    try:
                        out[r["record_date"]] = float(r.get(f)) / 1000.0
                        break
                    except (TypeError, ValueError):
                        continue
        if len(rows) < 10000:
            break
        page += 1
    return out


# 연준 H.4.1 전체 릴리스 (FRED 원본과 같은 수치). 단위: 백만 달러
H41_URL = "https://www.federalreserve.gov/datadownload/Output.aspx?rel=H41&filetype=zip"
H41_SERIES = {
    "tga": "RESPPLLDT_N.WW",       # U.S. Treasury, General Account: Wednesday level
    "reserves": "RESPPLLDE_N.WW",  # Deposits held by depository institutions (지급준비금): Wednesday level
    "bills": "RESPPALGUOB_N.WW",   # Treasury securities held outright: Bills: Wednesday level
    "walcl": "RESPPA_N.WW",        # Total assets: Wednesday level
    "rrp": "RESPPLLRD_N.WW",       # Reverse repurchase agreements: Others (ON RRP): Wednesday level
}
_H41_CACHE = None


def h41_all():
    global _H41_CACHE
    if _H41_CACHE is not None:
        return _H41_CACHE
    import xml.etree.ElementTree as ET
    import zipfile
    try:
        req = urllib.request.Request(H41_URL, headers=BROWSER_UA)
        with urllib.request.urlopen(req, timeout=120) as r:
            z = zipfile.ZipFile(io.BytesIO(r.read()))
    except Exception as e:  # noqa: BLE001
        print(f"H.4.1 다운로드 실패: {e}", file=sys.stderr)
        _H41_CACHE = {k: {} for k in H41_SERIES}
        return _H41_CACHE
    want = {v: k for k, v in H41_SERIES.items()}
    out = {k: {} for k in H41_SERIES}
    start = START.isoformat()
    with z.open("H41_data.xml") as f:
        for _, el in ET.iterparse(f, events=("end",)):
            if el.tag.split("}")[-1] != "Series":
                continue
            key = want.get(el.attrib.get("SERIES_NAME"))
            if key:
                mult = float(el.attrib.get("UNIT_MULT", "1000000"))
                for o in el:
                    if o.tag.split("}")[-1] != "Obs":
                        continue
                    d = o.attrib.get("TIME_PERIOD", "")
                    try:
                        v = float(o.attrib.get("OBS_VALUE"))
                    except (TypeError, ValueError):
                        continue
                    if d >= start:
                        out[key][d] = v * mult / 1e9  # 십억 달러
            el.clear()
    _H41_CACHE = out
    print("H.4.1: " + ", ".join(f"{k} {len(v)}" for k, v in out.items()), file=sys.stderr)
    return out


def with_fallback(name, primary, fallback):
    if primary:
        return primary
    try:
        data = fallback()
        print(f"{name}: 대체 소스 사용 ({len(data)}개)", file=sys.stderr)
        return data
    except Exception as e:  # noqa: BLE001
        print(f"{name}: 대체 소스도 실패 {e}", file=sys.stderr)
        return {}


# ---------- BTC/USDT 일봉 ----------
def btc_binance(host):
    out = {}
    start_ms = int(datetime(START.year, START.month, START.day, tzinfo=timezone.utc).timestamp() * 1000)
    while True:
        url = f"https://{host}/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=1000&startTime={start_ms}"
        rows = json.loads(get(url))
        if not rows:
            break
        for k in rows:
            d = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).date().isoformat()
            out[d] = float(k[4])
        start_ms = rows[-1][0] + 86_400_000
        if len(rows) < 1000:
            break
    return out


def btc_okx():
    out = {}
    after = ""
    for _ in range(20):
        url = "https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT&bar=1Dutc&limit=100"
        if after:
            url += f"&after={after}"
        rows = json.loads(get(url)).get("data", [])
        if not rows:
            break
        for k in rows:
            d = datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc).date()
            out[d.isoformat()] = float(k[4])
        after = rows[-1][0]
        if datetime.fromtimestamp(int(after) / 1000, tz=timezone.utc).date() < START:
            break
    return out


def btc_kucoin():
    out = {}
    end = int(time.time())
    begin = int(datetime(START.year, START.month, START.day, tzinfo=timezone.utc).timestamp())
    while end > begin:
        start = max(begin, end - 1400 * 86400)
        url = f"https://api.kucoin.com/api/v1/market/candles?type=1day&symbol=BTC-USDT&startAt={start}&endAt={end}"
        rows = json.loads(get(url)).get("data", [])
        if not rows:
            break
        for k in rows:
            d = datetime.fromtimestamp(int(k[0]), tz=timezone.utc).date().isoformat()
            out[d] = float(k[2])  # [time, open, close, high, low, ...]
        end = start - 1
    return out


def btc_daily():
    sources = [
        ("Binance (data-api.binance.vision)", lambda: btc_binance("data-api.binance.vision")),
        ("Binance (api.binance.com)", lambda: btc_binance("api.binance.com")),
        ("OKX", btc_okx),
        ("KuCoin", btc_kucoin),
    ]
    for name, fn in sources:
        try:
            data = fn()
            if len(data) > 300:
                print(f"BTC: {name} {len(data)}일", file=sys.stderr)
                return name, data
        except Exception as e:  # noqa: BLE001
            print(f"BTC 소스 실패 {name}: {e}", file=sys.stderr)
    raise RuntimeError("BTC/USDT 가격을 어떤 소스에서도 받지 못했습니다")


# ---------- 재무부 Fiscal Data (선택) ----------
FISCAL = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"


def fiscal(path, params):
    url = f"{FISCAL}{path}?" + urllib.parse.urlencode(params, safe="[],:")
    return json.loads(get(url)).get("data", [])


def tga_daily_latest():
    rows = fiscal(
        "/v1/accounting/dts/operating_cash_balance",
        {"sort": "-record_date", "page[size]": "40"},
    )
    out = []
    for r in rows:
        acct = (r.get("account_type") or "").lower()
        if "closing balance" in acct and ("tga" in acct or "general account" in acct):
            val = r.get("open_today_bal") or r.get("close_today_bal")
            try:
                v = float(val)
            except (TypeError, ValueError):
                continue
            out.append({"date": r["record_date"], "tga": round(v / 1000.0, 1)})
    out.sort(key=lambda x: x["date"])
    return out[-10:]


def upcoming_auctions():
    rows = fiscal(
        "/v1/accounting/od/upcoming_auctions",
        {"filter": f"issue_date:gte:{date.today().isoformat()}", "sort": "issue_date", "page[size]": "100"},
    )
    out = []
    for r in rows:
        amt = r.get("offering_amt")
        try:
            amt_b = float(amt) / 1e9 if float(amt) > 1e6 else float(amt) / 1e3
        except (TypeError, ValueError):
            amt_b = None
        out.append({
            "type": r.get("security_type"),
            "term": r.get("security_term"),
            "auction_date": r.get("auction_date"),
            "settle_date": r.get("issue_date"),
            "amount_b": round(amt_b, 1) if amt_b is not None else None,
        })
    return out


# ---------- 주간 정렬 & 판정 ----------
def asof(series_sorted, keys_sorted, d):
    """d 이하 가장 최근 값."""
    import bisect
    i = bisect.bisect_right(keys_sorted, d) - 1
    if i < 0:
        return None
    return series_sorted[keys_sorted[i]]


def window_max(series, keys_sorted, start, end):
    import bisect
    lo = bisect.bisect_left(keys_sorted, start)
    hi = bisect.bisect_right(keys_sorted, end)
    vals = [series[k] for k in keys_sorted[lo:hi]]
    return max(vals) if vals else None


def in_debt_ceiling(d):
    return any(a <= d <= b for a, b in DEBT_CEILING)


def tax_in_week(d):
    end = date.fromisoformat(d)
    start = end - timedelta(days=6)
    hits = []
    for y in {start.year, end.year}:
        for m, dd in TAX_DATES:
            t = date(y, m, dd)
            if start <= t <= end + timedelta(days=2):  # 주말 납부일은 다음 영업일로 밀림
                hits.append(t.isoformat())
    return hits


def rrp_state(v):
    if v is None:
        return None
    if v >= TH["rrp_ample"]:
        return "ample"
    if v < TH["rrp_floor"]:
        return "floor"
    return "thin"


def classify(w):
    """주간 레코드 -> 시나리오 코드.
    1: TGA↑ + 역레포 넉넉   -> 역레포가 먼저 줄고 지준은 그대로
    2: TGA↑ + 역레포 바닥   -> 지준이 그대로 줄고 단기금리 상승
    3: TGA↑ + 납부일/결제 겹침 스트레스 -> 레포금리 급등 위험
    4: TGA↓ + 부채한도      -> 지준 증가, 한도 풀리면 되돌림
    5: TGA↑ + 연준 T-bill 매입 -> 연준이 빠진 지준을 채움
    D: TGA↓ (일반)          -> 지준으로 돈이 풀림
    N: TGA 보합
    """
    d = w.get("d_tga")
    if d is None:
        return "N"
    if d <= -TH["tga_move"]:
        return "4" if w["debt_ceiling"] else "D"
    if d < TH["tga_move"]:
        return "N"
    if w["stress"] and w["rrp_state"] != "ample":
        return "3"
    if w["fed_buying"]:
        return "5"
    return "1" if w["rrp_state"] == "ample" else "2"


# ---------- BTC 7일 방향 확률 (워크포워드) ----------
MODELS = {
    "base": "전체 평균",
    "scenario": "판정 조건",
    "liq": "순유동성 4주 방향",
    "mom": "BTC 4주 추세",
    "scen_liq": "판정 × 순유동성",
}
SHRINK = 10      # 조건부 표본을 전체 평균 쪽으로 당기는 강도 (가짜 표본 수)
SELECT_WIN = 52  # 최근 몇 주 성적으로 모델을 고를지
SELECT_MIN = 26  # 이만큼 채점된 뒤부터 모델 선택


def _features(weeks, i):
    w = weeks[i]
    p4 = weeks[i - 4] if i >= 4 else None
    liq = mom = None
    if p4 and w.get("net_liq") is not None and p4.get("net_liq") is not None:
        liq = "up" if w["net_liq"] > p4["net_liq"] else "down"
    if p4 and w.get("btc") and p4.get("btc"):
        mom = "up" if w["btc"] > p4["btc"] else "down"
    return {"base": "all", "scenario": w["scenario"], "liq": liq, "mom": mom,
            "scen_liq": None if liq is None else f"{w['scenario']}|{liq}"}


def _brier(rows):
    return sum((p / 100.0 - o) ** 2 for p, o in rows) / len(rows) if rows else None


def _hit(rows):
    d = [(p, o) for p, o in rows if p != 50]
    return 100.0 * sum(1 for p, o in d if (p > 50) == (o == 1)) / len(d) if d else None


def predict_walk_forward(weeks):
    """각 주에 대해, 그 주까지 결과가 확정된 과거만으로 다음 7일 BTC 상승 확률을 모델별로 계산.
    맨 위에 쓸 확률은 '직전까지 채점된' 최근 성적(Brier)이 가장 좋은 모델로 고른다."""
    feats = [_features(weeks, i) for i in range(len(weeks))]
    for i, w in enumerate(weeks):
        hist = [j for j in range(1, i) if weeks[j + 1]["btc_ret"] is not None]
        w["preds"] = {}
        w["pred_n"] = {}
        if len(hist) >= MIN_SAMPLE:
            ups = {j: 1 if weeks[j + 1]["btc_ret"] > 0 else 0 for j in hist}
            base = sum(ups.values()) / len(hist)
            for m in MODELS:
                key = feats[i][m]
                if key is None:
                    continue
                match = [ups[j] for j in hist if feats[j][m] == key]
                p = (sum(match) + SHRINK * base) / (len(match) + SHRINK) if m != "base" else base
                w["preds"][m] = round(100 * p, 1)
                w["pred_n"][m] = len(match)
        w["next_ret"] = weeks[i + 1]["btc_ret"] if i + 1 < len(weeks) else None

    for i, w in enumerate(weeks):
        scored = [k for k in range(i) if weeks[k]["next_ret"] is not None and k + 1 <= i and weeks[k]["preds"]][-SELECT_WIN:]
        chosen = "scenario"
        if len(scored) >= SELECT_MIN:
            best = None
            for m in MODELS:
                rows = [(weeks[k]["preds"][m], 1 if weeks[k]["next_ret"] > 0 else 0) for k in scored if m in weeks[k]["preds"]]
                if len(rows) < SELECT_MIN:
                    continue
                b = _brier(rows)
                if best is None or b < best[0]:
                    best = (b, m)
            if best:
                chosen = best[1]
        if chosen not in w["preds"]:
            chosen = "base" if "base" in w["preds"] else None
        w["p_model"] = chosen
        w["p_up"] = w["preds"].get(chosen) if chosen else None
        w["p_n"] = w["pred_n"].get(chosen) if chosen else None


def score_models(weeks):
    out = {}
    done = [w for w in weeks if w["next_ret"] is not None and w["preds"]]
    for m in list(MODELS) + ["selected"]:
        def rows(ws):
            r = []
            for w in ws:
                p = w["p_up"] if m == "selected" else w["preds"].get(m)
                if p is not None:
                    r.append((p, 1 if w["next_ret"] > 0 else 0))
            return r
        r_all, r52 = rows(done), rows(done[-52:])
        out[m] = {
            "label": MODELS.get(m, "자동 선택"),
            "brier_52": None if not r52 else round(_brier(r52), 4),
            "hit_52": None if not r52 else round(_hit(r52), 1),
            "n_52": len(r52),
            "brier_all": None if not r_all else round(_brier(r_all), 4),
            "hit_all": None if not r_all else round(_hit(r_all), 1),
            "n_all": len(r_all),
        }
    return out


def r1(x):
    return None if x is None else round(x, 1)


def main():
    raw = {}
    for key, sid in FRED.items():
        s = fred(sid)
        raw[key] = s if key in RATE_SERIES else to_billions(s)
        print(f"FRED {sid}: {len(s)}개" + (f", 최신 {max(s)}" if s else ""), file=sys.stderr)

    # FRED가 막혔을 때 공공 API로 대체
    for k in ("tga", "reserves", "bills", "walcl"):
        raw[k] = with_fallback(f"H.4.1 {k}", raw[k], lambda k=k: h41_all()[k])
    raw["tga"] = with_fallback("TGA(DTS)", raw["tga"], dts_tga)
    raw["rrp"] = with_fallback("역레포", raw["rrp"], nyfed_rrp)
    raw["rrp"] = with_fallback("역레포(H.4.1)", raw["rrp"], lambda: h41_all()["rrp"])
    raw["sofr"] = with_fallback("SOFR", raw["sofr"], lambda: nyfed_rates("secured/sofr")[0])
    # IORB = 연방기금 목표범위 상단 − 10bp (2021년 7월 이후 동일한 설정)
    raw["iorb"] = with_fallback(
        "IORB", raw["iorb"],
        # IORB는 2021-07-29 도입. 그 전(IOER 시절)은 목표범위와의 차이가 달라 비워 둔다
        lambda: {k: round(v - 0.10, 3) for k, v in nyfed_rates("unsecured/effr")[1].items() if k >= "2021-07-29"},
    )
    if not raw["tga"]:
        raise RuntimeError("TGA 데이터를 어떤 소스에서도 받지 못했습니다")
    missing = [k for k in FRED if not raw[k]]
    if missing:
        print(f"경고: 비어 있는 시리즈 {missing} — FRED_API_KEY 시크릿을 추가하면 해결됩니다", file=sys.stderr)

    btc_src, btc = btc_daily()
    raw["btc"] = btc
    keys = {k: sorted(v) for k, v in raw.items()}

    # 주간 기준일 = 수요일 (H.4.1). TGA가 있는 마지막 날까지.
    last_tga = date.fromisoformat(keys["tga"][-1])
    d0 = START + timedelta(days=(2 - START.weekday()) % 7)
    weeks_dates = []
    while d0 <= last_tga:
        weeks_dates.append(d0.isoformat())
        d0 += timedelta(days=7)
    weeks = []
    for d in weeks_dates:
        start = (date.fromisoformat(d) - timedelta(days=6)).isoformat()
        w = {
            "date": d,
            "tga": asof(raw["tga"], keys["tga"], d),
            "rrp": asof(raw["rrp"], keys["rrp"], d),
            "reserves": asof(raw["reserves"], keys["reserves"], d),
            "bills": asof(raw["bills"], keys["bills"], d),
            "walcl": asof(raw["walcl"], keys["walcl"], d),
            "sofr": asof(raw["sofr"], keys["sofr"], d),
            "sofr_max": window_max(raw["sofr"], keys["sofr"], start, d),
            "iorb": asof(raw["iorb"], keys["iorb"], d),
            "btc": asof(raw["btc"], keys["btc"], d),
        }
        weeks.append(w)

    for i, w in enumerate(weeks):
        p = weeks[i - 1] if i >= 1 else None
        p4 = weeks[i - 4] if i >= 4 else None

        def diff(a, b, k):
            if a is None or b is None or a.get(k) is None or b.get(k) is None:
                return None
            return a[k] - b[k]

        w["d_tga"] = diff(w, p, "tga")
        w["d_rrp"] = diff(w, p, "rrp")
        w["d_reserves"] = diff(w, p, "reserves")
        w["d_bills"] = diff(w, p, "bills")
        w["d_bills_4w"] = diff(w, p4, "bills")
        w["d_tga_4w"] = diff(w, p4, "tga")
        nl = None
        if None not in (w["walcl"], w["tga"], w["rrp"]):
            nl = w["walcl"] - w["tga"] - w["rrp"]
        w["net_liq"] = nl
        w["d_net_liq"] = None if p is None or p.get("net_liq") is None or nl is None else nl - p["net_liq"]
        w["spread_bp"] = None
        if w["sofr_max"] is not None and w["iorb"] is not None:
            w["spread_bp"] = round((w["sofr_max"] - w["iorb"]) * 100, 1)
        w["tax_dates"] = tax_in_week(w["date"])
        w["rrp_state"] = rrp_state(w["rrp"])
        w["fed_buying"] = w["d_bills_4w"] is not None and w["d_bills_4w"] >= TH["fed_buy_4w"]
        w["debt_ceiling"] = in_debt_ceiling(w["date"])
        w["stress"] = bool(w["tax_dates"]) or (
            w["spread_bp"] is not None and w["spread_bp"] > TH["sofr_stress_bp"]
        )
        w["btc_ret"] = None
        if p and p.get("btc") and w.get("btc"):
            w["btc_ret"] = round((w["btc"] / p["btc"] - 1) * 100, 2)
        w["scenario"] = classify(w)

    predict_walk_forward(weeks)
    model_scores = score_models(weeks)

    for w in weeks:
        for k in list(w):
            if isinstance(w[k], float) and k not in ("sofr", "sofr_max", "iorb", "btc_ret", "spread_bp"):
                w[k] = r1(w[k])
            elif k in ("sofr", "sofr_max", "iorb") and w[k] is not None:
                w[k] = round(w[k], 3)
            elif k == "btc" and w[k] is not None:
                w[k] = round(w[k], 2)

    extras = {}
    try:
        extras["tga_daily"] = tga_daily_latest()
    except Exception as e:  # noqa: BLE001
        print(f"DTS 실패: {e}", file=sys.stderr)
        extras["tga_daily"] = []
    try:
        today = date.today().isoformat()
        extras["auctions"] = [a for a in upcoming_auctions() if (a["settle_date"] or "") >= today]
    except Exception as e:  # noqa: BLE001
        print(f"Upcoming auctions 실패: {e}", file=sys.stderr)
        extras["auctions"] = []

    rrp_daily = [{"date": k, "rrp": round(raw["rrp"][k], 1)} for k in keys["rrp"][-15:]]
    btc_daily_recent = [{"date": k, "btc": round(raw["btc"][k], 2)} for k in keys["btc"][-15:]]

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "units": "십억 달러 (금리는 %)",
        "btc_source": btc_src + " BTC/USDT 일봉 종가",
        "missing_series": missing,
        "thresholds": TH,
        "debt_ceiling": DEBT_CEILING,
        "weeks": weeks,
        "models": MODELS,
        "model_scores": model_scores,
        "rrp_daily": rrp_daily,
        "btc_daily": btc_daily_recent,
        **extras,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    # 실시간 예측 기록: 주마다 처음 계산된 확률을 고정해 남긴다 (나중에 덮어쓰지 않음)
    log = []
    if os.path.exists(PRED_LOG):
        with open(PRED_LOG, encoding="utf-8") as f:
            log = json.load(f)
    last = weeks[-1]
    if last.get("p_up") is not None and not any(e["week"] == last["date"] for e in log):
        log.append({
            "week": last["date"], "scenario": last["scenario"], "p_up": last["p_up"],
            "model": last["p_model"], "n": last["p_n"], "preds": last["preds"], "btc_at": last["btc"],
            "logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        with open(PRED_LOG, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=1)
    out["pred_log"] = log
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    last = weeks[-1]
    print(f"완료: {len(weeks)}주, 최신 {last['date']} 시나리오 {last['scenario']}", file=sys.stderr)


if __name__ == "__main__":
    main()
