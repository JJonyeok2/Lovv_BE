# 로컬 개발 DB (Docker) 가이드

dev RDS에 SSM 터널을 뚫는 대신, 로컬에서 MySQL 8.0 컨테이너를 띄워 DB 작업을 한다.
스키마(`schema/aurora_mysql/*.sql`)가 `utf8mb4_0900_ai_ci`(MySQL 8.0+) 이므로 컨테이너도 8.0이어야 하고,
FK가 `users`(001)를 참조하므로 마이그레이션은 `001 → 002 → 003` 순서로 적용돼야 한다.

호스트 포트는 **13306**을 쓴다(로컬에 이미 3306 MySQL이 떠 있어도 충돌하지 않게). 컨테이너 내부는 3306.

## 1. 컨테이너 기동 (마이그레이션 자동 적용)

```bash
docker compose up -d
```

`docker-compose.yml`이 `schema/aurora_mysql/`를 `/docker-entrypoint-initdb.d`로 마운트하므로,
**최초 기동 시(데이터 볼륨이 비어 있을 때)** 001 → 002 → 003 이 파일명 순서대로 자동 실행된다.

- DB명: `lovvdev`
- 접속(호스트에서): `127.0.0.1:13306`, user `root`, password `lovvlocal`

확인:

```bash
docker compose exec mysql mysql -uroot -plovvlocal -e "USE lovvdev; SHOW TABLES;"
```

> 스키마를 바꾼 뒤 처음부터 다시 적용하려면 볼륨을 비우고 재기동한다: `docker compose down -v && docker compose up -d`

## 2. 관리자 테스트용 seed (R-ADMIN / 제공자)

`scripts/seed_dev_admin.sql`이 R-ADMIN 한 명과 R-DATA-PROVIDER 한 명을 넣는다(관리자는 자기 제안을
검토할 수 없어 두 명이 필요). 적용:

PowerShell:

```powershell
Get-Content scripts/seed_dev_admin.sql | docker compose exec -T mysql mysql -uroot -plovvlocal lovvdev
```

bash:

```bash
docker compose exec -T mysql mysql -uroot -plovvlocal lovvdev < scripts/seed_dev_admin.sql
```

로컬용 액세스 토큰은 `scripts/mint_dev_admin_token.py`로 발급한다(서명 시크릿은 env 또는 Secrets Manager).

## 3. dev 헬퍼 스크립트를 로컬 DB로 가리키기

스크립트는 자격증명을 `RDS_PW`(평문)로, 접속을 `RDS_LOCAL_HOST`/`RDS_LOCAL_PORT`/`RDS_DATABASE`로 읽는다.

PowerShell:

```powershell
$env:RDS_USER = "root"
$env:RDS_PW = "lovvlocal"
$env:RDS_LOCAL_HOST = "127.0.0.1"
$env:RDS_LOCAL_PORT = "13306"
$env:RDS_DATABASE = "lovvdev"

python scripts/db_inspect.py            # 스키마/테이블 점검
python scripts/apply_admin_migration.py # 002만 수동 재적용이 필요할 때
```

## 4. 앱 핸들러를 로컬 DB로 직접 붙이기 (env 자격증명)

`MySqlClient`가 자격증명을 **명시 인자 → 환경변수 → Secrets Manager** 순으로 받도록 보강했다.
따라서 `DB_ACCESS_MODE=mysql` + 아래 env만으로 로컬 Docker DB에 바로 붙는다(시크릿 불필요).

PowerShell:

```powershell
$env:DB_ACCESS_MODE = "mysql"
$env:MYSQL_HOST = "127.0.0.1"
$env:MYSQL_PORT = "13306"
$env:MYSQL_DATABASE = "lovvdev"
$env:MYSQL_USER = "root"
$env:MYSQL_PASSWORD = "lovvlocal"
```

- 우선순위: `MYSQL_USER`/`MYSQL_PASSWORD` → `RDS_USER`/`RDS_PW` → 시크릿. 로컬에선 시크릿을 조회하지 않는다.
- 호스트/DB명/포트도 `MYSQL_*` 또는 `RDS_*` 어느 쪽으로든 줄 수 있다(`MYSQL_HOST`가 우선).
- `local_admin_server.py`처럼 핸들러를 띄우는 스크립트에 위 env를 주면 실제 로컬 DB로 동작한다.
  (단위 테스트는 여전히 InMemory 저장소를 쓰며 DB가 필요 없다.)

## 5. 정리

```bash
docker compose down       # 컨테이너만 정지 (데이터 유지)
docker compose down -v    # 데이터까지 삭제 (다음 기동 때 마이그레이션 재적용)
```
