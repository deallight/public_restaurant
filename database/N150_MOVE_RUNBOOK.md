# N150 이사 절차서

이 절차는 `gonggibap.com` 운영 N150을 다른 인터넷 회선과 공유기로 옮길 때 사용한다.
현재 공개 경로는 다음과 같다.

```text
gonggibap.com A 레코드
  -> 집 공인 IPv4
  -> 공유기 TCP 80/443 포트포워딩
  -> N150 Nginx
  -> 127.0.0.1:8001 Web + PostgreSQL/Worker
```

회선이 바뀌어도 앱 주소, Nginx `server_name`, HTTPS 인증서, PostgreSQL loopback,
네이버·Google OAuth Callback과 Tailscale 주소는 그대로 유지한다. 바꿀 대상은 새
공유기의 DHCP 예약/포트포워딩과 호스팅케이알의 루트 A 레코드다. `www`는 루트
도메인을 가리키므로 별도의 IP 변경이 필요하지 않다.

## 1. 현재 준비 상태

- Web, Worker, Nginx, PostgreSQL, Tailscale, SSH는 부팅 자동 시작 상태여야 한다.
- 이사 전 N150은 고정 LAN IPv4를 사용하므로, 새 공유기 대역에서 연결되도록
  `depart` 명령이 다음 부팅용 DHCP 설정을 만든다.
- `depart`는 운영 DB를 custom-format으로 백업하고 `pg_restore --list`와 SHA-256
  검증을 마친 뒤에만 네트워크 설정을 준비한다.
- `netplan apply`는 실행하지 않는다. 현재 운영 연결은 전원이 꺼질 때까지 바뀌지
  않는다.

## 2. 스크립트 설치

저장소의 스크립트를 N150 사용자 홈에 복사한다.

```bash
mkdir -p ~/n150-move
install -m 755 scripts/prepare_n150_move.sh ~/n150-move/
```

먼저 N150 터미널에서 읽기 전용 점검을 실행한다.

```bash
sudo ~/n150-move/prepare_n150_move.sh preflight
```

모든 서비스가 `yes`, 실행 중 operation/OAuth worker job이 `0`, 마지막 줄이
`Netplan and PostgreSQL preflight passed`여야 한다.

## 3. 이삿날 전원 종료

관리자 수집·파싱·검증 작업을 새로 시작하지 않은 상태에서 아래 명령 하나를
실행한다.

```bash
sudo ~/n150-move/prepare_n150_move.sh depart
```

이 명령은 다음 순서로 동작한다.

1. 실행 중 operation/OAuth worker job이 없는지 확인하고 Worker를 중지한다.
2. `/srv/backups/public-restaurant/pre-move-*.dump` 백업을 만든다.
3. `pg_restore --list`와 SHA-256으로 백업을 검증한다.
4. 기존 Netplan 파일을 `/var/lib/n150-move/` 아래에 보관한다.
5. 다음 부팅에서 유선 IPv4를 DHCP로 받도록 설정하고 `netplan generate`로 검증한다.
6. Web을 중지하고 파일시스템을 동기화한 뒤 전원을 끈다.

전원 LED가 완전히 꺼진 뒤 전원·LAN 케이블을 분리한다. `depart`가 오류로 중단되면
원래 Netplan 설정과 Worker를 자동 복원한다.

## 4. 새 장소의 공유기 설정

N150을 새 공유기에 LAN 케이블로 연결하고 전원을 켠다. 공유기 관리 화면에서
N150을 찾을 때 필요한 값은 N150의 로컬 터미널에서만 확인한다.

```bash
~/n150-move/prepare_n150_move.sh router-values
```

출력값을 채팅이나 공개 문서에 붙이지 않는다. 공유기에서 다음을 설정한다.

1. 표시된 MAC 주소에 현재 LAN IPv4를 DHCP 예약한다.
2. TCP 80을 그 LAN IPv4의 TCP 80으로 포워딩한다.
3. TCP 443을 그 LAN IPv4의 TCP 443으로 포워딩한다.
4. 공유기 자체 원격관리에는 80/443을 사용하지 않는다.
5. 가능하면 UPnP는 끄고 위 두 규칙만 명시적으로 둔다.

통신사가 CGNAT를 사용해 공유기 WAN 주소와 인터넷에서 보이는 공인 IPv4가 다르면
일반 포트포워딩으로 공개할 수 없다. 이 경우 통신사에 공인 IPv4 제공을 요청하거나
별도의 터널 구성을 결정해야 한다.

## 5. 호스팅케이알 DNS 갱신

현재 권한 DNS는 `ns1~ns4.hosting.co.kr`이고 루트 A 레코드 TTL은 약 5분이다.
호스팅케이알에서 다음 순서로 갱신한다.

1. 로그인 후 `나의서비스 -> 도메인 관리`로 이동한다.
2. `gonggibap.com`을 선택한다.
3. `네임서버 / DNS -> DNS 레코드 관리`로 이동한다.
4. 이름이 비어 있는 루트 A 레코드의 값을 `router-values`가 표시한 새 공인 IPv4로
   바꾸고 저장한다.
5. 기존 A 레코드를 추가로 남기지 말고 루트 A 레코드가 하나인지 확인한다.

DNS 비밀번호나 인증 코드를 스크립트·저장소·채팅에 남기지 않는다.

## 6. 이사 후 검증

N150에서 다음을 실행한다.

```bash
~/n150-move/prepare_n150_move.sh postmove
```

DNS 전파 전에는 `dns_a_matches_new_public_ipv4`와 `public_https_200`이 `no`일 수 있다.
약 5분 뒤 다시 확인한다. 최종 기대값은 다음과 같다.

- `dhcp_ipv4_received=yes`
- Tailscale, Web, Worker, Nginx, PostgreSQL 모두 `active=yes`
- `dns_a_matches_new_public_ipv4=yes`
- 로컬 home/map와 public HTTPS가 모두 `200=yes`

마지막으로 Wi-Fi가 아닌 LTE/5G에서 아래 주소를 확인한다.

- `https://gonggibap.com/` -> 200 및 지도 표시
- `https://gonggibap.com/api/map/restaurants` -> 200
- `https://gonggibap.com/admin` -> 로그인하지 않은 상태에서 401
- 네이버 로그인 -> 기존 운영 Callback으로 정상 복귀

## 7. 복구

`depart` 실행 후 전원을 끄기 전에 DHCP 준비를 취소해야 한다면 다음을 실행한다.

```bash
sudo ~/n150-move/prepare_n150_move.sh restore-static
```

이 명령은 원래 Netplan 파일을 다음 부팅용으로 되돌리며 현재 연결에는 적용하지
않는다. 새 장소에서는 원래 고정 IP가 새 공유기 대역과 맞지 않을 수 있으므로,
DHCP 연결이 실패한 원인을 확인하지 않고 실행하지 않는다.
