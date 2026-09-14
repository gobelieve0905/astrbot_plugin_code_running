function initialize() {
 const bridge = window.AstrBotPluginPage;
const states = {running:'运行中', succeeded:'已完成',failed:'失败',stopped:'已停止',interrupted:'重载中断'};
const el = (tag, text) => {const node=document.createElement(tag);node.textContent=text;return node;};
let guideLoaded=false;
const guideTitles={overview:'执行总览',batch:'批量读取',resume:'断点续接',analysis:'聚合与交付'};
async function loadGuide() {
 try {
  await bridge.ready();
  const result=await bridge.apiGet('guide');
  const tabs=document.getElementById('guide-tabs');tabs.replaceChildren();
  const buttons=[];
  function show(section,button){
   document.getElementById('guide-content').textContent=section.content;
   for(const b of buttons)b.setAttribute('aria-pressed',String(b===button));
  }
  for(const section of result.sections){
   const button=el('button',guideTitles[section.section] || section.section);
   button.type='button';button.onclick=()=>show(section,button);
   buttons.push(button);tabs.append(button);
  }
  if(!buttons.length)throw new Error('empty guide');
  show(result.sections[0],buttons[0]);
  guideLoaded=true;document.getElementById('guide-notice').textContent='';
 }catch(error){document.getElementById('guide-notice').textContent='指南加载失败，请重新加载';}
}
document.getElementById('guide-panel').ontoggle=()=>{
 if(document.getElementById('guide-panel').open && !guideLoaded)loadGuide();
};
document.getElementById('guide-retry').onclick=loadGuide;
async function refresh() {
 try {
  const result=await bridge.apiGet('jobs');
  const list=document.getElementById('jobs');list.replaceChildren();
  for (const job of result.jobs.sort((a,b)=>b.created-a.created)) {
   const card=el('article','');
   card.append(el('h2',states[job.state] || job.state),el('small',`${new Date(job.created*1000).toLocaleString()} · ${job.id}`),el('p',`API 调用 ${job.calls}/${job.quota ?? "旧任务未记录"} 次 · 剩余 ${job.remaining ?? "未知"} 次 · 成功 ${job.successful_calls ?? "未知"} / 失败 ${job.failed_calls ?? "未知"} · 时间上限 ${job.timeout} 秒`));
   if(job.state==='running') {const button=el('button','停止任务');button.onclick=async()=>{try{await bridge.apiPost('stop',{job_id:job.id});await refresh();}catch(e){document.getElementById('notice').textContent='停止失败，请重试';}};card.append(button);}
   if(job.error)card.append(el('p',`${job.error_code || 'FAILED'}：${job.error}`));
   card.append(el('pre',job.output || '暂无输出'));
   if(job.data_completeness)card.append(el('p',job.data_completeness));
   for(const file of job.files)card.append(el('p',`${file.name} · ${file.bytes} 字节`));
   const details=el('details','');details.append(el('summary','审计信息'),el('pre',JSON.stringify({code_sha256:job.code_sha256,operations:job.operations,audit_count:job.audit_count,recent_calls:job.call_audit},null,2)));card.append(details);list.append(card);
  }
  if(!result.jobs.length)list.append(el('p','暂无任务。在对话中提出分析或文件处理需求即可。'));
  document.getElementById('notice').textContent='';
 }catch(e){document.getElementById('notice').textContent='读取失败，请刷新重试';}
}
document.getElementById('refresh').onclick=refresh;
(async()=>{
 try {
  await bridge.ready();
  await refresh();
 } catch (error) {
  document.getElementById('notice').textContent='页面初始化失败，请重新打开插件页面';
 }
})();

}
// AstrBot appends its bridge SDK after the page scripts. Resolve it only once
// parsing (including the injected SDK) has finished, never at script evaluation.
if (document.readyState === 'loading') {
 document.addEventListener('DOMContentLoaded', initialize, {once:true});
} else {
 initialize();
}
