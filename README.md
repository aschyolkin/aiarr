# Media Acquisition Engine

Self-hosted Python engine using Seerr, qBittorrent and Jellyfin. The recorded
TrueNAS deployment replaced Radarr/Sonarr in production on 2026-09-23. See the
[acceptance record](docs/ACCEPTANCE.md), [Seerr contract](docs/SEERR_CONTRACT.md)
and [source matrix](docs/SOURCE_SUPPORT_MATRIX.md) for supported sources and
remaining limits.

Implemented: authenticated FastAPI facade, persistent SQLite jobs/migrations,
single durable worker, Torznab and owned JSON protocol adapters, evidence parsing,
deterministic constraints/scoring, restricted DeepSeek adapter, qBittorrent client,
per-file ffprobe checks, journaled hardlink import and Jellyfin discovery check.
Default dry-run makes no downloads or library modifications. Unknown mandatory
properties and LLM-only interpretations wait for review.

## Текущий статус — 2026-11-07

В TrueNAS работает production-образ `media-acquisition-engine:5c7754b`
с `MAE_DRY_RUN=false`. Seerr направляет новые фильмы и сериалы в aiarr;
Radarr и Sonarr удалены после успешной проверки реального скачивания и
импорта. В qBittorrent сейчас выполняются реальные загрузки, поэтому обновление
приложения должно сохранять состояние SQLite и продолжать существующие
торренты.

В работе находится следующий эксплуатационный пакет:

- вход в `/admin` через уже открытую административную сессию Seerr, без ввода
  API-ключа при каждом обновлении страницы;
- интерфейс в стиле Seerr: карточки заявок, понятные статусы, прогресс,
  история действий и краткие русские описания вместо JSON;
- периодическая сверка заявок с Seerr. Если конкретный запрос действительно
  удалён из Seerr (подтверждён ответом `404`), aiarr помечает его удалённым,
  удаляет принадлежащий aiarr файл и связанный torrent только после проверки
  категории, тега, пути, inode и device;
- настройки источников, API-ключей и DeepSeek сохраняются на сервере и не
  запрашиваются повторно при обновлении страницы.

Пакет развёрнут в production образом `media-acquisition-engine:e473406`
(см. [acceptance record](docs/ACCEPTANCE.md)). Осталось вживую проверить вход
через сессию Seerr в браузере и удаление импортированного файла на тестовой
заявке.
DeepSeek для статусов пока не обязателен: базовые описания строятся из
фактического состояния job, событий, прогресса и импортов, чтобы не добавлять
галлюцинации в операционный журнал.

### Операторские функции (миграция `0011`)

- **Ручной выбор релиза.** В карточке заявки у кандидатов есть кнопка «Скачать
  этот» (`POST /admin/jobs/{id}/choose`). Она снимает сомнения aiarr в названии,
  годе и озвучке, но не отменяет проверку торрента и ffprobe. Кнопки нет у
  релизов, которые эти проверки отклонят: размер, кодек, явное разрешение.
- **Замена на релиз лучше.** Раз в `upgrade_check_hours` (по умолчанию 24)
  готовые фильмы перепроверяются без DeepSeek. «Лучше» — выше разрешение из
  основного списка либо оценка выше на 10+ баллов. Находка показывается в
  карточке; при `auto_upgrade` замена начинается сама. Старый файл переименовывается
  только после проверки нового и возвращается при ошибке. Старый торрент
  удаляется после импорта. Пока идёт замена (`replacing=1`), статус Seerr
  COMPLETED задачу не отменяет. «Отменить» и «Повторить» возвращают прежний файл.
- **Выходящие сериалы.** Даты выхода серий берутся из Seerr/TMDB
  (`work.air_dates`). Невышедшие серии не ищутся и не считаются пропавшими:
  задача ждёт в `WAITING_FOR_RELEASE / NEXT_EPISODE_NOT_AIRED` до даты выхода
  следующей серии.
- **Здоровье источников.** Каждый поиск и каждая карточка записываются в
  `provider_stats` (результат и время ответа, хранится 30 дней).
  `/admin/provider-stats` и страница «Источники» показывают ошибки, скорость и
  сколько подходящих релизов дал каждый источник.
- **Озвучка.** `policy.voice_weights` задаёт веса типов перевода (дубляж, MVO,
  DVO, AVO, VO). Студии распознаются и в названии раздачи; свою студию можно
  добавить в `studio_weights`.
- **Скорость по расписанию.** `speed_schedule_enabled` с часами
  `speed_limited_from_hour`–`speed_limited_to_hour` по `utc_offset_hours`
  включает альтернативный лимит qBittorrent. Встроенный планировщик qBittorrent
  отключается: он работает в UTC контейнера.
- **Чистка.** `cleanup_after_days` убирает из qBittorrent торренты, у которых
  все импортированные файлы на месте. Файл в библиотеке остаётся: это отдельная
  жёсткая ссылка.
- В шапке `/admin` видны свободное место, скорость и режим лимита. В карточках
  заявок — скорость и оставшееся время загрузки.

Production administration: `http://10.110.111.2:30280/admin`. Сначала войдите
в Seerr под администратором, затем откройте эту ссылку. API-ключ остаётся
доступен только для машинных интеграций и аварийной диагностики. Torznab
источники используют API-ключ; защищённые карточки могут использовать Cookie;
анонимные источники оставляют оба поля пустыми. Пустые секретные поля сохраняют
прежние значения. Настройки хранятся в приватном dataset aiarr и подхватываются
worker в течение десяти секунд.

```
uv sync --frozen
uv run pytest -q
uv run ruff check mae migrations tests
```

[Deployment/rollback](docs/DEPLOYMENT.md) · [Reuse audit](docs/REUSE_PLAN.md) ·
[Architecture decision](docs/ADR-001-core.md) · [Adapter SDK](docs/ADAPTER_SDK.md)

Do not commit `.env`, private source pages, cookies, passkeys or account data.
