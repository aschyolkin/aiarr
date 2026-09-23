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
</style>
<h1>aiarr</h1><p>Очередь, доказательства выбора и диагностика импорта</p>
<div id="login" hidden><p>Войдите в Seerr под администратором в этом браузере и обновите страницу.</p>
<details><summary>Аварийный вход по API-ключу</summary><form id="key-login">
<label>API-ключ <input id="key" type="password" autocomplete="off" required></label><button>Войти</button></form></details></div>
<p id="status" role="status"></p>
<main id="dashboard" hidden>
<button id="refresh">Обновить</button><button id="logout">Выйти</button>
<p id="mode"></p>
<section><div id="jobs" class="cards"></div></section>
<details><summary>Источники</summary><pre id="sources"></pre></details>
<details><summary>Профиль качества</summary><pre id="profiles"></pre></details>
<details id="config-panel"><summary>Настройки aiarr</summary>
<p class="hint">Изменения сохраняются в состоянии aiarr и применяются к новым попыткам обработки. Пустые поля ключей сохраняют прежние значения.</p>
<form id="config-form">
<fieldset><legend>Качество</legend><div class="grid">
<label class="field">Название профиля<input id="policy-version" required></label>
<label class="field">Максимальный размер, ГиБ<input id="policy-size" type="number" min="1" step="1" required></label>
<label class="field">Разрешения<select id="policy-resolution" multiple size="2"><option value="2160">2160p</option><option value="1080">1080p</option></select></label>
<label class="field">Видеокодеки<select id="policy-codecs" multiple size="2"><option value="H.265">H.265</option><option value="H.264">H.264</option></select></label>
<label class="field"><span>Русская аудиодорожка обязательна</span><input id="policy-russian" type="checkbox"></label>
</div><label class="field">Приоритеты источников (JSON)<textarea id="policy-sources"></textarea></label>
<label class="field">Приоритеты студий (JSON)<textarea id="policy-studios"></textarea></label></fieldset>
<fieldset><legend>Источники релизов</legend>
<p class="hint">Анонимный трекер: оставьте ключ и Cookie пустыми. Для Torznab/Prowlarr укажите API-ключ; для закрытой карточки источника укажите Cookie и хост карточки.</p>
<div id="provider-list"></div><button id="add-provider" type="button">Добавить источник</button></fieldset>
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
<label class="field">Резерв диска, ГиБ<input id="reserve_gib" type="number" min="0"></label>
<label class="field">Модель DeepSeek<select id="deepseek_model"><option value="deepseek-flash">deepseek-flash</option></select></label>
<label class="field">Вызовов DeepSeek в день<input id="deepseek_daily_calls" type="number" min="0"></label>
<label class="field">Токенов DeepSeek в день<input id="deepseek_daily_tokens" type="number" min="0"></label>
<label class="field">Вызовов DeepSeek на запрос<input id="deepseek_request_calls" type="number" min="0"></label>
<label class="field">Токенов DeepSeek на запрос<input id="deepseek_request_tokens" type="number" min="0"></label>
</div></fieldset>
<button type="submit">Сохранить настройки</button><button id="reload-config" type="button">Отменить изменения</button>
<p id="config-status" role="status"></p></form></details>
<details><summary>Метрики</summary><pre id="metrics"></pre></details>
<section id="inspection" hidden><h2 id="job-title"></h2>
<p id="job-summary" class="summary"></p><div id="download-panel" hidden>
<label for="download-progress">Загрузка: <span id="download-text"></span></label>
<progress id="download-progress" class="progress" max="100" value="0"></progress></div>
<h3>Ход заказа</h3><ol id="timeline" class="timeline"></ol>
<div id="episodes-panel" hidden><h3>Эпизоды</h3><p id="episode-summary"></p><ul id="episode-list"></ul></div>
<button id="retry">Повторить попытку</button><button id="cancel">Отменить запрос</button>
<p class="hint">Отмена в aiarr сохраняет торрент и файлы. Удаление заявки в Seerr удаляет файлы и торрент этой заявки после проверки владельца.</p>
<details><summary>Технические подробности</summary>
<details><summary>Кандидаты, evidence и причины выбора</summary><pre id="decision"></pre></details>
<details><summary>Выбранный релиз</summary><pre id="candidate"></pre></details>
<details><summary>События и импорт</summary><pre id="history"></pre></details></details>
</section></main>
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
 for(const id of ['jobs','sources','profiles','metrics','decision','candidate','history','mode','job-title'])byId(id).textContent='';
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
function renderConfig(){
 const s=config.settings,p=s.policy;
 byId('policy-version').value=p.version;byId('policy-size').value=Math.round(p.max_bytes/1073741824);
 for(const option of byId('policy-resolution').options)option.selected=p.resolutions.includes(Number(option.value));
 for(const option of byId('policy-codecs').options)option.selected=p.codecs.includes(option.value);
 byId('policy-russian').checked=p.russian_required;
 byId('policy-sources').value=JSON.stringify(p.source_weights,null,2);
 byId('policy-studios').value=JSON.stringify(p.studio_weights,null,2);
 byId('provider-list').replaceChildren();for(const provider of s.providers)addProvider(provider);
 for(const key of ['qbit_url','qbit_username','jellyfin_url','seerr_url','deepseek_model',
  'max_downloads','max_attempts','enrichment_limit','deepseek_daily_calls','deepseek_daily_tokens',
  'deepseek_request_calls','deepseek_request_tokens'])byId(key).value=s[key];
 byId('reserve_gib').value=Math.round(s.reserve_bytes/1073741824);
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
  resolutions:selectedValues('policy-resolution').map(Number),codecs:selectedValues('policy-codecs'),
  russian_required:byId('policy-russian').checked,
  source_weights:JSON.parse(byId('policy-sources').value),
  studio_weights:JSON.parse(byId('policy-studios').value)};
 if(!policy.resolutions.length||!policy.codecs.length)throw new Error('Выберите разрешение и видеокодек.');
 const providers=[...byId('provider-list').children].map(box=>{
  const provider={};for(const input of box.querySelectorAll('[data-setting]')){
   const key=input.dataset.setting;provider[key]=input.type==='checkbox'?input.checked:
    key==='interval'?Number(input.value):input.value.trim();
  }return provider;
 });
 const values={policy,providers,reserve_bytes:Number(byId('reserve_gib').value)*1073741824};
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
 INSUFFICIENT_SPACE:'Недостаточно места на диске',DOWNLOAD_QUEUE_LIMIT:'Очередь загрузок заполнена',
 VIDEO_RESOLUTION_BELOW_POLICY:'Разрешение видео ниже заданного',RUSSIAN_AUDIO_NOT_TAGGED:'Не подтверждена русская аудиодорожка',
 SEERR_REQUEST_DELETED:'Заявка удалена в Seerr, файлы удаляются',SEERR_FILE_REMOVED:'Файл удалён из библиотеки',
 SEERR_CLEANUP_DONE:'Файлы и торрент удалены',SEERR_CLEANUP_RETRY:'Удаление повторится позже',
 IMPORT_INODE_MISMATCH:'Файл в библиотеке заменён вручную — не удалён',
 TORRENT_OWNERSHIP_TAG_MISSING:'Торрент без метки aiarr — не удалён',
 TORRENT_OUTSIDE_MANAGED_PATH:'Торрент вне каталога загрузок — не удалён',
 ADMIN_CANCELLED:'Отменено в aiarr',SEERR_REREQUESTED:'Запрошено повторно',
 PROVIDER_SEARCH_FAILED:'Источник не ответил на поиск',PROVIDER_ENRICHMENT_FAILED:'Не удалось открыть карточку релиза',
 LIBRARY_ITEM_MISSING:'Файл пропал из Jellyfin',EPISODE_MONITORING_CHANGED:'Изменён список отслеживаемых серий',
 NO_MONITORED_EPISODES:'Нет отслеживаемых серий'};
const labelState=value=>states[value]||value;
const labelReason=value=>reasons[value]||value||'';
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
 let summary=labelState(job.state);
 if(work.kind==='series'&&wanted.length)summary+=' · готово '+ready.length+' из '+wanted.length+' серий'+
  (missing.length?' · ожидаются '+missing.map(x=>x.episode).join(', '):'');
 if(job.reason)summary+=' · '+labelReason(job.reason);
 byId('job-summary').textContent=summary;
 const panel=byId('download-panel');panel.hidden=!data.download;
 if(data.download){const d=data.download,pct=Math.round(d.progress*1000)/10;
  byId('download-progress').value=pct;
  byId('download-text').textContent=pct+'% · '+(d.download_speed/1048576).toFixed(1)+' МиБ/с'+
   (d.eta>0&&d.eta<8640000?' · осталось около '+Math.ceil(d.eta/60)+' мин':'');}
 byId('episodes-panel').hidden=work.kind!=='series';
 byId('episode-summary').textContent=ready.length+' готово · '+missing.length+' ожидается';
 byId('episode-list').replaceChildren();for(const ep of wanted){const li=document.createElement('li');
  li.textContent=ep.episode+' — '+(ep.available?'есть в Jellyfin':'ожидается');byId('episode-list').append(li);}
 byId('timeline').replaceChildren();const seen=new Set();
 for(const event of data.events||[]){
  const key=event.state+':'+(event.reason||'');if(seen.has(key))continue;seen.add(key);
  appendStep(labelState(event.state)+(event.reason?' · '+labelReason(event.reason):''),event.created);
 }
 if(decision.candidates){const candidates=decision.candidates,eligible=candidates.filter(x=>!x.reasons?.length&&!x.unknown?.length);
  appendStep('Проверено '+candidates.length+' вариантов; требованиям соответствуют '+eligible.length+'.');}
 if(chosen?.release){const facts=[...new Set((chosen.claims||[]).map(x=>x.field+': '+x.value))];
  appendStep('Выбран релиз: '+chosen.release.title+' ('+chosen.release.provider+')'+
   (facts.length?'. Подтверждено: '+facts.join(', '):'. Соответствует заданной политике.'));}
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
 const data=await api('/admin/jobs/'+id);selected=id;
 byId('inspection').hidden=false;byId('job-title').textContent=(parse(data.job.work)?.title||'Задание')+' · #'+id;
 renderProgress(data);
 show('decision',parse(data.job.decision));show('candidate',parse(data.job.candidate));
 show('history',{events:data.events,imports:data.imports});
 byId('retry').disabled=!['FAILED','NEEDS_REVIEW','AUTH_REQUIRED','WAITING_FOR_RELEASE','RETRY_SCHEDULED'].includes(data.job.state);
 byId('cancel').disabled=['AVAILABLE','CANCELLED'].includes(data.job.state);
}
async function refresh(){
 const [overview,sources,profiles,metrics]=await Promise.all([
  api('/admin/overview'),api('/admin/sources'),api('/admin/profiles'),api('/admin/metrics')]);
 byId('mode').textContent=overview.dry_run?'Dry-run: загрузки выключены':'Загрузки включены';
 byId('jobs').replaceChildren();
 if(!overview.jobs.length)byId('jobs').textContent='Заявок пока нет.';
 for(const job of overview.jobs){
  const work=parse(job.work)||{},card=document.createElement('button');card.type='button';card.className='card';
  const h=document.createElement('h3');h.textContent=(work.title||'Без названия')+(work.year?' ('+work.year+')':'');
  const badge=document.createElement('span');badge.className='badge '+(tone[job.state]||'');badge.textContent=labelState(job.state);
  card.append(h,badge);
  const lines=[work.kind==='series'?'Сериал':'Фильм'];
  if(work.kind==='series'&&job.wanted)lines.push('готово '+job.ready+' из '+job.wanted+' серий');
  if(job.reason)lines.push(labelReason(job.reason));
  const p=document.createElement('p');p.className='muted';p.textContent=lines.join(' · ');card.append(p);
  if(typeof job.progress==='number'&&job.state==='DOWNLOADING'){const bar=document.createElement('progress');
   bar.className='progress';bar.max=100;bar.value=Math.round(job.progress*1000)/10;bar.title=bar.value+'%';card.append(bar);}
  card.onclick=()=>run(()=>inspect(job.id));byId('jobs').append(card);
 }
 show('sources',sources);show('profiles',profiles);show('metrics',metrics);
 if(config===null){config=await api('/admin/config');renderConfig();}
 if(selected!==null)await inspect(selected);
}
async function run(action){
 if(busy)return;busy=true;byId('logout').disabled=true;byId('status').textContent='Загрузка…';
 try{await action();byId('status').textContent='Готово';}
 catch(error){byId('status').textContent=error.message;}
 finally{busy=false;byId('logout').disabled=false;}
}
async function enter(){const who=await api('/admin/session');await refresh();signedIn=true;
 byId('login').hidden=true;byId('dashboard').hidden=false;
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
setInterval(()=>{if(signedIn&&!busy&&byId('dashboard').hidden===false)run(refresh);},15000);
for(const action of ['retry','cancel'])byId(action).onclick=()=>run(async()=>{
 if(selected===null)return;
 if(!confirm(action==='cancel'?'Отменить это задание? Торрент и файлы сохранятся.':'Повторить обработку этого задания?'))return;
 await api('/admin/jobs/'+selected+'/'+action,'POST');await refresh();
});
</script></html>'''
