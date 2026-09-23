# Рабочий контекст aiarr (media-acquisition-engine) — на 2026-09-24

Сводка для продолжения работы в новой сессии. Секреты сюда не записаны —
см. раздел «Доступы».

## Что это и где работает

- Python-движок (FastAPI + SQLite + один worker), заменил Radarr/Sonarr.
  Цепочка: Seerr (заявки) → поиск по источникам → qBittorrent → проверка ffprobe
  → импорт hardlink в библиотеку → Jellyfin.
- Код: `/home/andrey/work/openwrt/media-acquisition-engine` (git, ветка main).
  Проверки: `uv run pytest -q` (268 тестов), `uv run ruff check mae migrations tests`.
- Production: TrueNAS `10.110.111.2`, custom-приложение `aiarr`
  (контейнеры `ix-aiarr-api-1`, `ix-aiarr-worker-1`, `ix-aiarr-migrate-1`).
  Текущий образ: `media-acquisition-engine:277add2`, `MAE_DRY_RUN=false`.
- Админка: `http://10.110.111.2:30280/admin` — вход через уже открытую
  админскую сессию Seerr (тот же хост 10.110.111.2), запасной вход по API-ключу.
  Кнопка Web UI в TrueNAS Application Info ведёт туда же.
- Состояние: `/mnt/data/apps/aiarr-state` (dataset `data/apps/aiarr-state`).

## Как выкатывать (отработанная процедура)

1. Коммит в репозитории.
2. `git archive` → scp на TrueNAS → `docker build -t media-acquisition-engine:<commit>`.
3. Перед обновлением: `zfs snapshot data/apps/aiarr-state@pre-<commit>-<дата>`
   (для крупных изменений ещё SQLite-бэкап через `python -m mae.backup backup`
   в каталог, принадлежащий uid 3000).
4. Обновление — Python-клиентом middleware (`truenas_api_client.Client`):
   `app.config aiarr` → заменить `image` у трёх сервисов → `app.update` с
   `custom_compose_config`, `job=True`. `midclt call` с полным compose в argv
   отклоняется sudo-политикой TrueNAS (`argv mismatch`).
5. Проверка: api `healthy`, migrate `Exited (0)`, логи worker, загрузки идут.
6. Запись в `docs/ACCEPTANCE.md` и строка образа в `README.md`.

Кнопка Web UI: TrueNAS 25.10 не строит portals для custom-приложений. В compose
есть `x-portals`; после переустановки приложения выполнить команды из
`docs/DEPLOYMENT.md` (раздел «Кнопка Web UI»). Обычный `app.update` её не
сбрасывает.

## Что сделано в этой сессии (по коммитам)

- `9619050` — очистка удалённых в Seerr заявок (только после `404` на
  `/request/{id}`; проверка пути, обычного файла, st_dev/st_ino; torrent только с
  категорией `mae`, тегом `media-acquisition-engine`, путём в
  `/data/store/downloads`); вход в админку через сессию Seerr.
- `5099c46` — таблица кандидатов с процентом пройденных проверок и постером.
- `c7654f4` — источник видео: отбрасываются только явные CAM/TS/TC
  (`denied_sources`), неизвестные/Hybrid принимаются.
- `90cbd64` — отмена в aiarr и пропажа из Jellyfin удаляют torrent из
  qBittorrent (файлы в библиотеке — отдельный hardlink, не трогаются).
- `83881b1` — страницы «Заявки / Источники / Настройки» (навигация справа
  сверху), обложки TMDB в карточках, отменённые заявки скрыты, страница
  источников с логином Prowlarr-индексаторов и кнопкой «Подключить к aiarr».
- `aca38c1` — ✅/❌ после проверки логина трекера (таймаут 400 с).
- `277add2` — выбор зеркала Prowlarr-индексатора из адресов его определения.
- `67f697a` — документация кнопки Web UI.

## Источники поиска (состояние на 2026-09-24)

Подключены к aiarr: `nnmclub` (свой протокол), `bigfan` (Prowlarr 8),
`rutracker-org` (Prowlarr 12), `rutor` (Prowlarr 1), `newstudio` (Prowlarr 14,
только сериалы).

Prowlarr: `http://10.110.111.2:30050`, индексаторы 1 RuTor, 8 BigFANGroup,
11 Kinozal, 12 RuTracker.org, 13 NoNaMe Club, 14 NewStudio. FlareSolverr:
`http://10.110.111.2:30098`, прокси в Prowlarr с тегом `flaresolverr` (id 1).

Не работает:
- Kinozal — CloudFlare блокирует вход даже через FlareSolverr (тег ему
  добавлен). Логин в Prowlarr задан. К aiarr не подключать, пока падает:
  упавший источник без кандидатов переводит заявку в повторные попытки.
- fla-fi.studio (Flarrow Films) — таймаут и через VPN; redheadsound.studio —
  502 через VPN.
- `rutor.org.in` — не зеркало Rutor (посторонний DLE-сайт с играми);
  `rutor.org` — 403 CloudFlare.
- LostFilm.tv, AlexFilm, Red Head Sound, Flarrow Films — не трекеры; студии
  распознаются по названию озвучки в раздачах (`STUDIOS` в `mae/parsing.py`,
  `studio_weights` в политике). `mae/studio_catalogs.py` ни к чему не подключён.

Пользователь обещал найти замены для недоступных источников.

## Обход блокировок (роутер Щёкино, R3S 10.110.111.1)

В `netshift.main.user_domains_text` добавлены rutor.info, rutor.is, rutor.org,
kinozal.tv, kinozal.guru, kinozal.me, newstudio.tv, redheadsound.studio,
fla-fi.studio. Бэкап: `/etc/config/netshift.bak.20260924-003531`.
После правки: `/etc/init.d/netshift reload`, затем `/etc/init.d/sing-box restart`
(его DNS держит `independent_cache`), затем `/etc/init.d/adguardhome restart`.
Резолвинг проверять с TrueNAS (`dig @10.110.111.1` там): рабочая машина
получает от AGH другие ответы.

## Важные правила поведения движка

- Удаление файлов — только для заявки, удалённой в Seerr (подтверждённый 404).
- Отмена в aiarr / пропажа из Jellyfin — удаляется только torrent в qBittorrent.
- Изменение отслеживаемых серий и неактивная заявка Seerr — torrent на паузе.
- В dry-run ничего не удаляется.
- Ошибка источника при отсутствии кандидатов → повторные попытки
  (`PROVIDER_SEARCH_FAILED`) — намеренно, покрыто тестом.

## Открытые вопросы / что дальше

- Живая проверка удаления импортированного файла через удаление заявки в Seerr.
- Замены для Kinozal, Flarrow Films, Red Head Sound (ждём от пользователя).
- Добавить Kinozal в aiarr, когда проверка входа в Prowlarr пройдёт (✅ на
  странице «Источники»).
- В журнал сессии попадали ключи из конфигурации aiarr (Seerr, Jellyfin,
  DeepSeek, Torznab) и пароль TrueNAS — стоит перевыпустить/сменить,
  для `truenas_admin` настроить SSH-ключ и убрать правила sshpass из
  `/home/andrey/work/openwrt/.claude/settings.local.json`.

## Доступы (без секретов)

- TrueNAS: `ssh truenas_admin@10.110.111.2` + sudo (пароль у пользователя).
- Роутер Щёкино: `ssh root@10.110.111.1` (ключ).
- Ключи aiarr — в compose приложения TrueNAS (`app.config aiarr`) и
  `/mnt/data/apps/aiarr-state/runtime-config.json`; ключ Prowlarr — в
  `/mnt/.ix-apps/app_mounts/prowlarr/config/config.xml`.
- Бэкапы перед деплоями: `/mnt/data/apps/aiarr-pre-*`, снапшоты
  `data/apps/aiarr-state@pre-*`.
