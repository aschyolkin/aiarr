"""Public login shell; all operational data comes from authenticated endpoints."""

ADMIN_HTML = '''<!doctype html>
<html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>aiarr — администрирование</title>
<style>
body{font:16px system-ui;margin:2rem auto;padding:0 1rem;max-width:1100px;background:#111827;color:#e5e7eb}
input,button,select,textarea{font:inherit;padding:.5rem;margin:.25rem;border-radius:.3rem;background:#1f2937;color:#e5e7eb;border:1px solid #4b5563}
button{cursor:pointer}button:disabled{cursor:default;opacity:.5}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:.6rem;border-bottom:1px solid #374151}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#1f2937;padding:1rem}
#status{min-height:1.5em}a{color:#93c5fd}details{margin:1rem 0}section{overflow-x:auto}
fieldset{border:1px solid #374151;border-radius:.5rem;margin:1rem 0;padding:1rem}
legend{font-weight:700}label.field{display:flex;flex-direction:column;gap:.25rem;min-width:210px;flex:1}
.grid{display:flex;flex-wrap:wrap;gap:.75rem}.hint{color:#9ca3af;font-size:.9rem}
textarea{width:95%;min-height:5rem}#config-status{min-height:1.4em}
.summary{padding:1rem;background:#1f2937;border-radius:.5rem}.timeline{padding-left:1.6rem}
.timeline li{padding:.35rem .3rem .7rem;border-left:2px solid #4b5563;padding-left:1rem}
.timeline time{display:block;color:#9ca3af;font-size:.85rem}.progress{width:100%;height:1rem}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:1rem}
.card{background:#1f2937;border:1px solid #374151;border-radius:.75rem;padding:1rem;cursor:pointer;text-align:left;margin:0}
.card:hover,.card:focus{border-color:#6366f1}.card h3{margin:.1rem 0 .5rem;font-size:1.05rem}
.badge{display:inline-block;padding:.15rem .6rem;border-radius:999px;font-size:.8rem;font-weight:600;background:#374151}
.ok{background:#065f46}.work{background:#3730a3}.warn{background:#92400e}.bad{background:#991b1b}
.muted{color:#9ca3af;font-size:.9rem;margin:.4rem 0 0}
.cand{font-size:.9rem}.cand td,.cand th{padding:.45rem;vertical-align:top}.cand tr.fit td{background:#064e3b33}
.cand tr.chosen td{background:#065f4666}.cand .pct{white-space:nowrap;font-weight:600}.cand .title{overflow-wrap:anywhere;min-width:14rem}
.cand td.mono{font-family:ui-monospace,monospace;font-size:.85rem}
.cand img{width:64px;max-height:96px;object-fit:cover;border-radius:.3rem;display:block}
nav.top{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;margin-bottom:1rem}nav.top .spacer{flex:1}
nav.top a{padding:.45rem .8rem;border-radius:.4rem;text-decoration:none;color:#e5e7eb;background:#1f2937;border:1px solid #374151}
nav.top a.active{background:#4338ca;border-color:#6366f1}
.card{display:flex;gap:.8rem;align-items:flex-start}.card .body{flex:1;min-width:0}
.poster{width:72px;height:108px;flex:none;border-radius:.4rem;background:#374151 center/cover no-repeat;display:flex;align-items:center;justify-content:center;font-size:1.6rem;color:#6b7280}
.login{display:flex;flex-wrap:wrap;gap:.25rem;align-items:center}.login input{width:9rem}
.notice{padding:.8rem 1rem;border-radius:.5rem;background:#1e3a8a55;border:1px solid #3b82f6;margin:.8rem 0}
.notice.warn{background:#92400e44;border-color:#d97706}.cand button{margin:.3rem 0 0;padding:.3rem .6rem;font-size:.85rem}
.weights td{padding:.3rem}.weights input{width:5rem}.stat-ok{color:#6ee7b7}.stat-bad{color:#fca5a5}
#header-info{display:flex;flex-wrap:wrap;gap:.4rem 1rem}
</style>
<h1>aiarr</h1><p>Очередь, доказательства выбора и диагностика импорта</p>
<div id="login" hidden><p>Войдите в Seerr под администратором в этом браузере и обновите страницу.</p>
<details><summary>Аварийный вход по API-ключу</summary><form id="key-login">
<label>API-ключ <input id="key" type="password" autocomplete="off" required></label><button>Войти</button></form></details></div>
<p id="status" role="status"></p>
<main id="dashboard" hidden>
<nav class="top"><span id="header-info" class="muted"><span id="mode"></span><span id="disk"></span><span id="speed"></span></span><span class="spacer"></span>
<a href="#jobs" data-page="jobs">Заявки</a><a href="#sources" data-page="sources">Источники</a>
<a href="#settings" data-page="settings">⚙ Настройки</a>
<button id="refresh" type="button">Обновить</button><button id="logout" type="button">Выйти</button></nav>
<section id="page-jobs" class="page">
<div id="jobs" class="cards"></div>
<section id="inspection" hidden><h2 id="job-title"></h2>
<p id="job-summary" class="summary"></p><div id="download-panel" hidden>
<label for="download-progress">Загрузка: <span id="download-text"></span></label>
<progress id="download-progress" class="progress" max="100" value="0"></progress></div>
<div id="upgrade-panel" class="notice" hidden><span id="upgrade-text"></span>
<div><button id="upgrade-apply" type="button">Заменить на этот релиз</button><button id="upgrade-abort" type="button">Отменить замену</button></div></div>
<h3>Ход заказа</h3><ol id="timeline" class="timeline"></ol>
<div id="episodes-panel" hidden><h3>Эпизоды</h3><p id="episode-summary"></p><ul id="episode-list"></ul></div>
<button id="retry">Повторить попытку</button><button id="cancel">Отменить запрос</button>
<p class="hint">Отмена в aiarr и пропажа файла из Jellyfin удаляют закачку из qBittorrent (уже добавленные в библиотеку файлы не трогаются — это отдельная жёсткая ссылка). Удаление заявки в Seerr удаляет ещё и файлы этой заявки, после проверки владельца.</p>
<details open><summary>Кандидаты и причины выбора</summary><p id="cand-summary" class="hint"></p>
<section><table class="cand"><thead><tr><th></th><th>Релиз</th><th>Источник</th><th>Качество</th><th>Кодек</th><th>Размер</th>
<th>Сиды</th><th>Озвучка / студия</th><th>Вывод</th></tr></thead><tbody id="candidates"></tbody></table></section>
<p class="hint">Кнопка «Скачать этот» выбирает релиз вручную, даже если aiarr сомневается в названии, годе или озвучке. Скачанный файл всё равно проверяется ffprobe по профилю качества (кодек, разрешение, русская дорожка). Для готового фильма выбранный релиз заменит файл в библиотеке только после проверки.</p></details>
<details><summary>Технические подробности</summary>
<details><summary>Кандидаты, evidence и причины выбора (JSON)</summary><pre id="decision"></pre></details>
<details><summary>Выбранный релиз</summary><pre id="candidate"></pre></details>
<details><summary>События и импорт</summary><pre id="history"></pre></details></details>
</section>
<details><summary>Профиль качества</summary><pre id="profiles"></pre></details>
<details><summary>Метрики</summary><pre id="metrics"></pre></details>
</section>
<form id="config-form">
<section id="page-sources" class="page" hidden><h2>Источники поиска</h2>
<p id="sources-summary" class="hint"></p>
<h3>Здоровье источников за 7 дней</h3>
<section><table class="cand"><thead><tr><th>Источник</th><th>Поисков</th><th>Ответ поиска</th><th>Карточек</th><th>Ответ карточки</th>
<th>Ошибки</th><th>Последний поиск</th><th>Подходящих / выбрано</th></tr></thead><tbody id="provider-health"></tbody></table></section>
<p class="hint">«Подходящих» — сколько релизов этого источника прошли все проверки в текущих заявках; «выбрано» — сколько из них скачано.</p>
<h3>Трекеры и студии</h3>
<section><table class="cand"><thead><tr><th>Источник</th><th>Как ищем</th><th>Состояние</th><th>Вход</th></tr></thead>
<tbody id="known-sources"></tbody></table></section>
<p class="hint">Логин и пароль RuTracker.org и Kinozal.tv хранятся в Prowlarr: aiarr передаёт их туда, Prowlarr сразу проверяет вход на сайте. Пароль после сохранения не показывается. Зеркало трекера из Prowlarr выбирается из адресов, которые знает Prowlarr для этого трекера; адрес источника, который aiarr опрашивает сам (протоколы nnmclub/rutor), меняется в поле URL ниже.</p>
<h3>Подключённые к aiarr</h3>
<section><table class="cand"><thead><tr><th>Имя</th><th>Протокол</th><th>Адрес</th><th>Доступ</th>
<th>Карточка релиза</th><th>Интервал, сек</th></tr></thead><tbody id="sources"></tbody></table></section>
<fieldset><legend>Источники релизов</legend>
<p class="hint">Анонимный трекер: оставьте ключ и Cookie пустыми. Для Torznab/Prowlarr укажите API-ключ; для закрытой карточки источника укажите Cookie и хост карточки.</p>
<div id="provider-list"></div><button id="add-provider" type="button">Добавить источник</button></fieldset>
<button type="submit">Сохранить источники</button>
</section>
<section id="page-settings" class="page" hidden><h2>Настройки aiarr</h2>
<p class="hint">Изменения сохраняются в состоянии aiarr и применяются к новым попыткам обработки. Пустые поля ключей сохраняют прежние значения.</p>
<fieldset><legend>Качество</legend><div class="grid">
<label class="field">Название профиля<input id="policy-version" required></label>
<label class="field">Максимальный размер, ГиБ<input id="policy-size" type="number" min="1" step="1" required></label>
<label class="field">Максимальный размер файла, ГиБ<input id="policy-file-size" type="number" min="1" step="1" required></label>
<label class="field">Разрешения<select id="policy-resolution" multiple size="2"><option value="2160">2160p</option><option value="1080">1080p</option></select></label>
<label class="field">Если нет подходящих — допускать<select id="policy-fallback-resolution" multiple size="2"><option value="720">720p</option></select></label>
<label class="field">Видеокодеки<select id="policy-codecs" multiple size="2"><option value="H.265">H.265</option><option value="H.264">H.264</option></select></label>
<label class="field"><span>Русская аудиодорожка обязательна</span><input id="policy-russian" type="checkbox"></label>
</div><label class="field">Приоритеты источников (JSON)<textarea id="policy-sources"></textarea></label>
<p class="hint">Источник без явной метки CAM/TS/TC (например «Hybrid» или вообще без пометки) принимается к рассмотрению, а не отбрасывается. Отбрасываются только явные экранки и телесинк/телесине.</p>
<label class="field">Запрещённые источники (JSON-список)<textarea id="policy-denied-sources"></textarea></label>
<h4>Приоритеты озвучки</h4><p class="hint">Баллы прибавляются к оценке релиза: при прочих равных выбирается озвучка с бо́льшим весом. Для сравнения: 2160p даёт +30, 1080p +10, H.265 +10, WEB/Blu-ray +40.</p>
<div class="grid"><table class="weights"><thead><tr><th>Тип перевода</th><th>Баллы</th></tr></thead><tbody id="voice-weights"></tbody></table>
<table class="weights"><thead><tr><th>Студия</th><th>Баллы</th></tr></thead><tbody id="studio-weights"></tbody></table></div>
<div class="login"><input id="new-studio" placeholder="другая студия"><button id="add-studio" type="button">Добавить студию</button></div>
<p class="hint">Студия распознаётся в названии раздачи и в строках «Аудио»/«Перевод» описания.</p></fieldset>
<fieldset><legend>Улучшение качества</legend><div class="grid">
<label class="field">Искать релиз лучше для готовых фильмов, раз в N часов (0 — не искать)<input id="upgrade_check_hours" type="number" min="0" max="2160"></label>
<label class="field"><span>Заменять автоматически</span><input id="auto_upgrade" type="checkbox"></label></div>
<p class="hint">«Лучше» — выше разрешение из основного списка или на 10+ баллов выше оценка (например, дубляж вместо многоголосой). Без автозамены найденный релиз показывается в карточке фильма с кнопкой «Заменить».</p></fieldset>
<fieldset><legend>Скорость qBittorrent по расписанию</legend><div class="grid">
<label class="field"><span>Ограничивать скорость по расписанию</span><input id="speed_schedule_enabled" type="checkbox"></label>
<label class="field">Ограничивать с, ч<input id="speed_limited_from_hour" type="number" min="0" max="23"></label>
<label class="field">до, ч<input id="speed_limited_to_hour" type="number" min="0" max="23"></label>
<label class="field">Загрузка в это время, МиБ/с<input id="speed_dl_mib" type="number" min="0.1" step="0.1"></label>
<label class="field">Отдача в это время, МиБ/с<input id="speed_ul_mib" type="number" min="0.1" step="0.1"></label>
<label class="field">Часовой пояс, UTC+<input id="utc_offset_hours" type="number" min="-12" max="14"></label></div>
<p class="hint">Например, с 8 до 1 — днём и вечером торренты не забивают канал, ночью качают на полной скорости. Используется «альтернативный лимит» qBittorrent; встроенный планировщик qBittorrent aiarr отключает, чтобы они не спорили.</p></fieldset>
<fieldset><legend>Чистка раздач</legend><div class="grid">
<label class="field">Убирать из qBittorrent через N дней после загрузки (0 — не убирать)<input id="cleanup_after_days" type="number" min="0" max="3650"></label></div>
<p class="hint">Файл в библиотеке — отдельная жёсткая ссылка и остаётся. Место на диске это почти не освобождает (данные общие), зато раздача больше не висит в списке. Убираются только торренты, все импортированные файлы которых на месте.</p></fieldset>
<fieldset><legend>Подключения</legend><div class="grid">
<label class="field">qBittorrent URL<input id="qbit_url" type="url"></label>
<label class="field">qBittorrent пользователь<input id="qbit_username"></label>
<label class="field">qBittorrent пароль<input id="qbit_password" type="password" autocomplete="new-password"></label>
<label class="field">Jellyfin URL<input id="jellyfin_url" type="url"></label>
<label class="field">Jellyfin API-ключ<input id="jellyfin_key" type="password" autocomplete="new-password"></label>
<label class="field">Seerr URL<input id="seerr_url" type="url"></label>
<label class="field">Seerr API-ключ<input id="seerr_key" type="password" autocomplete="new-password"></label>
<label class="field">TMDB API-ключ<input id="tmdb_key" type="password" autocomplete="new-password"></label>
<label class="field">DeepSeek API-ключ<input id="deepseek_key" type="password" autocomplete="new-password"></label>
</div></fieldset>
<fieldset><legend>Очередь и DeepSeek</legend><div class="grid">
<label class="field">Одновременные загрузки<input id="max_downloads" type="number" min="1" max="10"></label>
<label class="field">Повторных попыток<input id="max_attempts" type="number" min="1" max="10"></label>
<label class="field">Кандидатов на обогащение<input id="enrichment_limit" type="number" min="1" max="100"></label>
<label class="field">Смена раздачи без скачивания, мин<input id="stall_minutes" type="number" min="1" max="1440"></label>
<label class="field">Скорость запасной раздачи, КиБ/с<input id="backup_kib" type="number" min="1"></label>
<label class="field">Минимум сидов<input id="min_seeders" type="number" min="0"></label>
<label class="field">Минимальная скорость, Мбит/с<input id="min_mbit" type="number" min="0" step="0.1"></label>
<label class="field">Максимальное время загрузки, ч<input id="max_eta_hours" type="number" min="1" step="0.5"></label>
<label class="field">Резерв диска, ГиБ<input id="reserve_gib" type="number" min="0"></label>
<label class="field">Модель DeepSeek<select id="deepseek_model"><option value="deepseek-flash">deepseek-flash</option></select></label>
<label class="field">Вызовов DeepSeek в день<input id="deepseek_daily_calls" type="number" min="0"></label>
<label class="field">Токенов DeepSeek в день<input id="deepseek_daily_tokens" type="number" min="0"></label>
<label class="field">Вызовов DeepSeek на запрос<input id="deepseek_request_calls" type="number" min="0"></label>
<label class="field">Токенов DeepSeek на запрос<input id="deepseek_request_tokens" type="number" min="0"></label>
</div></fieldset>
<button type="submit">Сохранить настройки</button><button id="reload-config" type="button">Отменить изменения</button>
</section>
<p id="config-status" role="status"></p>
</form>
</main>
<script>
'use strict';
let token='', signedIn=false, selected=null, busy=false, config=null;
const byId=id=>document.getElementById(id);
const show=(id,value)=>{byId(id).textContent=JSON.stringify(value,null,2);};
const parse=value=>{try{return JSON.parse(value||'null');}catch{return null;}};
function clearSession(){
 token='';signedIn=false;selected=null;config=null;byId('key').value='';byId('dashboard').hidden=true;
 byId('inspection').hidden=true;byId('login').hidden=false;
 byId('provider-list').replaceChildren();byId('config-form').reset();
 for(const id of ['jobs','sources','known-sources','profiles','metrics','decision','candidate','history','mode','disk','speed','job-title','provider-health'])byId(id).textContent='';
}
async function api(path,method='GET',body=null){
 const headers={'X-Aiarr-UI':'1'};if(token)headers['X-Api-Key']=token;
 if(body!==null)headers['Content-Type']='application/json';
 const response=await fetch(path,{method,headers,body:body===null?undefined:JSON.stringify(body),
  cache:'no-store',redirect:'error',credentials:'same-origin'});
 if(response.status===401){clearSession();throw new Error('Нужен вход в Seerr под администратором.');}
 if(response.status===503)throw new Error('Seerr недоступен, вход проверить нельзя.');
 if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(data.detail||'HTTP '+response.status);}
 return response.json();
}
function providerField(box,title,key,value,type='text'){
 const label=document.createElement('label');label.className='field';label.textContent=title;
 const input=document.createElement(type==='select'?'select':'input');input.dataset.setting=key;
 if(type==='select'){
  for(const option of (key==='protocol'?['nnmclub','torznab','json','rutor']:['html','rutor'])){
   const item=document.createElement('option');item.value=option;item.textContent=option;input.append(item);
  }
 }else input.type=type;
 if(type==='checkbox')input.checked=Boolean(value);
 else if(type==='password'){input.value='';input.placeholder=value?'Сохранён':'Не задан';}
 else input.value=value??'';
 label.append(input);box.append(label);return input;
}
function addProvider(value={}){
 const fieldset=document.createElement('fieldset'),legend=document.createElement('legend');
 legend.textContent=value.name||'Новый источник';fieldset.append(legend);
 const grid=document.createElement('div');grid.className='grid';fieldset.append(grid);
 for(const [title,key,type] of [
  ['Имя','name','text'],['Протокол','protocol','select'],['URL','url','url'],
  ['Хост карточки','source_host','text'],['CSS-селектор карточки','details_selector','text'],
  ['Адаптер карточки','details_adapter','select'],['Кодировка','encoding','text'],
  ['Интервал, сек','interval','number'],['Доверенный шлюз','trusted_gateway','checkbox'],
  ['Доверенный источник','trusted_source','checkbox'],['API-ключ Torznab/Prowlarr','api_key','password'],
  ['Cookie источника','cookie','password']]){
  const initial=type==='password'?value[key+'_configured']:value[key];
  const input=providerField(grid,title,key,initial,type);
  if(key==='interval')input.min='0';
 }
 const remove=document.createElement('button');remove.type='button';remove.textContent='Удалить источник';
 remove.onclick=()=>fieldset.remove();fieldset.append(remove);byId('provider-list').append(fieldset);
}
const VOICES={Dub:'Дубляж',MVO:'Многоголосый (MVO)',DVO:'Двухголосый (DVO)',AVO:'Авторский (AVO)',VO:'Одноголосый / любительский'};
function weightRow(body,key,label,value){
 const tr=document.createElement('tr'),name=document.createElement('td'),cell=document.createElement('td'),input=document.createElement('input');
 name.textContent=label;input.type='number';input.min='-100';input.max='100';input.value=value??0;input.dataset.key=key;
 cell.append(input);tr.append(name,cell);body.append(tr);
}
function renderWeights(p){
 const voices=byId('voice-weights');voices.replaceChildren();
 for(const [key,label] of Object.entries(VOICES))weightRow(voices,key,label,(p.voice_weights||{})[key]);
 const studios=byId('studio-weights');studios.replaceChildren();
 const names=[...new Set([...(config.known_studios||[]),...Object.keys(p.studio_weights||{})])];
 for(const name of names)weightRow(studios,name,name,(p.studio_weights||{})[name]);
}
function readWeights(id){const out={};for(const input of byId(id).querySelectorAll('input[data-key]'))
 out[input.dataset.key]=Math.round(Number(input.value)||0);return out;}
function renderConfig(){
 const s=config.settings,p=s.policy;
 byId('policy-version').value=p.version;byId('policy-size').value=Math.round(p.max_bytes/1073741824);
 byId('policy-file-size').value=Math.round(p.max_file_bytes/1073741824);
 for(const option of byId('policy-resolution').options)option.selected=p.resolutions.includes(Number(option.value));
 for(const option of byId('policy-fallback-resolution').options)option.selected=(p.fallback_resolutions||[]).includes(Number(option.value));
 for(const option of byId('policy-codecs').options)option.selected=p.codecs.includes(option.value);
 byId('policy-russian').checked=p.russian_required;
 byId('policy-sources').value=JSON.stringify(p.source_weights,null,2);
 byId('policy-denied-sources').value=JSON.stringify(p.denied_sources,null,2);
 renderWeights(p);
 byId('provider-list').replaceChildren();for(const provider of s.providers)addProvider(provider);
 for(const key of ['qbit_url','qbit_username','jellyfin_url','seerr_url','deepseek_model',
  'max_downloads','max_attempts','enrichment_limit','deepseek_daily_calls','deepseek_daily_tokens',
  'deepseek_request_calls','deepseek_request_tokens'])byId(key).value=s[key];
 byId('reserve_gib').value=Math.round(s.reserve_bytes/1073741824);
 byId('stall_minutes').value=Math.round(s.stall_seconds/60);
 byId('backup_kib').value=Math.round(s.backup_download_limit/1024);
 byId('min_seeders').value=s.min_seeders;
 byId('min_mbit').value=s.min_download_speed*8/1e6;byId('max_eta_hours').value=s.max_eta_seconds/3600;
 for(const key of ['upgrade_check_hours','speed_limited_from_hour','speed_limited_to_hour','utc_offset_hours','cleanup_after_days'])byId(key).value=s[key];
 for(const key of ['auto_upgrade','speed_schedule_enabled'])byId(key).checked=Boolean(s[key]);
 byId('speed_dl_mib').value=Math.round(s.speed_limit_download/104857.6)/10;byId('speed_ul_mib').value=Math.round(s.speed_limit_upload/104857.6)/10;
 for(const key of ['qbit_password','jellyfin_key','seerr_key','tmdb_key','deepseek_key']){
  byId(key).value='';byId(key).placeholder=s[key+'_configured']?'Сохранён':'Не задан';
 }
 byId('config-status').textContent='';
}
function collectConfig(){
 const previous=config.settings;
 const selectedValues=id=>[...byId(id).selectedOptions].map(option=>option.value);
 const policy={...previous.policy,version:byId('policy-version').value.trim(),
  max_bytes:Number(byId('policy-size').value)*1073741824,
  max_file_bytes:Number(byId('policy-file-size').value)*1073741824,
  resolutions:selectedValues('policy-resolution').map(Number),
  fallback_resolutions:selectedValues('policy-fallback-resolution').map(Number),codecs:selectedValues('policy-codecs'),
  russian_required:byId('policy-russian').checked,
  source_weights:JSON.parse(byId('policy-sources').value),
  denied_sources:JSON.parse(byId('policy-denied-sources').value),
  studio_weights:readWeights('studio-weights'),voice_weights:readWeights('voice-weights')};
 if(!policy.resolutions.length||!policy.codecs.length)throw new Error('Выберите разрешение и видеокодек.');
 const providers=[...byId('provider-list').children].map(box=>{
  const provider={};for(const input of box.querySelectorAll('[data-setting]')){
   const key=input.dataset.setting;provider[key]=input.type==='checkbox'?input.checked:
    key==='interval'?Number(input.value):input.value.trim();
  }return provider;
 });
 const values={policy,providers,reserve_bytes:Number(byId('reserve_gib').value)*1073741824,
  stall_seconds:Number(byId('stall_minutes').value)*60,
  backup_download_limit:Number(byId('backup_kib').value)*1024,min_seeders:Number(byId('min_seeders').value),
  min_download_speed:Math.round(Number(byId('min_mbit').value)*1e6/8),
  max_eta_seconds:Math.round(Number(byId('max_eta_hours').value)*3600),
  speed_limit_download:Math.round(Number(byId('speed_dl_mib').value)*1048576),
  speed_limit_upload:Math.round(Number(byId('speed_ul_mib').value)*1048576)};
 for(const key of ['upgrade_check_hours','speed_limited_from_hour','speed_limited_to_hour','utc_offset_hours','cleanup_after_days'])values[key]=Number(byId(key).value);
 for(const key of ['auto_upgrade','speed_schedule_enabled'])values[key]=byId(key).checked;
 for(const key of ['qbit_url','qbit_username','jellyfin_url','seerr_url','deepseek_model',
  'qbit_password','jellyfin_key','seerr_key','tmdb_key','deepseek_key'])values[key]=byId(key).value.trim();
 for(const key of ['max_downloads','max_attempts','enrichment_limit','deepseek_daily_calls',
  'deepseek_daily_tokens','deepseek_request_calls','deepseek_request_tokens'])values[key]=Number(byId(key).value);
 return values;
}
const states={REQUESTED:'Заявка принята',SEARCHING:'Поиск релизов',ENRICHING:'Проверка описаний релизов',
 SELECTED:'Подходящий релиз выбран',DOWNLOADING:'Загрузка',VERIFYING:'Проверка видео',
 IMPORTING:'Добавление в библиотеку',AVAILABLE:'Доступно в Jellyfin',
 WAITING_FOR_RELEASE:'Ожидание релиза',NEEDS_REVIEW:'Требуется проверка',
 RETRY_SCHEDULED:'Повторная попытка запланирована',AUTH_REQUIRED:'Нужен доступ к источнику',
 FAILED:'Ошибка обработки',CANCELLED:'Заявка отменена'};
const tone={AVAILABLE:'ok',DOWNLOADING:'work',VERIFYING:'work',IMPORTING:'work',SEARCHING:'work',ENRICHING:'work',
 SELECTED:'work',REQUESTED:'work',WAITING_FOR_RELEASE:'warn',RETRY_SCHEDULED:'warn',NEEDS_REVIEW:'bad',
 AUTH_REQUIRED:'bad',FAILED:'bad'};
const reasons={PARTIAL_SEASON:'Часть серий уже доступна, остальные ещё ищутся',
 NO_PROVEN_CANDIDATE:'Подходящий релиз пока не подтверждён',
 WAITING_FOR_JELLYFIN:'Файл добавлен, ожидается сканирование Jellyfin',
 ALREADY_IN_LIBRARY:'Уже есть в Jellyfin',DRY_RUN_SELECTED:'Релиз найден, загрузка отключена',
 INSUFFICIENT_SPACE:'Недостаточно места на диске',DOWNLOAD_STALLED_NO_PEERS:'Раздачи не качаются — выбираются следующие релизы',BACKUP_PROMOTED:'Основная раздача стояла — качается запасная',DOWNLOAD_TOO_SLOW:'Раздача слишком медленная — выбирается другая',DOWNLOAD_SLOW_NO_ALTERNATIVE:'Качается медленно, других подходящих раздач нет',DOWNLOAD_QUEUE_LIMIT:'Очередь загрузок заполнена',
 VIDEO_RESOLUTION_BELOW_POLICY:'Разрешение видео ниже заданного',RUSSIAN_AUDIO_NOT_TAGGED:'Не подтверждена русская аудиодорожка',DOWNLOAD_REMOVED:'Закачка удалена из qBittorrent',
 SEERR_REQUEST_DELETED:'Заявка удалена в Seerr, файлы удаляются',SEERR_FILE_REMOVED:'Файл удалён из библиотеки',
 SEERR_CLEANUP_DONE:'Файлы и торрент удалены',SEERR_CLEANUP_RETRY:'Удаление повторится позже',
 IMPORT_INODE_MISMATCH:'Файл в библиотеке заменён вручную — не удалён',
 TORRENT_OWNERSHIP_TAG_MISSING:'Торрент без метки aiarr — не удалён',
 TORRENT_OUTSIDE_MANAGED_PATH:'Торрент вне каталога загрузок — не удалён',
 ADMIN_CANCELLED:'Отменено в aiarr',SEERR_REREQUESTED:'Запрошено повторно',
 PROVIDER_SEARCH_FAILED:'Источник не ответил на поиск',PROVIDER_ENRICHMENT_FAILED:'Не удалось открыть карточку релиза',
 LIBRARY_ITEM_MISSING:'Файл пропал из Jellyfin',EPISODE_MONITORING_CHANGED:'Изменён список отслеживаемых серий',
 NO_MONITORED_EPISODES:'Нет отслеживаемых серий',NEXT_EPISODE_NOT_AIRED:'Следующая серия ещё не вышла',
 MANUAL_RELEASE_CHOSEN:'Релиз выбран вручную',MANUAL_RELEASE_NOT_FOUND:'Выбранный релиз больше не находится в источнике',
 MANUAL_RELEASE_SOURCE_GONE:'Источник выбранного релиза отключён',MANUAL_RELEASE_NOT_DOWNLOADABLE:'Выбранный релиз не пройдёт проверку',
 UPGRADE_AVAILABLE:'Найден релиз лучше',UPGRADE_STARTED:'Идёт замена на релиз лучше',UPGRADE_CANCELLED:'Замена отменена, файл прежний',
 UPGRADED:'Файл заменён на релиз лучше',SEEDING_CLEANED:'Раздача убрана из qBittorrent, файл в библиотеке остался',
 VIDEO_RESOLUTION_DIFFERS_FROM_RELEASE:'Разрешение файла не совпало с заявленным в раздаче',ALREADY_IN_LIBRARY:'Уже есть в Jellyfin'};
const candidateReasons={NOT_VIDEO_RELEASE:'не видео (книга, музыка, игра)',VIDEO_CODEC_NOT_ALLOWED:'кодек не из списка',FILE_TOO_LARGE:'файл больше лимита',IDENTITY_MISMATCH:'другое название',DOWNLOAD_STALLED_NO_PEERS:'не качалась или слишком медленная',YEAR_UNVERIFIED:'год не совпадает или не указан',
 NOT_A_MOVIE:'это серия, а не фильм',SEASON_MISMATCH:'не тот сезон',EPISODE_MISMATCH:'не те серии',
 SOURCE_NOT_ALLOWED:'источник видео явно плохого качества (экранка/телесинк/телесине)',RESOLUTION_NOT_ALLOWED:'разрешение не подходит',
 CODEC_EVIDENCE_CONFLICT:'противоречивые данные о кодеке',TOO_LARGE:'слишком большой размер',
 desired_episode_coverage:'неизвестен список серий',compatible_video_codec:'кодек не подтверждён',
 size:'размер неизвестен',russian_audio:'русская дорожка не подтверждена',
 llm_interpretation_requires_review:'данные только от DeepSeek — нужна ручная проверка'};
const CHECKS=['IDENTITY_MISMATCH','YEAR_OR_SEASON','SOURCE_NOT_ALLOWED','RESOLUTION_NOT_ALLOWED',
 'compatible_video_codec','size','russian_audio'];
function candidatePercent(c){
 const bad=new Set([...(c.reasons||[]),...(c.unknown||[])]);
 if(bad.has('YEAR_UNVERIFIED')||bad.has('SEASON_MISMATCH')||bad.has('EPISODE_MISMATCH')||bad.has('NOT_A_MOVIE'))bad.add('YEAR_OR_SEASON');
 if(bad.has('TOO_LARGE')||bad.has('FILE_TOO_LARGE'))bad.add('size');if(bad.has('CODEC_EVIDENCE_CONFLICT')||bad.has('VIDEO_CODEC_NOT_ALLOWED'))bad.add('compatible_video_codec');
 return Math.round(100*CHECKS.filter(x=>!bad.has(x)).length/CHECKS.length);
}
const sizeText=b=>typeof b==='number'?(b/1073741824).toFixed(b>=10737418240?0:1)+' ГиБ':'—';
const scoreOf=c=>Object.values(c.score||{}).reduce((n,v)=>n+v,0);
const SCORE_NAMES={source:'источник',resolution:'разрешение',codec:'кодек',studio:'студия',voice:'озвучка'};
const scoreText=c=>Object.entries(c.score||{}).filter(([,v])=>v).map(([k,v])=>SCORE_NAMES[k]+' '+(v>0?'+':'')+v).join(', ');
const UNCHOOSABLE=new Set(['TOO_LARGE','FILE_TOO_LARGE','VIDEO_CODEC_NOT_ALLOWED','NOT_VIDEO_RELEASE']);
const choosable=c=>!(c.reasons||[]).some(x=>UNCHOOSABLE.has(x))&&!((c.reasons||[]).includes('RESOLUTION_NOT_ALLOWED')&&c.parsed?.screen_size);
let currentJob=null;
async function chooseRelease(c){
 const r=c.release||{},replacing=currentJob&&(currentJob.state==='AVAILABLE'||currentJob.replacing);
 if(!confirm((replacing?'Заменить файл в библиотеке на релиз':'Скачать релиз')+' «'+r.title+'»?'+
  (replacing?' Старый файл останется, пока новый не пройдёт проверку.':' Текущая закачка этой заявки будет удалена.')))return;
 await api('/admin/jobs/'+selected+'/choose','POST',{provider:r.provider,release_id:r.release_id});await refresh();
}
function renderCandidates(decision,chosen){
 const list=(decision?.candidates||[]).map(c=>({c,fit:!c.reasons?.length&&!c.unknown?.length,pct:candidatePercent(c)}));
 list.sort((a,b)=>(b.fit-a.fit)||(scoreOf(b.c)-scoreOf(a.c))||(b.pct-a.pct));
 const body=byId('candidates');body.replaceChildren();
 const chosenId=chosen?.release?chosen.release.provider+':'+chosen.release.release_id:null;
 const fits=list.filter(x=>x.fit).length;
 byId('cand-summary').textContent=(decision?.partial?'Идёт поиск… ':'')+(list.length?'Проверено '+list.length+' релизов, подходят '+fits+
  '. Процент — доля пройденных проверок: название, год/сезон, источник, разрешение, кодек, размер, русская дорожка.'
  :'Кандидатов пока нет.');
 for(const {c,fit,pct} of list){
  const r=c.release||{},p=c.parsed||{},claims=c.claims||[];
  const tr=document.createElement('tr');const isChosen=chosenId===r.provider+':'+r.release_id;
  tr.className=isChosen?'chosen':fit?'fit':'';
  const audio=[...new Set(claims.filter(x=>x.field==='audio_language').map(x=>x.value==='rus'?'русская':x.value))];
  const voices=[...new Set(claims.filter(x=>x.field==='voice').map(x=>VOICES[x.value]||x.value))];
  const studios=[...new Set(claims.filter(x=>x.field==='studio').map(x=>x.value))];
  const codec=p.video_codec||claims.find(x=>x.field==='video_codec')?.value;
  const problems=[...(c.reasons||[]),...(c.unknown||[])].map(x=>candidateReasons[x]||x);
  const why=scoreText(c);
  const verdict=isChosen?'✅ Выбран · '+scoreOf(c)+' баллов'+(why?' ('+why+')':''):fit?'✔ Подходит · '+scoreOf(c)+' баллов'+(why?' ('+why+')':''):'✖ '+problems.join('; ');
  const cells=[r.title||'',r.provider||'',[p.screen_size,p.source].filter(Boolean).join(' · ')||'—',codec||'—',
   sizeText(r.size),r.seeders??'—',[...audio,...voices,...studios].join(', ')||'—'];
  const pic=document.createElement('td');
  if(c.image_url&&/^https:/.test(c.image_url)){const img=document.createElement('img');img.src=c.image_url;
   img.alt='';img.loading='lazy';img.referrerPolicy='no-referrer';img.onerror=()=>img.remove();pic.append(img);}
  tr.append(pic);
  cells.forEach((value,i)=>{const td=document.createElement('td');td.textContent=String(value);
   if(i===0){td.className='title';if(r.details_url&&/^https?:/.test(r.details_url)){td.textContent='';
    const a=document.createElement('a');a.href=r.details_url;a.target='_blank';a.rel='noopener noreferrer';
    a.textContent=r.title;td.append(a);}}
   tr.append(td);});
  const td=document.createElement('td');const pctEl=document.createElement('div');pctEl.className='pct';
  pctEl.textContent=pct+'%';const note=document.createElement('div');note.textContent=verdict;td.append(pctEl,note);
  if(!isChosen&&choosable(c)&&currentJob&&!['CANCELLED','VERIFYING','IMPORTING'].includes(currentJob.state)&&
   !(currentJob.state==='AVAILABLE'&&parse(currentJob.work)?.kind==='series')){
   const pick=document.createElement('button');pick.type='button';pick.textContent='Скачать этот';
   pick.onclick=()=>run(()=>chooseRelease(c));td.append(pick);}
  tr.append(td);body.append(tr);
 }
}
const labelState=value=>states[value]||value;
const services={JELLYFIN:'Jellyfin',SEERR:'Seerr',QBITTORRENT:'qBittorrent',UPSTREAM:'Внешний сервис'};
const labelFailure=value=>{const m=/^(JELLYFIN|SEERR|QBITTORRENT|UPSTREAM)_(?:HTTP_([0-9]{3})|(UNREACHABLE)|(TIMEOUT))$/.exec(value||'');if(!m)return null;return services[m[1]]+(m[2]?' ответил ошибкой HTTP '+m[2]:m[3]?' недоступен':' не ответил вовремя')+(m[1]==='UPSTREAM'?'':' — задача ждёт, попытки не расходуются');};
const labelReason=value=>reasons[value]||labelFailure(value)||value||'';
function appendStep(text,created){
 const li=document.createElement('li');li.textContent=text;
 if(created){const time=document.createElement('time');time.dateTime=new Date(created*1000).toISOString();
  time.textContent=new Date(created*1000).toLocaleString('ru-RU');li.append(time);}
 byId('timeline').append(li);
}
function renderProgress(data){
 const job=data.job,work=parse(job.work)||{},decision=parse(job.decision)||{},
  chosen=parse(job.candidate)||((decision.candidates||[]).filter(x=>!x.reasons?.length&&!x.unknown?.length)
   .sort((a,b)=>Object.values(b.score||{}).reduce((n,v)=>n+v,0)-Object.values(a.score||{}).reduce((n,v)=>n+v,0))[0]);
 const episodes=data.episodes||[],wanted=episodes.filter(x=>x.monitored),ready=wanted.filter(x=>x.available),
  missing=wanted.filter(x=>!x.available);
 const air=work.air_dates||{},today=new Date().toISOString().slice(0,10);
 const upcoming=missing.filter(x=>air[x.episode]===''||air[x.episode]>today);
 let summary=labelState(job.state);
 if(work.kind==='series'&&wanted.length)summary+=' · готово '+ready.length+' из '+wanted.length+' серий'+
  (missing.length?' · ожидаются '+missing.map(x=>x.episode).join(', '):'');
 if(job.reason)summary+=' · '+labelReason(job.reason);
 if(['RETRY_SCHEDULED','WAITING_FOR_RELEASE'].includes(job.state)&&job.next_run*1000>Date.now())
  summary+=' · следующая попытка '+new Date(job.next_run*1000).toLocaleString('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});
 if(job.attempts)summary+=' · попыток: '+job.attempts;
 byId('job-summary').textContent=summary;
 const panel=byId('download-panel');panel.hidden=!data.download;
 if(data.download){const d=data.download,pct=Math.round(d.progress*1000)/10;
  byId('download-progress').value=pct;
  byId('download-text').textContent=pct+'% · '+(d.download_speed/1048576).toFixed(1)+' МиБ/с'+
   (d.eta>0&&d.eta<8640000?' · осталось около '+Math.ceil(d.eta/60)+' мин':'');}
 byId('episodes-panel').hidden=work.kind!=='series';
 byId('episode-summary').textContent=ready.length+' готово · '+missing.length+' ожидается';
 byId('episode-list').replaceChildren();for(const ep of wanted){const li=document.createElement('li');
  const date=air[ep.episode];
  li.textContent=ep.episode+' — '+(ep.available?'есть в Jellyfin':date===''?'дата выхода не объявлена':
   date&&date>today?'выйдет '+new Date(date+'T12:00:00').toLocaleDateString('ru-RU'):'ожидается');byId('episode-list').append(li);}
 if(upcoming.length)byId('episode-summary').textContent+=' (из них ещё не вышли: '+upcoming.length+')';
 const upgrade=parse(job.upgrade_candidate),panel2=byId('upgrade-panel');
 panel2.hidden=!(upgrade?.candidate||job.replacing);byId('upgrade-apply').hidden=!upgrade?.candidate||Boolean(job.replacing);
 byId('upgrade-abort').hidden=!job.replacing;
 if(job.replacing)byId('upgrade-text').textContent='Идёт замена файла на релиз лучше. Старый файл остаётся в библиотеке, пока новый не скачается и не пройдёт проверку.';
 else if(upgrade?.candidate)byId('upgrade-text').textContent='Найден релиз лучше: «'+upgrade.candidate.release.title+'» ('+
  upgrade.candidate.release.provider+'). Сейчас '+(upgrade.from.resolution||'?')+'p · '+upgrade.from.score+' баллов → будет '+
  upgrade.to.resolution+'p · '+upgrade.to.score+' баллов ('+scoreText(upgrade.candidate)+').';
 byId('timeline').replaceChildren();const seen=new Set();
 for(const event of data.events||[]){
  const key=event.state+':'+(event.reason||'');if(seen.has(key))continue;seen.add(key);
  appendStep(labelState(event.state)+(event.reason?' · '+labelReason(event.reason):''),event.created);
 }
 if(decision.candidates){const candidates=decision.candidates,eligible=candidates.filter(x=>!x.reasons?.length&&!x.unknown?.length);
  appendStep('Проверено '+candidates.length+' вариантов; требованиям соответствуют '+eligible.length+'.');}
 if(chosen?.release){const facts=[...new Set((chosen.claims||[]).map(x=>x.field+': '+x.value))];
  appendStep('Выбран релиз: '+chosen.release.title+' ('+chosen.release.provider+')'+
   (chosen.parsed?.manual?' — выбран вручную':'')+
   (facts.length?'. Подтверждено: '+facts.join(', '):'. Соответствует заданной политике.'));
  const rivals=(decision.candidates||[]).filter(x=>!x.reasons?.length&&!x.unknown?.length);
  if(rivals.length>1&&!chosen.parsed?.manual)appendStep('Почему он: '+scoreOf(chosen)+' баллов ('+(scoreText(chosen)||'без бонусов')+
   '); других подходящих — '+(rivals.length-1)+'. При равных баллах решают разрешение из основного списка и число сидов.');}
 for(const item of data.imports||[])if(item.state==='PUBLISHED'){
  const release=item.source?.split('/').slice(-2,-1)[0]||'релиз',probe=parse(item.probe)||{};
  const video=(probe.streams||[]).find(x=>x.codec_type==='video'),
   audio=(probe.streams||[]).some(x=>x.codec_type==='audio'&&['rus','ru'].includes(x.tags?.language));
  const facts=[video?.codec_name?.toUpperCase(),video?.width&&video?.height?video.width+'×'+video.height:null,
   audio?'русская дорожка подтверждена':null].filter(Boolean);
  appendStep('Скачан и проверен релиз «'+release+'»'+(facts.length?': '+facts.join(', '):'')+'.');
  appendStep('Файл '+(item.episode||'фильма')+' добавлен в библиотеку.');
 }
 if(job.state==='AVAILABLE'||(data.imports||[]).some(item=>item.state==='PUBLISHED'&&
   (item.episode?ready.some(ep=>ep.episode===item.episode):job.state==='AVAILABLE')))
  appendStep('Jellyfin подтвердил доступные файлы.');
}
async function inspect(id){
 const data=await api('/admin/jobs/'+id);selected=id;currentJob=data.job;
 byId('inspection').hidden=false;byId('job-title').textContent=(parse(data.job.work)?.title||'Задание')+' · #'+id;
 renderProgress(data);
 renderCandidates(parse(data.job.decision),parse(data.job.candidate));
 show('decision',parse(data.job.decision));show('candidate',parse(data.job.candidate));
 show('history',{events:data.events,imports:data.imports});
 byId('retry').disabled=!['FAILED','NEEDS_REVIEW','AUTH_REQUIRED','WAITING_FOR_RELEASE','RETRY_SCHEDULED'].includes(data.job.state);
 byId('cancel').disabled=['AVAILABLE','CANCELLED'].includes(data.job.state);
}
const protocolNames={torznab:'Torznab/Prowlarr',json:'Собственный JSON',nnmclub:'NNM-Club',rutor:'Rutor'};
const OUTCOMES={RATE_LIMITED:'лимит запросов',TEMPORARY_FAILURE:'временный сбой',AUTH_REQUIRED:'нужен вход',
 ACCESS_DENIED:'доступ запрещён',PARSER_BROKEN:'страница не разобрана',ERROR:'ошибка',NO_RESULTS:'пусто'};
function renderHealth(stats){
 const body=byId('provider-health');body.replaceChildren();
 if(!stats.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=8;
  td.textContent='Запросов пока не было — статистика появится после первого поиска.';tr.append(td);body.append(tr);return;}
 for(const s of stats){
  const tr=document.createElement('tr');
  const errors=Object.entries(s.errors).map(([k,v])=>(OUTCOMES[k]||k)+': '+v).join(', ')||'нет';
  const last=s.last_search_outcome==='OK'?'✔ работает':s.last_search_outcome?'✖ '+(OUTCOMES[s.last_search_outcome]||s.last_search_outcome):'—';
  const ms=v=>v==null?'—':v<1000?v+' мс':(v/1000).toFixed(1)+' с';
  const cells=[s.provider,s.searches,ms(s.search_ms),s.cards,ms(s.card_ms),errors,last,s.fit+' / '+s.chosen];
  cells.forEach((value,i)=>{const td=document.createElement('td');td.textContent=String(value);
   if(i===6)td.className=s.last_search_outcome==='OK'?'stat-ok':'stat-bad';
   if(i===6&&s.last_error)td.title='Последняя ошибка: '+(OUTCOMES[s.last_error.outcome]||s.last_error.outcome)+', '+new Date(s.last_error.at*1000).toLocaleString('ru-RU');
   tr.append(td);});
  body.append(tr);
 }
}
function renderSources(sources){
 byId('sources-summary').textContent=sources.length?'Поиск идёт по '+sources.length+' '+
  (sources.length===1?'источнику':sources.length<5?'источникам':'источникам')+'.':'Источники не настроены.';
 const body=byId('sources');body.replaceChildren();
 for(const s of sources){
  const tr=document.createElement('tr');
  const access=s.protocol==='nnmclub'||s.protocol==='rutor'&&!s.api_key_configured&&!s.cookie_configured?'анонимный':
   [s.api_key_configured?'API-ключ':null,s.cookie_configured?'Cookie':null].filter(Boolean).join(' + ')||'анонимный';
  const card=s.details_configured?(s.trusted_source?'проверенная карточка':'карточка')+
   (s.source_host?' ('+s.source_host+')':''):'нет';
  const cells=[[s.name,''],[protocolNames[s.protocol]||s.protocol,''],[s.url,'mono'],
   [access,''],[card,''],[String(s.interval),'']];
  for(const [value,cls] of cells){const td=document.createElement('td');td.textContent=value;
   if(cls)td.className=cls;tr.append(td);}
  body.append(tr);
 }
}
const KNOWN=[
 {name:'RuTracker.org',match:/rutracker/i,how:'Prowlarr'},
 {name:'Rutor',match:/rutor/i,how:'Prowlarr или протокол rutor в aiarr'},
 {name:'Kinozal.tv',match:/kinozal/i,how:'Prowlarr'},
 {name:'NNM-Club',match:/nnm|noname/i,how:'протокол nnmclub в aiarr или Prowlarr'},
 {name:'NewStudio',match:/newstudio/i,how:'Prowlarr, только сериалы'},
 {name:'LostFilm.tv',studio:true},{name:'AlexFilm',studio:true},
 {name:'Red Head Sound',studio:true},{name:'Flarrow Films',studio:true}];
const loginChecks={},mirrorChecks={};
const when=value=>value?new Date(value).toLocaleString('ru-RU'):'';
function renderKnownSources(sources,indexers){
 const body=byId('known-sources');body.replaceChildren();
 for(const k of KNOWN){
  const tr=document.createElement('tr');
  const idx=k.match&&indexers.find(i=>k.match.test(i.name||''));
  const provider=k.match&&sources.find(s=>(idx&&s.name===idx.source)||k.match.test(s.name+' '+s.url));
  let state;
  if(k.studio)state='✔ распознаётся по названию озвучки в раздачах с трекеров';
  else if(!provider&&!idx)state='— нет ни в aiarr, ни в Prowlarr';
  else if(idx?.disabled_till&&new Date(idx.disabled_till)>new Date())
   state='⚠ Prowlarr отключил из-за ошибок до '+when(idx.disabled_till)+' (ошибки с '+when(idx.failing_since)+')'+
    (provider?'':'; в aiarr не подключён');
  else if(!provider)state='есть в Prowlarr, в aiarr не подключён';
  else state='✔ ищем';
  const login=document.createElement('td');const box=document.createElement('div');box.className='login';
  if(idx?.needs_login){
   const user=document.createElement('input');user.placeholder='логин';user.value=idx.username||'';user.autocomplete='off';
   const pass=document.createElement('input');pass.type='password';pass.placeholder=idx.username?'пароль (не менять)':'пароль';
   pass.autocomplete='new-password';
   const save=document.createElement('button');save.type='button';save.textContent='Проверить и сохранить';
   const mark=document.createElement('span');const check=loginChecks[idx.indexer_id];
   if(check){mark.textContent=check.ok?'✅ вход проверен':'❌ '+check.error;mark.title=check.at;}
   save.onclick=()=>run(async()=>{
    mark.textContent='⏳ проверяем вход на сайте…';
    try{await api('/admin/indexers/'+idx.indexer_id+'/credentials','PUT',{username:user.value.trim(),password:pass.value});
     loginChecks[idx.indexer_id]={ok:true,at:new Date().toLocaleString('ru-RU')};}
    catch(error){loginChecks[idx.indexer_id]={ok:false,error:error.message,at:new Date().toLocaleString('ru-RU')};}
    pass.value='';await refresh();});
   box.append(user,pass,save,mark);
  }else if(!k.studio&&(provider||idx))box.append('вход не нужен');
  if(idx?.mirrors?.length>1){
   const pick=document.createElement('select');pick.title='Зеркало';
   for(const url of idx.mirrors){const o=document.createElement('option');o.value=url;o.textContent=url;
    o.selected=url===idx.base_url;pick.append(o);}
   const apply=document.createElement('button');apply.type='button';apply.textContent='Сменить зеркало';
   const note=document.createElement('span');const check=mirrorChecks[idx.indexer_id];
   if(check)note.textContent=check.ok?'✅ зеркало работает':'❌ '+check.error;
   apply.onclick=()=>run(async()=>{note.textContent='⏳ проверяем зеркало…';
    try{await api('/admin/indexers/'+idx.indexer_id+'/mirror','PUT',{base_url:pick.value});
     mirrorChecks[idx.indexer_id]={ok:true};}
    catch(error){mirrorChecks[idx.indexer_id]={ok:false,error:error.message};}
    await refresh();});
   const row=document.createElement('div');row.className='login';row.append('Зеркало:',pick,apply,note);box.append(row);
  }else if(idx?.base_url){const row=document.createElement('div');row.className='muted';row.textContent='Адрес: '+idx.base_url;box.append(row);}
  if(idx&&!provider){const attach=document.createElement('button');attach.type='button';attach.textContent='Подключить к aiarr';
   attach.onclick=()=>run(async()=>{if(!confirm('Подключить '+k.name+' как источник поиска?'))return;
    await api('/admin/indexers/'+idx.indexer_id+'/attach','POST');config=null;await refresh();
    byId('config-status').textContent=k.name+' подключён. Worker начнёт искать в нём в течение 10 секунд.';});
   box.append(attach);}
  login.append(box);
  for(const v of [k.name,k.studio?'—':k.how,state]){const td=document.createElement('td');td.textContent=v;tr.append(td);}
  tr.append(login);body.append(tr);
 }
 const active=KNOWN.filter(k=>k.match&&sources.some(s=>k.match.test(s.name+' '+s.url))).length;
 byId('sources-summary').textContent='Поиск раздач идёт по '+sources.length+' подключённым источникам aiarr. '+
  'Из списка трекеров подключено '+active+' из 5; четыре студии озвучки распознаются в найденных раздачах.';
}
function route(){
 const page=['jobs','sources','settings'].includes(location.hash.slice(1))?location.hash.slice(1):'jobs';
 for(const name of ['jobs','sources','settings'])byId('page-'+name).hidden=name!==page;
 for(const a of document.querySelectorAll('nav.top a'))a.classList.toggle('active',a.dataset.page===page);
}
window.addEventListener('hashchange',route);
const ACTIVE_SEARCH=new Set(['REQUESTED','SEARCHING','ENRICHING','SELECTED']);
let searching=false;
async function refresh(){
 const [overview,sources,profiles,metrics]=await Promise.all([
  api('/admin/overview'),api('/admin/sources'),api('/admin/profiles'),api('/admin/metrics')]);
 renderJobs(overview);renderHealth(await api('/admin/provider-stats').catch(()=>[]));
 renderSources(sources);show('profiles',profiles);show('metrics',metrics);
 if(config===null){config=await api('/admin/config');renderConfig();}
 renderKnownSources(sources,await api('/admin/indexers').catch(()=>[]));
 if(selected!==null)await inspect(selected);
}
async function quickRefresh(){
 renderJobs(await api('/admin/overview'));
 if(selected!==null)await inspect(selected);
}
function renderJobs(overview){
 searching=overview.jobs.some(job=>ACTIVE_SEARCH.has(job.state));
 byId('mode').textContent=overview.dry_run?'Dry-run: загрузки выключены':'Загрузки включены';
 const gib=b=>b>=1099511627776?(b/1099511627776).toFixed(2)+' ТиБ':(b/1073741824).toFixed(0)+' ГиБ';
 const disk=overview.disk;if(disk){byId('disk').textContent='💾 свободно '+gib(disk.free)+' из '+gib(disk.total)+
  (disk.committed?' (ещё '+gib(disk.committed)+' докачивается)':'');
  byId('disk').className=disk.free-disk.committed<disk.reserve*2?'stat-bad':'';}
 const t=overview.transfer,sched=overview.speed_schedule;
 byId('speed').textContent=t?'⇣ '+(t.download_speed/1048576).toFixed(1)+' МиБ/с ⇡ '+(t.upload_speed/1048576).toFixed(1)+' МиБ/с'+
  (t.limited?' · ограничено до '+(t.alt_download_limit/1048576).toFixed(1)+' МиБ/с':'')+
  (sched?.enabled?' · расписание '+sched.from+':00–'+sched.to+':00':''):'qBittorrent недоступен';
 byId('jobs').replaceChildren();
 if(!overview.jobs.length)byId('jobs').textContent='Заявок пока нет.';
 for(const job of overview.jobs){
  const work=parse(job.work)||{},card=document.createElement('button');card.type='button';card.className='card';
  const cover=document.createElement('div');cover.className='poster';
  const src=/^[/][A-Za-z0-9_.-]+$/.test(work.poster_path||'')?'https://image.tmdb.org/t/p/w185'+work.poster_path:
   /^https:/.test(job.image_url||'')?job.image_url:null;
  if(src){const img=document.createElement('img');img.src=src;img.alt='';img.loading='lazy';img.referrerPolicy='no-referrer';
   img.style.cssText='width:100%;height:100%;object-fit:cover;border-radius:.4rem';img.onerror=()=>{img.remove();cover.textContent='🎬';};
   cover.append(img);}else cover.textContent='🎬';
  const body=document.createElement('div');body.className='body';card.append(cover,body);
  const h=document.createElement('h3');h.textContent=(work.title||'Без названия')+(work.year?' ('+work.year+')':'');
  const badge=document.createElement('span');badge.className='badge '+(tone[job.state]||'');badge.textContent=labelState(job.state);
  body.append(h,badge);
  const lines=[work.kind==='series'?'Сериал':'Фильм'];
  if(work.kind==='series'&&job.wanted)lines.push('готово '+job.ready+' из '+job.wanted+' серий');
  if(job.reason)lines.push(labelReason(job.reason));
  const air=parse(job.air_dates)||{},today=new Date().toISOString().slice(0,10);
  const next=Object.values(air).filter(d=>d&&d>today).sort()[0];
  if(work.kind==='series'&&next)lines.push('следующая серия '+new Date(next+'T12:00:00').toLocaleDateString('ru-RU',{day:'numeric',month:'short'}));
  if(job.replacing)lines.push('🔄 идёт замена на релиз лучше');
  else if(job.upgrade_resolution)lines.push('⬆ есть релиз лучше'+(job.current_resolution&&job.upgrade_resolution>job.current_resolution?': '+job.upgrade_resolution+'p':''));
  const p=document.createElement('p');p.className='muted';p.textContent=lines.join(' · ');body.append(p);
  if(typeof job.progress==='number'&&job.state==='DOWNLOADING'){const bar=document.createElement('progress');
   bar.className='progress';bar.max=100;bar.value=Math.round(job.progress*1000)/10;bar.title=bar.value+'%';body.append(bar);
   const info=document.createElement('p');info.className='muted';
   info.textContent=bar.value+'%'+(job.download_speed?' · '+(job.download_speed/1048576).toFixed(1)+' МиБ/с':'')+
    (job.eta>0&&job.eta<8640000?' · осталось '+(job.eta>=3600?Math.floor(job.eta/3600)+' ч '+Math.round(job.eta%3600/60)+' мин':Math.ceil(job.eta/60)+' мин'):'');
   body.append(info);}
  card.onclick=()=>run(()=>inspect(job.id));byId('jobs').append(card);
 }
}
async function run(action){
 if(busy)return;busy=true;byId('logout').disabled=true;byId('status').textContent='Загрузка…';
 try{await action();byId('status').textContent='Готово';}
 catch(error){byId('status').textContent=error.message;}
 finally{busy=false;byId('logout').disabled=false;}
}
async function enter(){const who=await api('/admin/session');await refresh();signedIn=true;
 byId('login').hidden=true;byId('dashboard').hidden=false;route();
 byId('status').textContent=who.mode==='seerr'?'Вход через Seerr: '+who.name:'Вход по API-ключу';}
byId('key-login').onsubmit=event=>{event.preventDefault();run(async()=>{
 token=byId('key').value;byId('key').value='';await enter();});};
(async()=>{try{await enter();}catch(error){byId('login').hidden=false;byId('status').textContent=error.message;}})();
byId('refresh').onclick=()=>run(refresh);
byId('add-provider').onclick=()=>addProvider();
byId('reload-config').onclick=()=>run(async()=>{config=await api('/admin/config');renderConfig();});
byId('config-form').onsubmit=event=>{event.preventDefault();run(async()=>{
 const values=collectConfig();config=await api('/admin/config','PUT',{revision:config.revision,settings:values});
 renderConfig();byId('config-status').textContent='Настройки сохранены. Worker применит их в течение 10 секунд.';
 await refresh();
});};
byId('logout').onclick=()=>{clearSession();byId('login').hidden=false;
 byId('status').textContent='Ключ забыт. Сессия Seerr завершается выходом в Seerr.';};
let lastFull=Date.now();
setInterval(()=>{
 if(!signedIn||busy||byId('dashboard').hidden!==false)return;
 if(Date.now()-lastFull>=15000){lastFull=Date.now();run(refresh);}
 else if(searching)run(quickRefresh);
},3000);
byId('add-studio').onclick=()=>{const name=byId('new-studio').value.trim();if(!name)return;
 weightRow(byId('studio-weights'),name,name,0);byId('new-studio').value='';};
byId('upgrade-apply').onclick=()=>run(async()=>{const u=parse(currentJob?.upgrade_candidate);if(u?.candidate)await chooseRelease(u.candidate);});
byId('upgrade-abort').onclick=()=>run(async()=>{if(selected===null||!confirm('Отменить замену? Файл в библиотеке останется прежним.'))return;
 await api('/admin/jobs/'+selected+'/abort-upgrade','POST');await refresh();});
for(const action of ['retry','cancel'])byId(action).onclick=()=>run(async()=>{
 if(selected===null)return;
 if(!confirm(action==='cancel'?'Отменить это задание? Закачка в qBittorrent будет удалена; уже импортированные файлы останутся.':'Повторить обработку этого задания?'))return;
 await api('/admin/jobs/'+selected+'/'+action,'POST');await refresh();
});
</script></html>'''
