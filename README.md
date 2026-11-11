# TGA 주간 트래커 (BTC/USDT 연결)

> TGA는 혼자 읽지 않습니다. **① 역레포가 비었는지 → ② 연준이 단기채를 사고 있는지 → ③ TGA는 마지막에.**

매주 H.4.1(수요일 잔액) 기준으로 위 순서대로 판정하고, 결과가 지급준비금·SOFR−IORB·순유동성과
비트코인(BTC/USDT) 가격에 어떻게 나타나는지 한 화면에서 봅니다.

## 판정 규칙 (`scripts/fetch_data.py`의 `TH`에서 조정)

| 조합 | 조건 | 의미 |
|---|---|---|
| 1 | TGA 주간 +250억 달러 이상 & 역레포 ≥ 5,000억 | 역레포가 흡수, 지준 거의 그대로 |
| 2 | TGA 증가 & 역레포 < 5,000억 (바닥 < 1,000억) | 지준이 그대로 감소, 단기금리 상승 |
| 3 | TGA 증가 & (세금 납부일 주간 또는 SOFR 주중 최고 > IORB) & 역레포 넉넉하지 않음 | 레포금리 급등 위험 |
| 4 | TGA 주간 −250억 이상 & 부채한도 구간 | 지준 증가, 한도 해제 후 되돌림 |
| 5 | TGA 증가 & 연준 T-bill 4주 순증 ≥ 200억 | 연준이 빠지는 지준을 채움 |
| ↓ / = | TGA 감소(일반) / 보합 | 유동성 공급 / 다른 변수 우선 |

부채한도 구간은 `DEBT_CEILING`에 직접 추가하세요(자동 감지 불가).

## 데이터

- FRED (키 불필요): `WTREGEN`(TGA), `RRPONTSYD`(역레포), `WRESBAL`(지준), `WSHOBL`(연준 T-bill), `WALCL`(연준 총자산), `SOFR`, `IORB`
- 재무부 Fiscal Data: DTS 일간 TGA, Upcoming Auctions(결제 일정)
- BTC/USDT 일봉 종가: Binance → OKX → KuCoin 순으로 시도. 페이지 상단 실시간 가격은 브라우저에서 Binance 직접 조회
- 순유동성 = 연준 총자산 − TGA − 역레포

## 실행

```bash
python3 scripts/fetch_data.py      # data/tga.json 생성 (표준 라이브러리만 사용)
python3 -m http.server 8000        # http://localhost:8000 에서 확인
```

## 자동 갱신 & 배포 (GitHub Pages)

`.github/workflows/update.yml`이 매일 22:23 UTC(한국 07:23)에 데이터를 받아 `data/tga.json`을 커밋하고,
`main` 브랜치에서는 GitHub Pages로 배포합니다.

1. 이 브랜치를 `main`에 병합
2. 저장소 **Settings → Pages → Build and deployment → Source: GitHub Actions**
3. **Actions → Update TGA data → Run workflow** 로 첫 실행
