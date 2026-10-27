# airdrop-linux

Apple AirDrop on Linux: the AirDrop protocol layer of
[raph559/winawdl](https://github.com/raph559/winawdl) ported to run on Linux
over [OWL](https://github.com/seemoo-lab/owl) (AWDL).

IOS·macOS 기기와 리눅스 사이에서 AirDrop을 주고받기 위한 프로토콜 계층입니다.
라디오(AWDL)는 OWL이, 발견과 전송은 이 저장소의 `airdrop/`이 담당합니다.

- 순수 파이썬 3, 표준 라이브러리만 사용 (설치할 패키지 없음)
- 최신 프로토콜: `_airdrop._tcp.local.` mDNS 발견, `/Discover` `/Ask` `/Upload`,
  `application/x-dvzip` + `x-cpio`, `flags=0x3fb`
- 받은 파일은 절대 열지 않고, 기본값으로 **받기 전에 물어봅니다**

## 상태

| 항목 | 상태 |
|---|---|
| 프로토콜 계층 (발견·TLS·수락·전송·저장) | ✅ 리눅스에서 종단간 검증 |
| 유닛 테스트 94개 + 종단간 스크립트 | ✅ 전부 통과 |
| 실제 Wi-Fi 인터페이스에서 mDNS 발견·전송 | ✅ 검증 |
| `awdl0` 위에서 실제 아이폰과 통신 | ⚠️ 미검증 (테스트한 PC의 내장 Wi-Fi가 active monitor 미지원) |
| contacts-only 모드 | ❌ 미구현 (애플 서명 인증서 필요) |
| Bluetooth 비콘 | ❌ 리눅스 미구현 (아이폰에서 AirDrop 화면을 열어두면 됨) |

## 필요한 것

1. **active monitor mode + 프레임 주입이 되는 Wi-Fi 카드.** 이게 유일한 하드 제약입니다.
   - 권장: MediaTek MT7921AU 등 `mt76` 계열 (`NL80211_FEATURE_ACTIVE_MONITOR`)
   - 저렴: Atheros AR9271 (`ath9k_htc`), AR9280 (`ath9k`)
   - 불가: Intel CNVi/iwlwifi 계열 — 주입 시 펌웨어 크래시로 알려져 있음
   - 확인: `iw phy` 에 `Device supports active monitor` 가 있어야 합니다
2. 리눅스, 파이썬 3.10+ (`python3`), `openssl` (자체서명 인증서 생성용)
3. OWL 빌드 (아래)

## 설치

```bash
git clone https://github.com/kimsherry/airdrop-linux.git
cd airdrop-linux
```

`airdrop/`은 그대로 실행됩니다. OWL은 따로 빌드해야 합니다 (root 불필요):

```bash
# 빌드 의존성 (libpcap/libev/libnl 개발 패키지) 중 하나:
sudo apt install libpcap-dev libev-dev libnl-3-dev libnl-genl-3-dev libnl-route-3-dev

git clone --recurse-submodules https://github.com/seemoo-lab/owl
cmake -S owl -B owl/build -DCMAKE_BUILD_TYPE=Release
cmake --build owl/build --target owl -j2
```

## 사용

```bash
# 터미널 1: AWDL 시작 (root, 카드를 독점하므로 그동안 일반 Wi-Fi는 끊김)
sudo ./owl/build/daemon/owl -i wlan1      # awdl0 가 생김

# 터미널 2: 수신 대기 — 아이폰/맥 AirDrop 목록에 이 PC가 뜸
./bin/airdrop-receive

# 보내기
./bin/airdrop-send 사진.jpg 문서.pdf
```

`bin/`의 래퍼는 `awdl0`을 기본으로 씁니다. 다른 이름이면
`AIRDROP_IFACE=wlan1 ./bin/airdrop-receive` 처럼 지정하세요.

자주 쓰는 옵션:

```bash
./bin/airdrop-receive --name "내 PC" --outdir ~/Pictures   # 이름/저장 위치
./bin/airdrop-receive --take-all                           # 묻지 않고 전부 받기
./bin/airdrop-receive --max-receive-percent 10             # 여유 공간의 10%까지만
./bin/airdrop-receive --redact                             # 로그에서 기기 이름 감추기
./bin/airdrop-send --to 아이폰 --search 20 사진.jpg        # 대상 지정/검색 시간
```

## 왜 OpenDrop이 아니라 이걸 쓰나

OpenDrop은 2018년대 프로토콜 구현이라 최신 아이폰이 광고를 무시할 수 있습니다.
이 계층은 macOS가 실제로 쓰는 값을 따릅니다.

| | OpenDrop | `airdrop/` |
|---|---|---|
| 업로드 포맷 | `application/x-cpio` | `x-dvzip` + `x-cpio` 둘 다 |
| 광고 flags | 0x381 계열 | `0x3fb` (macOS 실제 값) |
| 거절 응답 | 없음 | `401` |
| 받은 파일 | 아카이브를 그대로 품 | 아무것도 열지 않음 |
| 디스크 보호 | 없음 | `--max-receive-percent` (기본 30%) |
| 수락 여부 | 항상 수락 | 터미널에서 `y/N` |

## winawdl에서 바꾼 것

원본은 Windows 전용입니다. 이식에 필요했던 변경은 세 곳뿐입니다.

- `airdrop/netif.py`: `iphlpapi`(Win32) 호출을 `os.name` 분기로 두고 리눅스에서는
  `socket.if_nametoindex` 사용. 링크로컬 주소 조회는 원본이 그대로 동작했습니다.
- `airdrop/identity.py`: 키 보관 위치를 `~/.local/share/airdrop/keys`로.
- `airdrop/sender.py`: 리눅스에는 Bluetooth 비콘이 없어 건너뜁니다 (`shortcut:` 주석).
- `airdrop/receiver.py`: CLI에 수락 프롬프트(`Terminal`) 추가. 원본 CLI는 묻지 않고
  전부 저장했습니다.

## 검증

```bash
cd airdrop
PYTHONPATH=. python3 tests/test_*.py        # 94개
PYTHONPATH=. python3 tests/e2e_loopback.py  # 루프백 종단간 (라디오 불필요)
```

종단간 스크립트는 엔진 두 개를 루프백으로 띄워 TLS 핸드셰이크 → `/Discover` →
`/Ask` → `/Upload`(dvzip) → 디코드 → 저장까지 확인하고 SHA-256을 비교합니다.

## 알려진 한계

- OWL이 Wi-Fi 카드를 독점합니다 → AirDrop 사용 중 일반 Wi-Fi 인터넷 불가
  (이더넷/USB 테더링은 영향 없음).
- OWL 저장소는 2026-10에 archived 되었습니다. 최신 macOS/iOS 호환은 보장되지 않습니다.
- 이 패키지는 mDNS 멀티캐스트 루프백을 끕니다(`IPV6_MULTICAST_LOOP=0`). 그래서
  **같은 PC 안에서는** 수신기와 송신기가 서로를 mDNS로 찾지 못합니다. 실제 기기끼리는
  영향이 없고, 루프백 테스트는 엔진의 `--loopback` 모드를 씁니다.
- AirDrop은 Apple의 상표입니다. 이 프로젝트는 Apple과 무관합니다.

## 라이선스

MIT. `airdrop/`은 [winawdl](https://github.com/raph559/winawdl)의
AirDrop 계층(저작자 Brent Kearney, MIT)에서 왔고, 원 라이선스는
[`airdrop/LICENSE`](airdrop/LICENSE)에 그대로 있습니다. OWL은 별도 프로젝트이며
이 저장소에 포함되지 않습니다.
