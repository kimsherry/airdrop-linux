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
| 유닛 테스트 92개 + 종단간 스크립트 | ✅ 전부 통과 (1개는 플랫폼 조건으로 skip) |
| 실제 Wi-Fi 인터페이스에서 mDNS 발견·전송 | ✅ 검증 |
| 실제 아이폰의 mDNS 질의에 응답 | ✅ 관측 — 다만 AWDL이 아니라 아이폰이 접속하지 않음 (아래 [실제 아이폰 시도](#실제-아이폰-시도-2026-10-27)) |
| `awdl0` 위에서 실제 아이폰과 통신 | ⚠️ 미검증 (테스트한 PC의 내장 Wi-Fi가 active monitor 미지원) |
| Bluetooth 비콘 (BlueZ LE 광고) | ✅ 구현·등록 검증 (`ActiveInstances` 1→0). 아이폰이 실제로 반응하는지는 미검증 |
| contacts-only 모드 | ❌ 원리적으로 불가 (아래 [설명](#contacts-only는-왜-안-되나)) |

## 필요한 것

1. **active monitor mode + 프레임 주입이 되는 Wi-Fi 카드.** 이게 유일한 하드 제약입니다.
   - 권장: MediaTek MT7921AU 등 `mt76` 계열 (`NL80211_FEATURE_ACTIVE_MONITOR`)
   - 저렴: Atheros AR9271 (`ath9k_htc`), AR9280 (`ath9k`)
   - 불가: Intel CNVi/iwlwifi 계열 — 주입 시 펌웨어 크래시로 알려져 있음
   - 확인: `iw phy` 에 `Device supports active monitor` 가 있어야 합니다
2. 리눅스, 파이썬 3.10+ (`python3`), `openssl` (자체서명 인증서 생성용)
   - Bluetooth 비콘을 쓰려면 `python3-dbus`, `python3-gi` (Ubuntu/Mint는 보통 기본 설치)
   - 둘 중 하나가 없으면 비콘만 꺼지고 AirDrop 자체는 동작합니다
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
- `airdrop/beacon.py`: 새 파일. 윈도우판은 PowerShell로 BLE 광고를 쏘지만 리눅스에는
  그 API가 없어서, 같은 payload를 BlueZ `LEAdvertisingManager1`로 광고합니다.
- `airdrop/sender.py`: 플랫폼에 따라 PowerShell 비콘과 BlueZ 비콘을 고릅니다.
- `airdrop/receiver.py`: CLI에 수락 프롬프트(`Terminal`) 추가. 원본 CLI는 묻지 않고
  전부 저장했습니다.

## contacts-only는 왜 안 되나

AirDrop의 contacts-only는 "내 연락처에 있는 사람에게만 보이기"이고, 판정은 **아이폰/맥이**
합니다. 상대 기기는 우리가 보낸 신원 정보가 자기 연락처 해시와 맞을 때만 응답합니다.
그 신원은 iCloud 로그인 시 Apple이 서명해 발급하는 device certificate + validation record
(`UUID`, `SHA-256(이메일/전화번호)` 목록, Apple CA 서명)입니다.

- 리눅스 기기에는 Apple이 서명한 신원이 없습니다 → contacts-only 아이폰은 우리에게
  응답하지 않습니다. 코드로 우회할 수 있는 종류가 아니라 **키가 없는 문제**입니다.
- 유일한 방법은 Mac에서 `seemoo-lab/airdrop-keychain-extractor`로 그 자격증명을
  추출하는 것입니다(SIP 해제 + AMFI 우회 필요, Mac 실물 필요). OpenDrop이 이 방식으로
  contacts-only를 지원합니다.
- 이 저장소의 `airdrop/`은 아직 **자체서명 인증서만** 씁니다(원본 winawdl도 동일).
  추출한 자격증명을 쓰려면 상호 인증 레코드 교환/검증을 추가해야 합니다.

그래서 지금은 아이폰에서 AirDrop을 **"모든 사람(10분)"** 으로 두고 쓰는 것이 답입니다.
받는 쪽은 어차피 이 PC가 `y/N`으로 물어봅니다.

## 검증

```bash
cd airdrop
PYTHONPATH=. python3 tests/test_*.py        # 92개
PYTHONPATH=. python3 tests/e2e_loopback.py  # 루프백 종단간 (라디오 불필요)
```

종단간 스크립트는 엔진 두 개를 루프백으로 띄워 TLS 핸드셰이크 → `/Discover` →
`/Ask` → `/Upload`(dvzip) → 디코드 → 저장까지 확인하고 SHA-256을 비교합니다.

### 실제 아이폰 시도 (2026-10-27)

Bluetooth 비콘과 수신기를 켠 상태에서 아이폰으로 AirDrop 목록을 열어 확인했습니다.

- 아이폰이 같은 Wi-Fi(`wlo1`)에서 `_airdrop._tcp.local.`을 약 40초간 6회 질의
- 수신기는 매번 `flags=0x3fb`로 응답 (SRV/TXT/AAAA 포함)
- 아이폰은 TLS 연결을 시도하지 않음 (`/Discover` 0회)
- 원인: 아이폰은 AirDrop 상대를 AWDL 위에서만 찾는데, 시험한 PC의 Intel CNVi
  카드는 active monitor/프레임 주입이 안 되어 `awdl0`을 만들 수 없음

즉 프로토콜 계층은 아이폰의 질의에 정상 응답했지만, 링크 계층(AWDL)이 없어
목록에 뜨지 않았습니다. 소프트웨어가 아니라 Wi-Fi 카드 제약이며, active monitor가
되는 동글을 꽂으면 이 경로가 그대로 이어집니다.

## iOS 27.2 바이너리에서 확인한 것 (2026-10-29)

기기 없이 IPSW만으로 확인했습니다. 대상은 iPhone17,1 (iPhone 16 Pro)
iOS 27.2 (24B119)의 `/usr/lib/epsex/sharingd`(AirDrop 데몬)와
`Sharing.framework`이며, dyld shared cache를 추출해 정적 분석했습니다.

### TXT 레코드는 `flags` 하나뿐 (Everyone이면 `atag` 추가)

`-[SDAirDropPublisher updateTXTRecordAndNotify]`(`0x10006a274`)가
`NSNetService dataFromTXTRecordDictionary:` → `CFNetServiceSetTXTData`로 TXT를
만듭니다. 사전에 넣는 키는 두 개뿐입니다.

| 키 | 값 | 조건 |
|---|---|---|
| `flags` | `stringWithFormat:@"%ld"` → **10진수** | 항상 |
| `atag` | `[SDNearbyAgent bleAuthTag]`의 base64 | 발견 모드가 **Everyone**일 때만, 아니면 키 제거 |

`adVN` `adVF` `adDT` `cname` `phash` `ehash` `nhash` `comment` `ecolor` `icolor`
`osxvers` `model` 문자열은 바이너리에 있지만 **이 TXT 사전에는 들어가지 않습니다.**
이 저장소가 `flags`만 광고하는 것과 일치합니다. 다만 Everyone 모드의 `atag`는
이 구현이 보내지 않습니다(Apple 서명 신원이 없어 채울 값이 없음).

### `flags` 계산

```
base = 0x367
Wallet 앱이 있고 Apple ID가 개인(비관리)이면 base = 0x377
Gestalt "RenderWideGamutImagesAtDisplayTime" → base |= 0x80
Gestalt "R1Capability"                      → base |= 0x400
```

- iPhone 16 Pro(개인 Apple ID, wide gamut, R1 없음) → **`0x3f7` = 1015**
- 이 저장소는 macOS 실측값 `0x3fb`(1019)를 씁니다. 두 값은 `0x04`와 `0x08`
  비트가 서로 다릅니다. 받는 쪽이 Mac으로 보이는 게 목적이라 macOS 값을 유지합니다.
- 값을 10진수 문자열로 쓴다는 점은 구현과 같습니다.

### 발견할 때 읽는 TXT는 서비스마다 다르다

`-[SDBonjourBrowser processTXTRecordUpdate:rdlen:rdata:]`(`0x1001493a0`):

| Bonjour 서비스 | 읽는 TXT 키 | 콜백 |
|---|---|---|
| `_airdrop._tcp.` | `flags` `atag` `cname` `phash` `ehash` `nhash` | `personInfoChanged:...` |
| `_device-info._tcp.` | `model` `ecolor` `icolor` `osxvers` | `deviceInfoChanged:...` |
| `_netbios._udp.` | `comment` (`sys`) | `commentChanged:` |

즉 상대의 모델명·기기 색은 `_airdrop._tcp.` TXT가 아니라 `_device-info._tcp.`
TXT로 옵니다. 이 저장소는 `_device-info._tcp.`를 광고하지 않습니다(모델명은
`/Discover` 응답의 `ReceiverModelName`으로 전달).

### `_airdrop-alt._tcp.`는 고정 대안이 아니라 환경설정 스위치

`sub_1000f1628`:

- `AlternateService` 환경설정 없음 → `_airdrop._tcp.`
- bool `true` 또는 문자열 `"1"` → `_airdrop-alt._tcp.`
- 그 외 문자열 → 앞 7글자를 붙여 `_airdrop-<7자>._tcp.`
- `EnableDemoMode`가 true → `_airdrop-demo._tcp.`

### 최신 수신 데이터 모델 (Sharing.framework)

`Sharing.SFAirDropReceive.AskRequest`는 Codable 구조체이고 키는 다음과 같습니다.

`id` `type` `transferTypes` `canAutoAccept` `verifiableIdentity` `senderIsMe`
`contactIdentifier` `senderBundleID` `senderComputerName` `senderModelName`
`senderEmail` `senderEmailHash` `senderCompositeName` `senderFirstName`
`senderLastName` `senderID` `senderIcon` `smallPreviewImage` `previewImage`
`itemsDescription` `itemsDescriptionAdvanced` `urlItems` `items` `customPayload`
`pairingPin` `senderIdentityAuthTag`

`AskResponse`는 plist 딕셔너리가 아니라 enum입니다:
`accept(destination, acceptContext{shouldExtractMedia, customPayload})` /
`decline` / `error` / `mixedTypes`.

이 구현의 `/Ask`는 예전 키(`Files` `Items` `TransferType` `SenderComputerName`)를
읽습니다. 최신 iOS가 위 키로 보내면 파일 목록 로그와 링크(URL) 판별이 비게 됩니다.
실제 와이어 인코딩(plist/JSON)과 하위 호환 여부는 아이폰 실물로 확인해야 합니다.

## 알려진 한계

- OWL이 Wi-Fi 카드를 독점합니다 → AirDrop 사용 중 일반 Wi-Fi 인터넷 불가
  (이더넷/USB 테더링은 영향 없음).
- OWL 저장소는 2026-10에 archived 되었습니다. 최신 macOS/iOS 호환은 보장되지 않습니다.
- Bluetooth 비콘은 BlueZ가 켜져 있고 어댑터가 powered 상태여야 동작합니다
  (`rfkill list`로 확인, `rfkill unblock bluetooth`). 어댑터가 없으면 로그에 이유가 남고
  AirDrop은 그대로 동작합니다 — 이때는 아이폰에서 AirDrop 화면을 열어두면 됩니다.
- 비콘이 공중에 나가는 것까지는 확인했지만, 그 신호를 받은 아이폰이 AWDL을 켜는지는
  아이폰 실물과 AWDL 카드가 있어야 확인됩니다.
- 이 패키지는 mDNS 멀티캐스트 루프백을 끕니다(`IPV6_MULTICAST_LOOP=0`). 그래서
  **같은 PC 안에서는** 수신기와 송신기가 서로를 mDNS로 찾지 못합니다. 실제 기기끼리는
  영향이 없고, 루프백 테스트는 엔진의 `--loopback` 모드를 씁니다.
- AirDrop은 Apple의 상표입니다. 이 프로젝트는 Apple과 무관합니다.

## 라이선스

MIT. `airdrop/`은 [winawdl](https://github.com/raph559/winawdl)의
AirDrop 계층(저작자 Brent Kearney, MIT)에서 왔고, 원 라이선스는
[`airdrop/LICENSE`](airdrop/LICENSE)에 그대로 있습니다. OWL은 별도 프로젝트이며
이 저장소에 포함되지 않습니다.
