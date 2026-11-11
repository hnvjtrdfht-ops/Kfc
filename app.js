/* TGA 주간 트래커 — 역레포 → 연준 매입 → TGA 순서로 읽기, BTC/USDT 연결 */
(function () {
  "use strict";

  const SCEN = {
    "1": { k: "1", title: "TGA 증가", cond: "역레포 잔고가 넉넉할 때", tone: "--good",
      effect: "역레포가 먼저 줄어듭니다. 지급준비금은 거의 그대로입니다.",
      btc: "중립 — 충격을 역레포가 흡수", ex: "2023년 6~8월", exd: "TGA +3,840억 달러 · 역레포 −4,550억 · 지준 −860억" },
    "2": { k: "2", title: "TGA 증가", cond: "역레포가 바닥일 때", tone: "--crit",
      effect: "지급준비금이 그대로 줄어듭니다. 단기금리가 위로 밀립니다.",
      btc: "역풍 — 시중 달러 유동성이 직접 빠짐", ex: "2025년 8~10월", exd: "지준 약 3.3조 → 2.85조 달러 · 10/31 스탠딩 레포 500억" },
    "3": { k: "3", title: "TGA 증가", cond: "세금 납부일과 국채 결제일이 겹칠 때", tone: "--crit",
      effect: "하루 이틀 사이에 은행 돈이 빠집니다. 레포금리가 뛸 수 있습니다.",
      btc: "단기 변동성 주의 — 레버리지 청산 위험", ex: "2019년 9월", exd: "9/17 SOFR 5.25% (직전 2%대 초반)" },
    "4": { k: "4", title: "TGA 감소", cond: "부채한도로 발행이 막혔을 때", tone: "--warn",
      effect: "지급준비금이 늘어납니다. 한도가 풀리면 되돌려야 하는 돈입니다.",
      btc: "일시적 순풍 — 한도 해제 후 재흡수 대비", ex: "2023년 4~5월", exd: "TGA −2,480억 달러 · 지준 +1,730억" },
    "5": { k: "5", title: "TGA 증가", cond: "연준이 단기채를 사들일 때", tone: "--good",
      effect: "빠지는 지준을 연준이 채웁니다. 충격이 상쇄됩니다.",
      btc: "중립~순풍 — 연준이 바닥을 받쳐줌", ex: "2025년 12월 ~ 현재", exd: "월 약 400억 달러로 시작 · 연준 단기채 +3,776억(1년)" },
    "D": { k: "↓", title: "TGA 감소", cond: "일반적인 재무부 현금 지출", tone: "--good",
      effect: "재무부가 쌓아둔 현금을 풀어 지급준비금이 늘어납니다.",
      btc: "순풍 — 시중 달러가 늘어남" },
    "N": { k: "=", title: "TGA 보합", cond: "주간 변화가 작을 때", tone: "--neutral",
      effect: "이번 주 TGA 충격은 크지 않습니다. 역레포 잔고와 연준 매입 흐름이 더 중요합니다.",
      btc: "중립 — 다른 변수 우선" },
  };
  const ORDER = ["1", "2", "3", "4", "5", "D", "N"];

  const $ = (s, r = document) => r.querySelector(s);
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // 십억 달러 -> "3,840억 달러" / "2.85조 달러"
  function fmtB(v, signed) {
    if (v == null || isNaN(v)) return "—";
    const s = signed ? (v > 0 ? "+" : v < 0 ? "−" : "±") : v < 0 ? "−" : "";
    const a = Math.abs(v);
    if (a >= 1000) return s + (a / 1000).toFixed(2) + "조 달러";
    return s + Math.round(a * 10).toLocaleString("ko-KR") + "억 달러";
  }
  const fmtShort = (v, signed) => fmtB(v, signed).replace(" 달러", "");
  const fmtUsd = (v) => (v == null ? "—" : "$" + v.toLocaleString("en-US", { maximumFractionDigits: 0 }));
  const fmtPct = (v, d = 1) => (v == null || isNaN(v) ? "—" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(d) + "%");
  const cls = (v) => (v == null ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "");
  const md = (d) => { const [, m, dd] = d.split("-"); return `${+m}/${+dd}`; };
  const ymd = (d) => d.replace(/-/g, ".");

  function chip(label, toneVar) {
    const icon = { "--good": "●", "--warn": "▲", "--crit": "■", "--serious": "▲", "--neutral": "○" }[toneVar] || "●";
    return `<span class="chip" style="--c:var(${toneVar})"><span aria-hidden="true" style="color:var(${toneVar})">${icon}</span>${esc(label)}</span>`;
  }

  function pearson(xs, ys) {
    const n = xs.length; if (n < 8) return null;
    const mx = xs.reduce((a, b) => a + b, 0) / n, my = ys.reduce((a, b) => a + b, 0) / n;
    let sxy = 0, sx = 0, sy = 0;
    for (let i = 0; i < n; i++) { const dx = xs[i] - mx, dy = ys[i] - my; sxy += dx * dy; sx += dx * dx; sy += dy * dy; }
    return sx && sy ? sxy / Math.sqrt(sx * sy) : null;
  }

  // ---------- Live BTC ----------
  async function liveBtc() {
    const srcs = [
      async () => { const r = await (await fetch("https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT")).json(); return { px: +r.lastPrice, chg: +r.priceChangePercent }; },
      async () => { const r = await (await fetch("https://data-api.binance.vision/api/v3/ticker/24hr?symbol=BTCUSDT")).json(); return { px: +r.lastPrice, chg: +r.priceChangePercent }; },
      async () => { const r = (await (await fetch("https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT")).json()).data[0]; return { px: +r.last, chg: (r.last / r.open24h - 1) * 100 }; },
    ];
    for (const f of srcs) {
      try { const v = await f(); if (v.px > 0) return v; } catch (e) { /* 다음 소스 */ }
    }
    return null;
  }
  async function tickBtc() {
    const v = await liveBtc();
    if (!v) { $("#btcPx").textContent = "연결 안 됨"; return; }
    $("#btcPx").textContent = fmtUsd(v.px);
    const c = $("#btcChg");
    c.textContent = "24h " + fmtPct(v.chg, 2);
    c.className = "chg num " + (v.chg >= 0 ? "up" : "down");
  }

  // ---------- Theme ----------
  function initTheme() {
    let saved = null;
    try { saved = localStorage.getItem("tga-theme"); } catch (e) {}
    if (saved) document.documentElement.setAttribute("data-theme", saved);
    $("#themeBtn").addEventListener("click", () => {
      const isDark = css("color-scheme") === "dark" || document.documentElement.getAttribute("data-theme") === "dark";
      const next = isDark ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try { localStorage.setItem("tga-theme", next); } catch (e) {}
      if (window.__data) renderCharts(window.__data);
    });
  }

  // ---------- Render ----------
  function render(data) {
    const W = data.weeks.filter((w) => w.tga != null);
    const cur = W[W.length - 1];
    const sc = SCEN[cur.scenario] || SCEN.N;
    const T = data.thresholds;
    $("#genAt").textContent = "데이터 생성: " + new Date(data.generated_at).toLocaleString("ko-KR") + " · 다음 H.4.1 발표: 매주 목 16:30 ET (한국 금 05:30~06:30)";
    if (data.btc_source) $("#btcSrc").textContent = data.btc_source;

    const rrpLab = { ample: ["넉넉", "--good"], thin: ["얇아짐", "--warn"], floor: ["바닥", "--crit"] }[cur.rrp_state] || ["—", "--neutral"];
    const b4 = cur.d_bills_4w;
    const fedLab = cur.fed_buying ? ["매입 중", "--good"]
      : b4 == null ? ["—", "--neutral"]
      : b4 > 0 ? ["소폭 매입 (기준 미달)", "--warn"]
      : b4 < -T.fed_buy_4w ? ["축소 중", "--crit"] : ["매입 없음", "--neutral"];
    const tgaLab = cur.d_tga >= T.tga_move ? ["증가", "--crit"] : cur.d_tga <= -T.tga_move ? ["감소", "--good"] : ["보합", "--neutral"];
    const spreadTone = cur.spread_bp == null ? "--neutral" : cur.spread_bp > 0 ? "--crit" : cur.spread_bp > -5 ? "--warn" : "--good";
    const lastRrpDaily = (data.rrp_daily || []).slice(-1)[0];
    const lastTgaDaily = (data.tga_daily || []).slice(-1)[0];
    const tgaSince = lastTgaDaily && lastTgaDaily.date > cur.date ? lastTgaDaily.tga - cur.tga : null;

    // 4주 BTC 수익률
    const w4 = W[W.length - 5];
    const btc4 = w4 && w4.btc && cur.btc ? (cur.btc / w4.btc - 1) * 100 : null;
    const nl4 = w4 && w4.net_liq != null && cur.net_liq != null ? cur.net_liq - w4.net_liq : null;

    // 판정 이력 연속
    let streak = 0;
    for (let i = W.length - 1; i >= 0 && W[i].scenario === cur.scenario; i--) streak++;

    const flags = [];
    if (cur.tax_dates && cur.tax_dates.length) flags.push(chip("세금 납부일 주간 " + cur.tax_dates.map(md).join(", "), "--warn"));
    if (cur.debt_ceiling) flags.push(chip("부채한도 구간", "--warn"));
    if (cur.spread_bp != null && cur.spread_bp > 0) flags.push(chip("SOFR가 IORB 위", "--crit"));

    const html = `
    <section style="margin-top:0">
      <div class="sec-head"><div><h2>이번 주 판정</h2><span class="muted">H.4.1 기준일 ${ymd(cur.date)} (수) · ${streak}주 연속</span></div></div>
      <div class="card verdict" style="--tone:var(${sc.tone})">
        <div class="big">${esc(sc.k)}</div>
        <div>
          <div class="title">${esc(sc.title)} <span style="font-weight:400;color:var(--text-2);font-size:15px">+ ${esc(sc.cond)}</span></div>
          <p class="effect">${esc(sc.effect)}</p>
          <div class="btcread"><b>BTC 해석</b><span>${esc(sc.btc)}</span></div>
          ${flags.length ? `<div style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap">${flags.join("")}</div>` : ""}
        </div>
      </div>
    </section>

    <section>
      <div class="sec-head"><div><h2>순서대로 읽기</h2><span class="muted">완충재가 있는 시기의 1,000억 달러와 없는 시기의 1,000억 달러는 무게가 다릅니다</span></div></div>
      <div class="grid g3">
        <div class="card step">
          <div class="n">STEP 1</div>
          <div class="q">역레포가 비어 있는가?</div>
          <div class="v num">${fmtB(cur.rrp)}</div>
          <div class="d num">전주 대비 <span class="${cls(cur.d_rrp)}">${fmtB(cur.d_rrp, true)}</span>${lastRrpDaily ? ` · 최신 일간 ${md(lastRrpDaily.date)} ${fmtB(lastRrpDaily.rrp)}` : ""}</div>
          <div style="margin-top:8px">${chip(rrpLab[0], rrpLab[1])}</div>
          <div class="rule">${fmtShort(T.rrp_ample)} 이상 넉넉 · ${fmtShort(T.rrp_floor)} 미만 바닥. 바닥이면 TGA 증가분이 곧장 지준에서 빠집니다.</div>
        </div>
        <div class="card step">
          <div class="n">STEP 2</div>
          <div class="q">연준이 단기채를 사고 있는가?</div>
          <div class="v num">${fmtB(cur.bills)}</div>
          <div class="d num">4주 순증 <span class="${cls(cur.d_bills_4w)}">${fmtB(cur.d_bills_4w, true)}</span> · 1주 <span class="${cls(cur.d_bills)}">${fmtB(cur.d_bills, true)}</span></div>
          <div style="margin-top:8px">${chip(fedLab[0], fedLab[1])}</div>
          <div class="rule">연준 보유 T-bill 4주 순증 ${fmtShort(T.fed_buy_4w)} 이상이면 매입 중. 빠지는 지준을 연준이 채웁니다.</div>
        </div>
        <div class="card step">
          <div class="n">STEP 3 · 마지막</div>
          <div class="q">TGA가 올랐나, 내렸나?</div>
          <div class="v num">${fmtB(cur.tga)}</div>
          <div class="d num">1주 <span class="${cls(-cur.d_tga)}">${fmtB(cur.d_tga, true)}</span> · 4주 <span class="${cls(-cur.d_tga_4w)}">${fmtB(cur.d_tga_4w, true)}</span></div>
          ${lastTgaDaily ? `<div class="d num">최신 일간 ${md(lastTgaDaily.date)} ${fmtB(lastTgaDaily.tga)}${tgaSince != null ? ` · 기준일 이후 <span class="${cls(-tgaSince)}">${fmtB(tgaSince, true)}</span>` : ""}</div>` : ""}
          <div style="margin-top:8px">${chip(tgaLab[0], tgaLab[1])}</div>
          <div class="rule">주간 ±${fmtShort(T.tga_move)} 이상을 증가·감소로 봅니다. TGA 증가 = 시중 달러 흡수.</div>
        </div>
      </div>
    </section>

    <section>
      <div class="sec-head"><div><h2>결과: 돈은 어디서 빠졌나</h2><span class="muted">이번 주 TGA 변화가 역레포·지준·연준 매입으로 어떻게 나뉘었는지</span></div></div>
      <div class="grid g2">
        <div class="card"><div id="decomp" class="decomp"></div>
          <p class="muted" style="margin:12px 0 0">TGA는 증가가 흡수(빨강), 나머지는 증가가 공급(파랑). 연준 대차대조표 다른 항목 때문에 합이 정확히 맞지는 않습니다.</p></div>
        <div class="grid g2">
          <div class="card kpi"><div class="lab">지급준비금</div><div class="v num">${fmtB(cur.reserves)}</div><div class="d num">1주 <span class="${cls(cur.d_reserves)}">${fmtB(cur.d_reserves, true)}</span></div></div>
          <div class="card kpi"><div class="lab">SOFR 주중 최고 − IORB</div><div class="v num">${cur.spread_bp == null ? "—" : (cur.spread_bp > 0 ? "+" : "") + cur.spread_bp + "bp"}</div><div class="d num">SOFR ${cur.sofr ?? "—"}% · IORB ${cur.iorb ?? "—"}%</div><div style="margin-top:6px">${chip(cur.spread_bp == null ? "—" : cur.spread_bp > 0 ? "자금시장 긴장" : cur.spread_bp > -5 ? "주의" : "안정", spreadTone)}</div></div>
          <div class="card kpi"><div class="lab">순유동성 (연준자산−TGA−역레포)</div><div class="v num">${fmtB(cur.net_liq)}</div><div class="d num">1주 <span class="${cls(cur.d_net_liq)}">${fmtB(cur.d_net_liq, true)}</span> · 4주 <span class="${cls(nl4)}">${fmtB(nl4, true)}</span></div></div>
          <div class="card kpi"><div class="lab">BTC/USDT (기준일 종가)</div><div class="v num">${fmtUsd(cur.btc)}</div><div class="d num">1주 <span class="${cls(cur.btc_ret)}">${fmtPct(cur.btc_ret)}</span> · 4주 <span class="${cls(btc4)}">${fmtPct(btc4)}</span></div></div>
        </div>
      </div>
    </section>

    <section>
      <div class="sec-head">
        <div><h2>BTC vs 순유동성</h2><span class="muted">두 값을 구간 시작점 = 100으로 맞춰 한 축에서 비교</span></div>
        <div class="toolbar" id="rangeBtns">
          <button type="button" data-n="26">6개월</button><button type="button" data-n="52" aria-pressed="true">1년</button><button type="button" data-n="104">2년</button><button type="button" data-n="9999">전체</button>
        </div>
      </div>
      <div class="card">
        <div class="legend"><span><i style="--c:var(--accent)"></i>BTC/USDT</span><span><i style="--c:var(--s1)"></i>순유동성</span></div>
        <div class="chart-box"><canvas id="cBtc" aria-label="BTC와 순유동성 지수 비교 차트"></canvas></div>
      </div>
    </section>

    <section>
      <div class="sec-head"><div><h2>세 개의 저수지</h2><span class="muted">TGA · 역레포 · 지급준비금 잔액 (수요일 기준)</span></div></div>
      <div class="card">
        <div class="legend"><span><i style="--c:var(--s2)"></i>TGA</span><span><i style="--c:var(--s1)"></i>역레포</span><span><i style="--c:var(--s3)"></i>지급준비금</span><span><i style="--c:var(--s4)"></i>연준 T-bill</span></div>
        <div class="chart-box"><canvas id="cLevels" aria-label="TGA 역레포 지준 잔액 차트"></canvas></div>
      </div>
    </section>

    <section>
      <div class="sec-head"><div><h2>BTC는 각 조합에서 어떻게 움직였나</h2><span class="muted">2022년 이후 주간 판정별 BTC/USDT 수익률</span></div></div>
      <div class="grid g2">
        <div class="card tscroll"><table id="scTable"></table></div>
        <div class="card" id="corrBox"></div>
      </div>
    </section>

    <section>
      <div class="sec-head"><div><h2>다가올 일정</h2><span class="muted">재무부 발행(결제) 일정과 세금 납부일 — TGA가 움직이는 날</span></div></div>
      <div class="card tscroll" id="calBox"></div>
    </section>

    <section>
      <div class="sec-head"><div><h2>주간 기록</h2><span class="muted">최근 26주</span></div></div>
      <div class="card tscroll"><table id="histTable"></table></div>
    </section>

    <section>
      <div class="sec-head"><div><h2>판정 기준: 다섯 가지 조합</h2><span class="muted">같은 TGA 움직임도 옆에 무엇이 붙느냐에 따라 결과가 달라집니다</span></div></div>
      <div class="grid" id="refList"></div>
    </section>`;
    const miss = data.missing_series || [];
    const missName = { reserves: "지급준비금", bills: "연준 T-bill", walcl: "연준 총자산", rrp: "역레포", sofr: "SOFR", iorb: "IORB" };
    $("#app").innerHTML = (miss.length ? `<div class="card" style="border-left:4px solid var(--warn);margin-bottom:16px">${chip("데이터 일부 없음", "--warn")} <span style="margin-left:6px">${esc(miss.map((k) => missName[k] || k).join(", "))} 수집 실패 — 해당 칸은 비어 있습니다.</span></div>` : "") + html;

    renderDecomp(cur);
    renderScenarioStats(W);
    renderOdds(W);
    renderCalendar(data, cur);
    renderHistory(W);
    renderRef(cur.scenario);
    $("#rangeBtns").addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      $("#rangeBtns").querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b ? "true" : "false"));
      window.__range = +b.dataset.n; renderCharts(data);
    });
    window.__range = 52;
    renderCharts(data);
  }

  function renderDecomp(w) {
    const rows = [
      ["TGA", w.d_tga, true],
      ["역레포", w.d_rrp, true],
      ["연준 T-bill", w.d_bills, false],
      ["지급준비금", w.d_reserves, false],
    ];
    const max = Math.max(25, ...rows.map((r) => Math.abs(r[1] || 0)));
    $("#decomp").innerHTML = rows.map(([lab, v, drain]) => {
      if (v == null || v === 0) return `<div>${lab}</div><div class="bar"></div><div class="val num">${v == null ? "—" : "±0억"}</div>`;
      // TGA·역레포는 증가가 시중 유동성 흡수 → 빨강, 감소 → 파랑. 지준·T-bill은 증가가 공급 → 파랑.
      const supply = drain ? v < 0 : v > 0;
      const pct = (Math.abs(v) / max) * 50;
      const left = v >= 0 ? 50 : 50 - pct;
      return `<div>${lab}</div><div class="bar" title="${lab} ${fmtB(v, true)}"><div class="fill" style="left:${left}%;width:${Math.max(pct, 0.6)}%;background:var(${supply ? "--pos" : "--neg"})"></div></div><div class="val num">${fmtShort(v, true)}</div>`;
    }).join("");
  }

  // 다음 주(수→수, 7일) BTC 수익률의 과거 분포. filter(w) 가 참인 주 다음 주를 모은다.
  function nextWeek(W, filter) {
    const r = [];
    for (let i = 1; i < W.length - 1; i++) if (filter(W[i]) && W[i + 1].btc_ret != null) r.push(W[i + 1].btc_ret);
    const n = r.length;
    if (!n) return null;
    const up = r.filter((x) => x > 0).length;
    return { n, up: (up / n) * 100, avg: r.reduce((a, b) => a + b, 0) / n };
  }

  function renderOdds(W) {
    const cur = W[W.length - 1];
    const sc = SCEN[cur.scenario] || SCEN.N;
    const all = nextWeek(W, () => true);
    let s = nextWeek(W, (w) => w.scenario === cur.scenario);
    let basis = `판정 ${sc.k}(${sc.title}) 이후 과거 ${s ? s.n : 0}주`;
    if (!s || s.n < 8) { s = all; basis = `표본 부족 — 전체 ${all ? all.n : 0}주 기준`; }
    if (!s) return;
    const up = s.up, dn = 100 - s.up;
    $("#btcOdds").innerHTML = `
      <div class="row"><span class="muted">향후 7일</span><span class="up">▲ 상승 ${up.toFixed(0)}%</span><span class="dn">▼ 하락 ${dn.toFixed(0)}%</span><span class="muted num">평균 ${fmtPct(s.avg, 1)}</span></div>
      <div class="bar" aria-hidden="true"><i style="width:${up}%;background:var(--good)"></i><i style="width:${dn}%;background:var(--crit)"></i></div>
      <div class="muted" style="font-size:12px">${esc(basis)} · 전체 평균 ${all.up.toFixed(0)}%<br>과거 빈도일 뿐 예측이 아닙니다</div>`;
  }

  function renderScenarioStats(W) {
    const stats = {};
    for (let i = 1; i < W.length; i++) {
      const w = W[i], s = w.scenario;
      if (!stats[s]) stats[s] = { n: 0, same: [], next: [] };
      stats[s].n++;
      if (w.btc_ret != null) stats[s].same.push(w.btc_ret);
      if (W[i + 1] && W[i + 1].btc_ret != null) stats[s].next.push(W[i + 1].btc_ret);
    }
    const avg = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);
    const up = (a) => (a.length ? (a.filter((x) => x > 0).length / a.length) * 100 : null);
    const cur = W[W.length - 1].scenario;
    let rows = ORDER.filter((k) => stats[k]).map((k) => {
      const s = stats[k], sc = SCEN[k];
      return `<tr${k === cur ? ' style="background:var(--surface-2)"' : ""}><td class="l"><span style="color:var(${sc.tone});font-weight:700">${esc(sc.k)}</span> ${esc(sc.title)} <span class="muted">+ ${esc(sc.cond)}</span></td><td class="num">${s.n}</td><td class="num ${cls(avg(s.same))}">${fmtPct(avg(s.same), 2)}</td><td class="num ${cls(avg(s.next))}">${fmtPct(avg(s.next), 2)}</td><td class="num">${up(s.next) == null ? "—" : up(s.next).toFixed(0) + "%"}</td></tr>`;
    }).join("");
    $("#scTable").innerHTML = `<thead><tr><th class="l">조합</th><th>주 수</th><th>같은 주</th><th>다음 주</th><th>다음 주 상승</th></tr></thead><tbody>${rows}</tbody>`;

    // 4주 순유동성 변화 vs 4주 BTC 수익률 상관
    const pairs = [];
    for (let i = 4; i < W.length; i++) {
      const a = W[i], b = W[i - 4];
      if (a.net_liq != null && b.net_liq != null && a.btc && b.btc) pairs.push([a.net_liq - b.net_liq, (a.btc / b.btc - 1) * 100, a.date]);
    }
    const corr = (n) => { const p = pairs.slice(-n); return pearson(p.map((x) => x[0]), p.map((x) => x[1])); };
    const c52 = corr(52), c104 = corr(104), cAll = corr(pairs.length);
    const tgaPairs = [];
    for (let i = 4; i < W.length; i++) {
      const a = W[i], b = W[i - 4];
      if (a.tga != null && b.tga != null && a.btc && b.btc) tgaPairs.push([a.tga - b.tga, (a.btc / b.btc - 1) * 100]);
    }
    const ct = pearson(tgaPairs.slice(-52).map((x) => x[0]), tgaPairs.slice(-52).map((x) => x[1]));
    const f = (c) => (c == null ? "—" : (c > 0 ? "+" : "") + c.toFixed(2));
    const word = (c) => (c == null ? "" : Math.abs(c) < 0.15 ? "거의 무관" : Math.abs(c) < 0.35 ? (c > 0 ? "약한 동행" : "약한 역행") : c > 0 ? "뚜렷한 동행" : "뚜렷한 역행");
    $("#corrBox").innerHTML = `
      <div class="kpi"><div class="lab">4주 순유동성 변화 ↔ 4주 BTC 수익률 상관계수</div></div>
      <table style="margin-top:8px"><tbody>
        <tr><td class="l">최근 1년</td><td class="num"><b>${f(c52)}</b></td><td class="l muted">${word(c52)}</td></tr>
        <tr><td class="l">최근 2년</td><td class="num">${f(c104)}</td><td class="l muted">${word(c104)}</td></tr>
        <tr><td class="l">2022년 이후 전체</td><td class="num">${f(cAll)}</td><td class="l muted">${word(cAll)}</td></tr>
        <tr><td class="l">4주 TGA 변화 ↔ BTC (1년)</td><td class="num">${f(ct)}</td><td class="l muted">${word(ct)}${ct != null && ct < 0 ? " · TGA↑ 때 BTC↓ 경향" : ""}</td></tr>
      </tbody></table>
      <p class="muted" style="margin:10px 0 0">겹치는 4주 구간으로 계산해 표본이 서로 독립적이지 않습니다. 방향 참고용입니다.</p>`;
  }

  function renderCalendar(data, cur) {
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const horizon = new Date(today); horizon.setDate(horizon.getDate() + 21);
    const iso = (d) => d.toISOString().slice(0, 10);
    const ev = [];
    for (const a of data.auctions || []) {
      if (!a.settle_date) continue;
      const d = new Date(a.settle_date + "T00:00:00");
      if (d < today || d > horizon) continue;
      ev.push({ date: a.settle_date, kind: "국채 결제", what: `${a.term || ""} ${a.type || ""}`.trim(), amt: a.amount_b, auction: a.auction_date });
    }
    const taxes = [[0, 15], [2, 15], [3, 15], [5, 15], [8, 15], [11, 15]];
    for (const y of [today.getFullYear(), today.getFullYear() + 1]) for (const [m, d] of taxes) {
      const t = new Date(y, m, d);
      if (t >= today && t <= horizon) ev.push({ date: iso(new Date(Date.UTC(y, m, d))), kind: "세금 납부일", what: "분기 법인세·추정세 (TGA 유입 ↑)", amt: null, tax: true });
    }
    ev.sort((a, b) => a.date.localeCompare(b.date));
    // 같은 날 결제 합계
    const byDay = {};
    for (const e of ev) if (e.kind === "국채 결제" && e.amt) byDay[e.date] = (byDay[e.date] || 0) + e.amt;
    const taxDays = new Set(ev.filter((e) => e.tax).map((e) => e.date));
    const near = (d) => [...taxDays].some((t) => Math.abs(new Date(t) - new Date(d)) <= 2 * 864e5);
    if (!ev.length) { $("#calBox").innerHTML = `<div class="muted">향후 3주 내 확인된 일정이 없습니다 (재무부 발표 전이거나 데이터 수집 실패).</div>`; return; }
    $("#calBox").innerHTML = `<table><thead><tr><th class="l">날짜</th><th class="l">구분</th><th class="l">내용</th><th>금액(총발행)</th><th class="l">메모</th></tr></thead><tbody>${ev.map((e) => {
      const warn = e.kind === "국채 결제" && near(e.date);
      return `<tr><td class="l num">${ymd(e.date)}</td><td class="l">${e.tax ? chip("세금", "--warn") : chip("결제", "--neutral")}</td><td class="l">${esc(e.what)}</td><td class="num">${e.amt ? fmtShort(e.amt) : ""}</td><td class="l muted">${warn ? "⚠ 납부일과 겹침 — 조합 3 위험" : e.auction ? "입찰 " + md(e.auction) + (byDay[e.date] ? " · 당일 결제 합계 " + fmtShort(byDay[e.date]) : "") : ""}</td></tr>`;
    }).join("")}</tbody></table>
    <p class="muted" style="margin:10px 0 0">총발행 기준이며 만기 상환분을 빼지 않았습니다. 순발행(=TGA 순유입)은 이보다 작습니다.</p>`;
  }

  function renderHistory(W) {
    const rows = W.slice(-26).reverse().map((w) => {
      const sc = SCEN[w.scenario] || SCEN.N;
      return `<tr><td class="num">${ymd(w.date)}</td><td class="l"><span style="color:var(${sc.tone});font-weight:700">${esc(sc.k)}</span> <span class="muted">${esc(sc.title)}</span></td>
        <td class="num">${fmtShort(w.rrp)}</td><td class="num ${cls(w.d_bills_4w)}">${fmtShort(w.d_bills_4w, true)}</td>
        <td class="num">${fmtShort(w.tga)}</td><td class="num ${cls(-w.d_tga)}">${fmtShort(w.d_tga, true)}</td>
        <td class="num ${cls(w.d_reserves)}">${fmtShort(w.d_reserves, true)}</td><td class="num">${w.spread_bp == null ? "—" : w.spread_bp + "bp"}</td>
        <td class="num">${fmtUsd(w.btc)}</td><td class="num ${cls(w.btc_ret)}">${fmtPct(w.btc_ret)}</td></tr>`;
    }).join("");
    $("#histTable").innerHTML = `<thead><tr><th class="l">기준일</th><th class="l">판정</th><th>① 역레포</th><th>② 연준 T-bill 4주</th><th>③ TGA</th><th>ΔTGA</th><th>Δ지준</th><th>SOFR−IORB</th><th>BTC</th><th>BTC 1주</th></tr></thead><tbody>${rows}</tbody>`;
  }

  function renderRef(current) {
    $("#refList").innerHTML = ["1", "2", "3", "4", "5"].map((k) => {
      const s = SCEN[k];
      return `<div class="card ref${k === current ? " now" : ""}" style="--tone:var(${s.tone})"><div class="k">${k}</div><div class="t">${esc(s.title)}<small>+ ${esc(s.cond)}</small></div><div class="e">${esc(s.effect)}</div><div class="x"><b>${esc(s.ex)}</b><br>${esc(s.exd)}${k === current ? `<br>${chip("이번 주", s.tone)}` : ""}</div></div>`;
    }).join("");
  }

  // ---------- Charts ----------
  const charts = {};
  function baseOpts(yFmt) {
    const grid = css("--grid"), tc = css("--text-3"), ink = css("--text"), surf = css("--surface"), bd = css("--border");
    return {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: { backgroundColor: surf, titleColor: ink, bodyColor: ink, borderColor: bd, borderWidth: 1, padding: 10, boxPadding: 4, usePointStyle: true,
          callbacks: { label: (c) => ` ${c.dataset.label}: ${yFmt(c.raw, c)}` } },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: tc, maxRotation: 0, autoSkipPadding: 18, font: { size: 11 } }, border: { color: bd } },
        y: { grid: { color: grid }, ticks: { color: tc, font: { size: 11 } }, border: { display: false } },
      },
      elements: { point: { radius: 0, hoverRadius: 4, hoverBorderWidth: 2, hoverBorderColor: surf }, line: { borderWidth: 2, tension: 0.15 } },
    };
  }

  function renderCharts(data) {
    if (!window.Chart) return;
    const W = data.weeks.filter((w) => w.tga != null);
    const n = window.__range || 52;
    const S = W.slice(-Math.min(n, W.length));
    const labels = S.map((w) => w.date.slice(2).replace(/-/g, "."));

    const b0 = S.find((w) => w.btc)?.btc, l0 = S.find((w) => w.net_liq != null)?.net_liq;
    const btcIdx = S.map((w) => (w.btc && b0 ? +((w.btc / b0) * 100).toFixed(1) : null));
    const nlIdx = S.map((w) => (w.net_liq != null && l0 ? +((w.net_liq / l0) * 100).toFixed(2) : null));
    const o1 = baseOpts((v, c) => {
      const w = S[c.dataIndex];
      return c.datasetIndex === 0 ? `${v} (${fmtUsd(w.btc)})` : `${v} (${fmtB(w.net_liq)})`;
    });
    o1.plugins.tooltip.callbacks.afterBody = (items) => { const w = S[items[0].dataIndex]; const sc = SCEN[w.scenario] || SCEN.N; return `판정 ${sc.k} · ${sc.title}`; };
    // 순유동성 변동폭이 BTC보다 훨씬 작아 별도 패널 대신 같은 축에 지수로 표시
    charts.btc?.destroy();
    charts.btc = new Chart($("#cBtc"), { type: "line", data: { labels, datasets: [
      { label: "BTC/USDT 지수", data: btcIdx, borderColor: css("--accent"), backgroundColor: css("--accent") },
      { label: "순유동성 지수", data: nlIdx, borderColor: css("--s1"), backgroundColor: css("--s1") },
    ] }, options: o1 });

    const o2 = baseOpts((v) => fmtB(v));
    o2.scales.y.ticks.callback = (v) => (v / 1000).toFixed(1) + "조";
    charts.lv?.destroy();
    charts.lv = new Chart($("#cLevels"), { type: "line", data: { labels, datasets: [
      { label: "TGA", data: S.map((w) => w.tga), borderColor: css("--s2"), backgroundColor: css("--s2") },
      { label: "역레포", data: S.map((w) => w.rrp), borderColor: css("--s1"), backgroundColor: css("--s1") },
      { label: "지급준비금", data: S.map((w) => w.reserves), borderColor: css("--s3"), backgroundColor: css("--s3") },
      { label: "연준 T-bill", data: S.map((w) => w.bills), borderColor: css("--s4"), backgroundColor: css("--s4") },
    ] }, options: o2 });
  }

  // ---------- Boot ----------
  async function boot() {
    initTheme();
    tickBtc(); setInterval(tickBtc, 60000);
    try {
      const r = await fetch("data/tga.json?ts=" + Date.now(), { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      const data = await r.json();
      window.__data = data;
      render(data);
    } catch (e) {
      $("#app").innerHTML = `<div class="card empty">아직 데이터가 없습니다. GitHub Actions의 <b>Update TGA data</b> 워크플로가 실행되면 <code>data/tga.json</code>이 생성됩니다.<br><span class="muted">(${esc(e.message)})</span></div>`;
    }
  }
  boot();
})();
