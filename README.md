구드공 폴더를 플렉스로 스캔하기 전에 vfs/refresh를 먼저 실행하고 싶어서 만들었어요.

### 컨셉은 이렇습니다.

- 플렉스로 스캔하고 싶은 폴더를 먼저 rclone 리모트로 vfs/refresh 합니다.
- vfs/refresh가 완료되면 해당 폴더를 plex_mate 플러그인을 통해 스캔합니다.

### 실행 환경 조건은 이렇습니다.

- Flaskfarm의 plex_mate 플러그인이 설치되어 있어야 합니다.
- rclone remote control이 활성화 되어 있어야 해요. (vfs/refresh 명령을 전송해야 하니...)

### 한계점은 이렇습니다.

- vfs/refresh가 항상 성공하는 건 아니라서 새로고침 없이 스캔될 여지가 있습니다.
- 런타임 plex_mate 플러그인이 필요하기 때문에 커맨드 등록시 **LOAD** 로 실행해야 합니다. (rclone 명령은 일반 python 스크립트처럼 실행 가능해요.)
- 오라클 ubuntu에서만 테스트했기 때문에 다른 OS에서 작동 여부는 장담 못합니다.
- 그밖에 예상치 못한 버그가 있을 수 있어요.

### ff_aider.sample.yaml 파일을 꼭 살펴본 뒤에 ff_aider.yaml로 변경해 주세요.

### 실행 방식은 flaskfarm의 시스템 툴 > Command에 등록해서 사용합니다.

> Command: **LOAD** /path/to/ff_aider/ff_aider.py

쉘에서 실행하는 것도 감안했으나 plex_mate 플러그인이 필요한 명령은 오류가 날 거예요.
CLI 도움말은 아래처럼 입력하면 볼 수 있어요.

> **python3** /path/to/ff_aider/ff_aider.py --help

혹은 실행 권한을 주고 바로 실행하세요.

> chmod +x /path/to/ff_aider/ff_aider.py
> /path/to/ff_aider/ff_aider.py --help

세부 명령은 ARGS 칸에 입력해 주세요.

> ARGS: **plexmate refresh --dirs** ***"/mnt/gds/VOD/1.방송중/드라마"***

### 세부 명령의 예시는 이렇습니다.

> Command: **LOAD** /path/to/ff_aider/ff_aider.py
> ARGS: **plexmate refresh --dirs** ***"/mnt/gds/VOD/1.방송중/드라마/A" "/mnt/gds/VOD/1.방송중/드라마/B"***

plex_mate의 [스캔]>[스캔 목록]에 입력받은 폴더를 READY 상태로 등록합니다.
refresh 명령으로 실행하면 vfs/refresh를 먼저 실행 후 등록합니다.
vfs/refresh 요청시 모든 폴더를 포함해서 한번만 요청합니다.

> Command: **LOAD** /path/to/ff_aider/ff_aider.py
> ARGS: **plexmate periodic {ID}**

{ID}는 plex_mate의 [주기적 스캔]>[작업 관리] 목록의 ID 번호입니다.
프로그램상 ID 인덱스는 0부터 시작하기 때문에 작업 관리 목록의 ID에서 1을 빼줘야 합니다.
예를 들어 목록의 ID 번호가 1이면 plexmate periodic 0 으로 입력해야 합니다.

periodic 명령으로 실행하면 vfs/refresh 할 폴더를 주기적 스캔 작업에서 가져옵니다.
"폴더" 정보가 있으면 해당 폴더를, 없으면 "섹션ID"를 토대로 섹션 전체 폴더를 사용합니다.
vfs/refresh가 완료되면 해당 주기적 스캔 작업을 실행합니다.

> Command: **LOAD** /path/to/ff_aider/ff_aider.py
> ARGS: **plexmate**

ARGS에 아무 명령 없이 plexmate만 입력하면 기존 오리알님의 pmscan_refresh.py 처럼 동작합니다.
동작 내용은 이렇습니다.

- SCANNNG 상태가 너무 오래 지속되는 항목을 FINISH_ALREADY_IN_QUEUE로 변경후 스캔 QUEUE에서 제외 (yaml에서 시간 지정)
- 파일 체크 TIMEOVER 된 항목중 일부를 다시 READY로 변경 (yaml에서 항목 지정)
- READY 상태인 항목들의 폴더를 vfs/refresh

> Command: **LOAD** /path/to/ff_aider/ff_aider.py
> ARGS: **plexmate scan --dirs** ***"/mnt/gds/VOD/1.방송중/드라마/A" "/mnt/gds/VOD/1.방송중/드라마/B"***

plex_mate의 [스캔]>[스캔 목록]에 입력받은 폴더를 READY 상태로 등록합니다.
scan 명령으로 실행하면 vfs/refresh 없이 등록됩니다.
[스캔]>[설정]>[기본]의 <테스트> 추가 모드와 동일한 기능이에요.
단순히 재스캔하고 싶거나 스캔 누락된 파일이 있을 때 사용하려고 만들었어요.

> Command: **python3** /path/to/ff_aider/ff_aider.py
> ARGS: **rclone vfs/refresh --dirs** ***"/mnt/gds/VOD/1.방송중/드라마/A" "/mnt/gds/VOD/1.방송중/드라마/B"***

rclone 리모트에 vfs/refresh 명령을 전송합니다.
스캔 없이 vfs/refresh만 실행합니다.
처리 과정중에 plex_mate 플러그인을 사용하지 않기 때문에 일반적인 python 스크립트처럼 실행하면 됩니다.

> Command: **python3** /path/to/ff_aider/ff_aider.py
> ARGS: **init**

flaskfarm "시작시 한번 실행" 용으로 쓰기 위해서 만들었어요.
설치된 플러그인을 토대로 시작전 필수로 실행되어야 하는 명령을 실행하기 위해서 만들었습니다.
로그에 어떤 명령이 실행되어야 하는지 확인할 수 있어요.
실제 실행 여부는 yaml파일의 **execute_commands**에서 조절합니다.
rclone 과 마찬가지로 일반 python 스크립트처럼 실행하면 됩니다.

끝.
