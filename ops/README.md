# WMZ 운영 명령어 정리

이 문서는 Minecraft 서버 네트워크를 빌드하고 설정을 관리하기 위한
`ops` 도구 사용법을 정리합니다.

명령어는 프로젝트 루트에서 실행합니다.

```bash
cd /c/Users/dnjsw/Desktop/develope/wmz
```

## 디렉토리 구조

```text
ops/manifests           서버/플러그인 선택과 상속 정의
ops/templates           common 및 서버별 config 템플릿
ops/vendor/plugins      플러그인 jar 보관소, Git에 커밋하지 않음
ops/vendor/server       서버/proxy jar 보관소, Git에 커밋하지 않음
ops/tools               실제 Python 도구
ops/scripts             Bash 실행 wrapper(.sh)
ops/secrets             비밀값/env, Git에 커밋하지 않음
workbench/bootstrap/*   기본 config 생성을 위한 작업장
workbench/build/*       서버 빌드 테스트용 작업장
deploy/*                실제 운영/배포용 서버 인스턴스
```

기본 상속 흐름은 얕게 유지합니다.

```text
common -> survival
common -> farm
common -> dungeon
```

## 실행 방식

Windows에서는 Git Bash를 열어서 실행하고, Linux에서는 일반 Bash에서 실행합니다.
실제 기능은 `ops/tools/*.py`에 있고, `ops/scripts/*.sh`는 같은 Python 도구를 호출하는 얇은 wrapper입니다.

Windows Git Bash:

```bash
cd /c/Users/dnjsw/Desktop/develope/wmz
./ops/scripts/server.sh check survival
```

Linux Bash:

```bash
cd /path/to/wmz
./ops/scripts/server.sh check survival
```

사용하는 명령은 전부 `.sh`입니다.

```text
config.sh
live-config.sh
manifest.sh
plugins.sh
server.sh
workbench.sh
```

경로 인자는 `/`로 씁니다.

```bash
./ops/scripts/server.sh check survival plugins/EcoItems
./ops/scripts/config.sh apply survival plugins/EcoItems --dry-run
```

Git Bash에서 `python3`가 WindowsApps alias로 잡히는 경우가 있어, `.sh` wrapper는 Python 후보를 실제로 실행해 보고 사용할 수 있는 실행기만 선택합니다.

## 기본 작업 흐름

1. jar로 bootstrap 작업장을 만듭니다.

```bash
./ops/scripts/workbench.sh bootstrap common
```

2. bootstrap 서버를 한 번 실행해서 기본 config를 생성합니다.

```bash
./ops/scripts/workbench.sh run-local bootstrap common --accept-eula --timeout-seconds 240
```

3. 필요한 config만 templates로 가져옵니다.

```bash
./ops/scripts/workbench.sh import-bootstrap common plugins/EcoItems --dry-run
./ops/scripts/workbench.sh import-bootstrap common plugins/EcoItems
```

4. deploy 서버 빌드를 미리 확인합니다.

```bash
./ops/scripts/server.sh check survival
```

5. 실제 deploy 서버 인스턴스를 빌드합니다.

```bash
./ops/scripts/server.sh build survival
```

특정 플러그인 config만 적용할 수도 있습니다.

```bash
./ops/scripts/server.sh check survival plugins/EcoItems
./ops/scripts/server.sh build survival plugins/EcoItems
```

## server.sh

`server.sh`는 실제 배포 인스턴스를 만드는 통합 명령입니다.

하는 일:

```text
manifest 확인
server.jar 적용
plugin jar 적용
templates 적용
deploy/<server> 생성 또는 갱신
```

서버 요약 보기:

```bash
./ops/scripts/server.sh show survival
```

deploy 대상 경로 보기:

```bash
./ops/scripts/server.sh path survival
```

실제 파일을 쓰지 않고 빌드 확인:

```bash
./ops/scripts/server.sh check survival
./ops/scripts/server.sh build survival --dry-run
```

실제 빌드:

```bash
./ops/scripts/server.sh build survival
```

특정 config 경로만 적용:

```bash
./ops/scripts/server.sh build survival plugins/EcoItems
./ops/scripts/server.sh build survival plugins/EcoItems/config.yml
```

자주 쓰는 옵션:

```bash
./ops/scripts/server.sh build survival --prune
./ops/scripts/server.sh build survival --check-hashes
./ops/scripts/server.sh build survival --allow-missing
./ops/scripts/server.sh build survival --no-config
./ops/scripts/server.sh build survival --target deploy/survival-test
```

옵션 의미:

```text
--prune          대상 plugins 폴더에서 manifest에 없는 오래된 jar 제거
--check-hashes   manifest의 sha256 값 검증
--allow-missing  template 변수 값이 없어도 그대로 남김
--no-config      server.jar와 plugin jar만 적용하고 config는 적용하지 않음
--target         기본 deploy/<server> 대신 다른 경로에 빌드
```

## workbench.sh

`workbench.sh`는 임시 작업장을 다룹니다.

작업장 경로 보기:

```bash
./ops/scripts/workbench.sh bootstrap-path common
./ops/scripts/workbench.sh build-path survival
```

bootstrap 작업장 만들기:

```bash
./ops/scripts/workbench.sh bootstrap common
./ops/scripts/workbench.sh bootstrap common --manifest survival
./ops/scripts/workbench.sh bootstrap common --server purpur-26.1.2-2592.jar
```

bootstrap은 templates를 적용하지 않습니다. server jar와 plugin jar만 복사해서
서버가 직접 기본 config를 생성하게 만드는 단계입니다.

로컬 서버 실행:

```bash
./ops/scripts/workbench.sh run-local bootstrap common --accept-eula --timeout-seconds 240
./ops/scripts/workbench.sh run-local build survival --accept-eula --timeout-seconds 240
```

`--timeout-seconds` 시간이 지나면 자동으로 `stop`을 보냅니다.

bootstrap에서 templates로 config 가져오기:

```bash
./ops/scripts/workbench.sh import-bootstrap common plugins/EcoItems --dry-run
./ops/scripts/workbench.sh import-bootstrap common plugins/EcoItems
```

전체 후보 확인:

```bash
./ops/scripts/workbench.sh import-bootstrap common --dry-run
```

runtime/state 성격의 파일은 기본적으로 제외됩니다. 정말 포함해야 할 때만
`--include-runtime`을 사용합니다.

```bash
./ops/scripts/workbench.sh import-bootstrap common --dry-run --include-runtime
```

빌드 테스트 작업장 만들기:

```bash
./ops/scripts/workbench.sh assemble survival --dry-run
./ops/scripts/workbench.sh assemble survival
```

빌드 테스트 작업장 실행:

```bash
./ops/scripts/workbench.sh run-local build survival --accept-eula --timeout-seconds 240
```

## config.sh

`config.sh`는 config 파일을 가져오거나 templates를 적용하는 저수준 명령입니다.

manifest를 읽어서 bootstrap 작업장을 준비하고, 생성된 config를 서버별
template layer로 가져오기:

```bash
./ops/scripts/config.sh init survival --dry-run
./ops/scripts/config.sh init survival
```

기본 동작에는 bootstrap 서버 실행이 포함됩니다. 즉 서버를 한 번 켜서 플러그인
config를 생성한 뒤 templates로 가져옵니다. 실행을 건너뛰고 이미 존재하는
bootstrap config만 가져오려면 `--no-run`을 붙입니다.

```bash
./ops/scripts/config.sh init survival --no-run --dry-run
./ops/scripts/config.sh init survival --no-run
```

특정 플러그인만 초기화:

```bash
./ops/scripts/config.sh init survival better-structures --dry-run
./ops/scripts/config.sh init survival better-structures
```

기본 동작은 bootstrap에 있는 config 파일을 templates로 가져오는 것입니다.
디렉토리 자리만 만들고 싶으면 `--no-import`를 붙입니다.

```bash
./ops/scripts/config.sh init survival better-structures --no-import --dry-run
./ops/scripts/config.sh init survival better-structures --no-import
```

`--run`은 기본값이지만, 명확히 쓰고 싶으면 붙여도 됩니다.

```bash
./ops/scripts/config.sh init survival better-structures --run --dry-run
./ops/scripts/config.sh init survival better-structures --run
```

기본 동작은 `survival.yml`에 직접 정의된 플러그인만 대상으로 합니다.
common에서 상속된 플러그인까지 포함하려면 `--all`을 붙입니다.

```bash
./ops/scripts/config.sh init survival --all --dry-run
```

template layer 또는 플러그인 override 자리만 수동으로 만들기:

```bash
./ops/scripts/config.sh init-layer survival
./ops/scripts/config.sh init-layer survival plugins/BetterStructures
./ops/scripts/config.sh init-layer survival plugins/BetterStructures --dry-run
```

가져올 수 있는 config 파일 목록 보기:

```bash
./ops/scripts/config.sh scan bootstrap/common --from workbench
./ops/scripts/config.sh scan survival --from server
```

실제 플러그인 config 디렉토리 보기:

```bash
./ops/scripts/config.sh plugin-dirs bootstrap/common --from workbench
./ops/scripts/config.sh plugin-dirs bootstrap/common --from workbench --counts
```

config를 template layer로 가져오기:

```bash
./ops/scripts/config.sh import bootstrap/common common plugins/EcoItems --from workbench --dry-run
./ops/scripts/config.sh import bootstrap/common common plugins/EcoItems --from workbench
```

templates를 서버 인스턴스에 적용:

```bash
./ops/scripts/config.sh apply survival --dry-run
./ops/scripts/config.sh apply survival plugins/EcoItems --dry-run
./ops/scripts/config.sh apply survival plugins/EcoItems --target deploy/survival
```

template 변수 확인:

```bash
./ops/scripts/config.sh vars survival
./ops/scripts/config.sh vars survival --verbose
```

## manifest.sh

`manifest.sh`는 서버 manifest와 플러그인 선택을 관리합니다.

manifest 목록 보기:

```bash
./ops/scripts/manifest.sh list
./ops/scripts/manifest.sh list --plugins
./ops/scripts/manifest.sh list --plugins --resolved
```

manifest 요약 보기:

```bash
./ops/scripts/manifest.sh show survival
```

상속까지 반영된 manifest 보기:

```bash
./ops/scripts/manifest.sh resolve survival
./ops/scripts/manifest.sh resolve survival --format json
```

manifest 검증:

```bash
./ops/scripts/manifest.sh validate
./ops/scripts/manifest.sh validate --check-files
./ops/scripts/manifest.sh validate --check-files --check-hashes
```

주의: `--check-files`는 vendor jar가 실제로 있는지 검사합니다. 일부 jar를 일부러
제거한 상태라면 기본 `validate`만 사용하는 편이 낫습니다.

manifest 생성:

```bash
./ops/scripts/manifest.sh create survival
./ops/scripts/manifest.sh create proxy --extends proxy-common
./ops/scripts/manifest.sh create common --no-extends --force
```

플러그인 추가:

```bash
./ops/scripts/manifest.sh add-plugin common ecoitems EcoItems-2026.25.jar
./ops/scripts/manifest.sh add-plugin common ecoitems EcoItems-2026.25.jar --hash
```

플러그인 제거 또는 상속 제거 override:

```bash
./ops/scripts/manifest.sh remove-plugin survival ecoitems
./ops/scripts/manifest.sh remove-plugin survival ecoitems --override
```

server jar만 적용:

```bash
./ops/scripts/manifest.sh apply-server survival --target deploy/survival
```

plugin jar만 적용:

```bash
./ops/scripts/manifest.sh apply-plugins survival --target deploy/survival
./ops/scripts/manifest.sh apply-plugins survival --target deploy/survival --prune
```

## plugins.sh

`plugins.sh`는 plugin jar 적용만 빠르게 실행하는 wrapper입니다.

```bash
./ops/scripts/plugins.sh apply survival --dry-run
./ops/scripts/plugins.sh apply survival
./ops/scripts/plugins.sh apply survival --prune
```

일반적인 서버 빌드는 `server.sh build`를 사용합니다. jar만 따로 확인하거나
디버깅할 때 `plugins.sh`를 사용합니다.

## live-config.sh

`live-config.sh`는 templates에서 수정한 플러그인 config를 deploy 서버에 적용하고,
실행 중인 Docker 서버에 reload 명령을 보냅니다.

명령 순서는 플러그인 이름이 먼저이고, 서버 이름은 선택입니다.

```bash
./ops/scripts/live-config.sh push EcoItems --dry-run
./ops/scripts/live-config.sh push EcoItems
./ops/scripts/live-config.sh push EcoItems survival --dry-run
./ops/scripts/live-config.sh push EcoItems survival
```

서버 이름을 적지 않으면 `docker-compose.yml`의 service 이름과 같은 manifest를
대상으로 잡습니다. 현재 compose에 `survival`만 있으면 survival만 대상입니다.
나중에 compose에 `farm`, `dungeon` 서비스를 추가하면 같은 명령으로 함께 처리됩니다.

하는 일:

```text
templates/common + templates/<server>에서 해당 plugin config 적용
deploy/<server>/plugins/<PluginName> 갱신
docker compose exec로 서버 콘솔에 reload 명령 전송
```

기본 reload 명령은 플러그인 이름을 소문자 명령으로 바꿔서 추론합니다.

```text
EcoItems         -> ecoitems reload
EcoCollections   -> ecocollections reload
BetterStructures -> betterstructures reload
```

명령이 다르면 `--command`로 직접 지정합니다. 여러 번 지정할 수도 있습니다.

```bash
./ops/scripts/live-config.sh push EcoItems survival --command "ecoitems reload"
./ops/scripts/live-config.sh push Nexo survival --command "nexo reload"
```

config 적용만 하고 reload는 보내지 않기:

```bash
./ops/scripts/live-config.sh push EcoItems survival --apply-only
```

config 적용 없이 reload 명령만 보내기:

```bash
./ops/scripts/live-config.sh push EcoItems survival --reload-only
```

## templates 상속 규칙

common templates가 먼저 적용되고, 서버별 templates가 나중에 적용됩니다.

같은 상대 경로의 파일이 있으면 서버별 파일이 최종 결과를 덮어씁니다.

예시:

```text
ops/templates/common/plugins/EcoItems/config.yml
ops/templates/survival/plugins/EcoItems/config.yml
  -> deploy/survival/plugins/EcoItems/config.yml
```

운영 원칙:

```text
공통 기본값       -> ops/templates/common
survival 전용 값  -> ops/templates/survival
farm 전용 값      -> ops/templates/farm
```

## survival 전용 플러그인 사전 설정

survival에만 추가되는 플러그인은 `ops/manifests/survival.yml`에 정의하고,
config는 `ops/templates/survival`에 override로 둡니다.

먼저 manifest를 기준으로 survival 전용 template layer 또는 플러그인 config
자리를 만듭니다.

```bash
./ops/scripts/config.sh init survival better-structures --dry-run
./ops/scripts/config.sh init survival better-structures
```

플러그인을 한 번 실행해서 기본 config를 생성한 뒤 가져오려면 survival bootstrap을
사용합니다.

```bash
./ops/scripts/workbench.sh bootstrap survival --manifest survival
./ops/scripts/workbench.sh run-local bootstrap survival --accept-eula --timeout-seconds 240
./ops/scripts/workbench.sh import-bootstrap survival plugins/BetterStructures --dry-run
./ops/scripts/workbench.sh import-bootstrap survival plugins/BetterStructures
```

또는 bootstrap 준비, 서버 실행, config import까지 한 번에 처리할 수 있습니다.

```bash
./ops/scripts/config.sh init survival better-structures --dry-run
./ops/scripts/config.sh init survival better-structures
```

서버 실행을 건너뛰고 기존 bootstrap config만 가져오려면:

```bash
./ops/scripts/config.sh init survival better-structures --no-run --dry-run
./ops/scripts/config.sh init survival better-structures --no-run
```

직접 작성할 config가 있다면 같은 상대 경로에 파일을 두면 됩니다.

```text
ops/templates/survival/plugins/BetterStructures/config.yml
```

빌드 확인:

```bash
./ops/scripts/server.sh check survival plugins/BetterStructures
```

## templates 수정 후 deploy에 반영

templates에서 config를 수정한 뒤 deploy 서버에 적용할 때:

```bash
./ops/scripts/server.sh check survival plugins/EcoItems
./ops/scripts/server.sh build survival plugins/EcoItems
```

서버를 재시작하지 않고 플러그인 reload로 반영할 수 있는 config라면:

```bash
./ops/scripts/live-config.sh push EcoItems survival --dry-run
./ops/scripts/live-config.sh push EcoItems survival
```

여러 플러그인 config를 한 번에 적용할 수도 있습니다.

```bash
./ops/scripts/server.sh check survival plugins/EcoItems plugins/EcoCollections plugins/Talismans
./ops/scripts/server.sh build survival plugins/EcoItems plugins/EcoCollections plugins/Talismans
```

핫 리로드 명령은 플러그인 이름을 먼저 씁니다. 서버를 생략하면 compose에 정의된
대상 서버 전체에 적용합니다.

```bash
./ops/scripts/live-config.sh push EcoItems
./ops/scripts/live-config.sh push EcoItems survival
```





