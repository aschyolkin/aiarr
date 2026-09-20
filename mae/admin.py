"""Public login shell; all operational data comes from authenticated endpoints."""

ADMIN_HTML = '''<!doctype html>
<html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>aiarr — администрирование</title>
<style>
body{font:16px system-ui;margin:2rem auto;padding:0 1rem;max-width:1100px;background:#111827;color:#e5e7eb}
input,button,select{font:inherit;padding:.5rem;margin:.25rem;border-radius:.3rem}
button{cursor:pointer}button:disabled{cursor:default;opacity:.5}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:.6rem;border-bottom:1px solid #374151}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#1f2937;padding:1rem}
#status{min-height:1.5em}a{color:#93c5fd}details{margin:1rem 0}section{overflow-x:auto}
</style>
<h1>aiarr</h1><p>Очередь, доказательства выбора и диагностика импорта</p>
<form id="login"><label>API-ключ <input id="key" type="password" autocomplete="off" required></label>
<button>Войти</button></form>
<p id="status" role="status"></p>
<main id="dashboard" hidden>
<button id="refresh">Обновить</button><button id="logout">Выйти</button>
<p id="mode"></p>
<section><table><thead><tr><th>ID</th><th>Произведение</th><th>Состояние</th><th>Причина</th><th></th></tr></thead>
<tbody id="jobs"></tbody></table></section>
<details><summary>Источники</summary><pre id="sources"></pre></details>
<details><summary>Профиль качества</summary><pre id="profiles"></pre></details>
<details><summary>Метрики</summary><pre id="metrics"></pre></details>
<section id="inspection" hidden><h2 id="job-title"></h2>
<button id="retry">Повторить попытку</button><button id="cancel">Отменить запрос</button>
<p>Просмотр evidence не разрешает обход обязательных условий. Отмена сохраняет торрент и файлы.</p>
<details open><summary>Кандидаты, evidence и причины выбора</summary><pre id="decision"></pre></details>
<details><summary>Выбранный релиз</summary><pre id="candidate"></pre></details>
<details><summary>События и импорт</summary><pre id="history"></pre></details>
</section></main>
<script>
'use strict';
let token='', selected=null, busy=false;
const byId=id=>document.getElementById(id);
const show=(id,value)=>{byId(id).textContent=JSON.stringify(value,null,2);};
const parse=value=>{try{return JSON.parse(value||'null');}catch{return null;}};
function clearSession(){
 token='';selected=null;byId('key').value='';byId('dashboard').hidden=true;
 byId('inspection').hidden=true;byId('login').hidden=false;
 for(const id of ['jobs','sources','profiles','metrics','decision','candidate','history','mode','job-title'])byId(id).textContent='';
}
async function api(path,method='GET'){
 const response=await fetch(path,{method,headers:{'X-Api-Key':token},cache:'no-store',redirect:'error'});
 if(response.status===401){clearSession();throw new Error('Неверный ключ или доступ отозван.');}
 if(!response.ok)throw new Error('Запрос не выполнен: HTTP '+response.status);
 return response.json();
}
async function inspect(id){
 const data=await api('/admin/jobs/'+id);selected=id;
 byId('inspection').hidden=false;byId('job-title').textContent='Задание '+id+' — '+data.job.state;
 show('decision',parse(data.job.decision));show('candidate',parse(data.job.candidate));
 show('history',{events:data.events,imports:data.imports});
 byId('retry').disabled=!['FAILED','NEEDS_REVIEW','AUTH_REQUIRED','WAITING_FOR_RELEASE','RETRY_SCHEDULED'].includes(data.job.state);
 byId('cancel').disabled=['AVAILABLE','CANCELLED'].includes(data.job.state);
}
async function refresh(){
 const [health,jobs,sources,profiles,metrics]=await Promise.all([
  api('/health'),api('/admin/jobs'),api('/admin/sources'),api('/admin/profiles'),api('/metrics')]);
 byId('mode').textContent=health.dry_run?'Dry-run: загрузки выключены':'Загрузки разрешены конфигурацией';
 byId('jobs').replaceChildren();
 for(const job of jobs){
  const tr=document.createElement('tr');
  for(const value of [job.id,parse(job.work)?.title||'',job.state,job.reason||'']){
   const td=document.createElement('td');td.textContent=String(value);tr.append(td);}
  const td=document.createElement('td'),button=document.createElement('button');button.textContent='Разобрать';
  button.onclick=()=>run(()=>inspect(job.id));td.append(button);tr.append(td);byId('jobs').append(tr);
 }
 show('sources',sources);show('profiles',profiles);show('metrics',metrics);
 if(selected!==null)await inspect(selected);
}
async function run(action){
 if(busy)return;busy=true;byId('logout').disabled=true;byId('status').textContent='Загрузка…';
 try{await action();byId('status').textContent='Готово';}
 catch(error){byId('status').textContent=error.message;}
 finally{busy=false;byId('logout').disabled=false;}
}
byId('login').onsubmit=event=>{event.preventDefault();run(async()=>{
 token=byId('key').value;await refresh();byId('key').value='';byId('login').hidden=true;byId('dashboard').hidden=false;});};
byId('refresh').onclick=()=>run(refresh);
byId('logout').onclick=()=>{clearSession();byId('status').textContent='Вы вышли';};
for(const action of ['retry','cancel'])byId(action).onclick=()=>run(async()=>{
 if(selected===null)return;
 if(!confirm(action==='cancel'?'Отменить это задание? Файлы сохранятся.':'Повторить обработку этого задания?'))return;
 await api('/admin/jobs/'+selected+'/'+action,'POST');await refresh();
});
</script></html>'''
