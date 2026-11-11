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

START = date(2022, 1, 1)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "tga.json")
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


def get(url, tries=4, timeout=40):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 ** (i + 1))
    raise RuntimeError(f"GET 실패 {url}: {last}")


def fred(series_id):
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?" + urllib.parse.urlencode(
        {"id": series_id, "cosd": START.isoformat()}
    )
    rows = list(csv.reader(io.StringIO(get(url))))
    out = {}
    for row in rows[1:]:
        if len(row) < 2 or row[1] in ("", "."):
            continue
        try:
            out[row[0]] = float(row[1])
        except ValueError:
            continue
    if not out:
        raise RuntimeError(f"FRED {series_id}: 데이터 없음")
    return out


def to_billions(series):
    """FRED 단위가 백만 달러면 십억 달러로 바꾼다 (최댓값 크기로 판별)."""
    if max(series.values()) > 50_000:
        return {k: v / 1000.0 for k, v in series.items()}
    return series


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
    rows = fiscal("/v1/accounting/od/upcoming_auctions", {"sort": "issue_date", "page[size]": "100"})
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


def r1(x):
    return None if x is None else round(x, 1)


def main():
    raw = {}
    for key, sid in FRED.items():
        s = fred(sid)
        raw[key] = s if key in RATE_SERIES else to_billions(s)
        print(f"FRED {sid}: {len(s)}개, 최신 {max(s)}", file=sys.stderr)

    btc_src, btc = btc_daily()
    raw["btc"] = btc
    keys = {k: sorted(v) for k, v in raw.items()}

    weeks_dates = [d for d in keys["tga"] if d >= START.isoformat()]
    weeks = []
    for d in weeks_dates:
        start = (date.fromisoformat(d) - timedelta(days=6)).isoformat()
        w = {
            "date": d,
            "tga": raw["tga"][d],
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
        "thresholds": TH,
        "debt_ceiling": DEBT_CEILING,
        "weeks": weeks,
        "rrp_daily": rrp_daily,
        "btc_daily": btc_daily_recent,
        **extras,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    last = weeks[-1]
    print(f"완료: {len(weeks)}주, 최신 {last['date']} 시나리오 {last['scenario']}", file=sys.stderr)


if __name__ == "__main__":
    main()
