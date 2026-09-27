/* MarketIntel: local records, source tracking and evidence-led decisions. */
const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
const safeUrl = v => /^https?:\/\//i.test(v || '') ? esc(v) : '#';
const date = v => v ? String(v).replace('T',' ').slice(0,16) : '未核查';
const stage = {'needs-evidence':'待补证',interview:'访谈验证',pilot:'付费试点',paid:'已有成交',repeat:'重复使用',parked:'暂不推进'};
const review = {pending:'待审核',verified:'已审核',rejected:'已排除'};
const dirs = {support:'支持',counter:'反证',neutral:'中性'};
const kindNames = {project:'研究方向',source:'追踪来源',evidence:'证据',competitor:'竞品',opportunity:'机会',experiment:'验证',decision:'决策',research:'研究记录'};
const viewNames = {desk:'研究桌面',evidence:'证据与痛点',sources:'来源与追踪',competitors:'竞品比较',opportunities:'机会与验证',knowledge:'知识库检索',history:'决策与历史',settings:'数据与连接'};
let state = {records:[],changes:[],jobs:[],snapshots:[],connections:{}}, currentProject='', currentView='desk', editorRecord=null, editorKind='', editorData={}, noticeTimer, searchTimer, searchVersion=0, searchHits=[], searchIndex=-1;
const records = kind => state.records.filter(r => r.kind===kind && (!currentProject || r.project_id===currentProject || (kind==='project' && r.id===currentProject)));
const find = id => state.records.find(r=>r.id===id);
const projectName = id => find(id)?.name || '未关联方向';
const empty = (title,body) => `<div class="empty"><h3>${esc(title)}</h3><p>${esc(body)}</p></div>`;
const btn = (label,action,data='',cls='') => `<button type="button" class="${cls}" data-action="${action}" ${data}>${label}</button>`;
const edit = r => btn('查看 / 编辑','edit',`data-id="${r.id}"`,'small-btn');
const badge = (label,cls='') => `<span class="badge ${cls}">${esc(label)}</span>`;
const sourceLink = url => url ? `<a href="${safeUrl(url)}" target="_blank" rel="noopener noreferrer">打开原始来源</a>` : '<span class="hint">尚无来源链接</span>';
const resumable = job => ['waiting','interrupted','error'].includes(job.status)&&(find(job.source_id)?.data.type!=='apify'||!!job.remote_id);
function notice(message) { $('#notice').textContent=message; $('#notice').hidden=false; clearTimeout(noticeTimer); noticeTimer=setTimeout(()=>$('#notice').hidden=true,9000); }
async function api(path,body) {
 const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-MarketIntel-Token':state.csrf},body:JSON.stringify(body)});
 const data=await response.json(); if(!response.ok)throw new Error(data.error || '请求未完成'); return data;
}
async function refresh(renderPage=true) {
 state=await api('/api/state');
 const old=currentProject;
 $('#project-filter').innerHTML='<option value="">全部方向</option>'+state.records.filter(r=>r.kind==='project').map(r=>`<option value="${r.id}">${esc(r.name)}</option>`).join('');
 $('#project-filter').value=old;
 $('#top-status').textContent=`${state.records.length} 条本地档案`;
 if(renderPage)render();
}
function header(title,body,actions='') {return `<div class="page-head"><div><h1>${title}</h1><p>${body}</p></div><div class="actions">${actions}</div></div>`;}
function section(title,body,actions='') {return `<section class="section"><div class="section-head"><h2>${title}</h2><div class="actions">${actions}</div></div>${body}</section>`;}
function render() {
 currentView=location.hash.slice(1)||'desk'; if(!viewNames[currentView])currentView='desk';
 document.querySelectorAll('nav a').forEach(a=>{a.classList.toggle('active',a.dataset.view===currentView);if(a.dataset.view===currentView)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
 $('#breadcrumb').textContent=viewNames[currentView];
 const views={desk:desk,evidence:evidencePage,sources:sourcesPage,competitors:competitorsPage,opportunities:opportunitiesPage,knowledge:knowledgePage,history:historyPage,settings:settingsPage};
 $('#content').innerHTML=views[currentView]();
}
function desk() {
 const ev=records('evidence'), ops=records('opportunity'), sources=records('source');
 const changes=state.changes.filter(c=>!currentProject || find(c.source_id)?.project_id===currentProject);
 const working=records('project');
 return header('把情报变成下一步行动','先核查重复发生的任务与替代方案，再记录交付、付款和复用。',btn('新建研究方向','new','data-kind="project"','primary'))+
 `<div class="metric-band"><div><strong>${working.length}</strong><span>研究方向</span></div><div><strong>${ev.filter(x=>x.data.review_status==='pending').length}</strong><span>待审核证据</span></div><div><strong>${sources.filter(x=>x.due).length}</strong><span>需要复核的来源</span></div><div><strong>${changes.filter(x=>!x.reviewed).length}</strong><span>待核查变化</span></div></div>`+
 `<div class="desk-grid"><div>${section('当前方向',working.length?working.map(r=>`<div class="row"><div class="row-title"><h3>${esc(r.name)}</h3>${btn('进入研究','select-project',`data-id="${r.id}"`,'small-btn')}</div><p>${esc(r.data.audience)}</p><p>${esc(r.data.task)}</p><div class="tag-row">${badge(state.records.filter(e=>e.project_id===r.id&&e.kind==='evidence').length+' 条证据')}${badge('付款与利润待实测')}</div></div>`).join(''):empty('创建你的第一个方向','从具体人群和任务开始。'))}
 ${section('最近变化',changes.length?changes.slice(0,4).map(c=>`<div class="row"><div class="row-title"><h3>${esc(find(c.source_id)?.name||c.source_id)}</h3>${badge(c.reviewed?'已阅':'待核查',c.reviewed?'':'blue')}</div><p>${date(c.created)}</p>${btn('查看新旧变化','change',`data-id="${c.id}"`,'small-btn')}</div>`).join(''):empty('尚无变化记录','为一个来源取得快照，下一次内容变化会出现在这里。'))}</div>
 <div>${section('下一轮研究',`<div class="section-body"><ol class="steps"><li>调用已有结论与反证<small>先检索知识库，避免重复研究。</small></li><li>补查过期或缺失事实<small>核对最新价格、功能和真实任务。</small></li><li>定义一次可验收交付<small>明确买单人、输入、输出和价格。</small></li><li>记录成交与实际成本<small>再次使用之后再判断订阅是否成立。</small></li></ol><div class="actions">${btn('检索历史','search')}${btn('发现新线索','discover')}</div></div>`)}
 ${section('机会概况',ops.slice(0,3).map(o=>`<div class="row"><h3>${esc(o.name)}</h3><div class="tag-row">${badge(stage[o.data.stage]||'待补证')}${badge(o.assessment.paid_trials+' 次收款记录')}</div><p>${esc(o.data.hypothesis||o.data.deliverable)}</p></div>`).join('')||empty('没有机会档案','先收集证据，再定义可以交付的结果。'))}</div></div>`;
}
function evidencePage() {
 const list=records('evidence');
 return header('证据与痛点','线索先进入待审核区。检查角色、独立性、已解决情况和推广可能性。',btn('发现线索','discover')+btn('导入证据','import')+btn('添加证据','new','data-kind="evidence"','primary'))+
 `<div class="toolbar"><label for="evidence-status" class="sr-only">审核状态筛选</label><select id="evidence-status"><option value="">全部审核状态</option><option value="pending">待审核</option><option value="verified">已审核</option><option value="rejected">已排除</option></select></div>`+
 section('来源与陈述',list.length?`<div class="table-wrap"><table><thead><tr><th>陈述与来源</th><th>关联方向</th><th>性质</th><th>审核</th><th>操作</th></tr></thead><tbody>${list.map(r=>`<tr data-review="${esc(r.data.review_status||'pending')}"><td class="title">${esc(r.name)}<small>${esc(r.data.summary||'待补充具体陈述')}</small><div class="meta-line">${sourceLink(r.data.url)}</div></td><td>${esc(projectName(r.project_id))}<small>${date(r.data.observed_at)}</small></td><td>${badge(dirs[r.data.direction]||'中性',r.data.direction==='counter'?'bad':'')}<small>${esc({'fact':'有来源事实','self-report':'用户/厂商自述',inference:'推断'}[r.data.claim_type]||'未分类')}</small></td><td>${badge(review[r.data.review_status]||'待审核',r.data.review_status==='verified'?'good':'')}</td><td>${edit(r)}</td></tr>`).join('')}</tbody></table></div>`:empty('还没有证据','添加原始来源和具体陈述，或使用公开搜索。'));
}
function sourcesPage() {
 const sources=records('source'), jobs=state.jobs.filter(j=>!currentProject||find(j.source_id)?.project_id===currentProject);
 return header('来源与变化追踪','按设定间隔检查公开页面；保留新旧快照。自动复查仅在本地程序运行时执行。',btn('添加追踪来源','new','data-kind="source"','primary'))+
 section('追踪清单',sources.length?`<div class="table-wrap"><table><thead><tr><th>来源</th><th>最近检查</th><th>追踪方式</th><th>状态</th><th>操作</th></tr></thead><tbody>${sources.map(r=>`<tr><td class="title">${esc(r.name)}<small>${sourceLink(r.data.url)}</small></td><td>${date(r.data.last_checked)}<small>复核间隔 ${esc(r.data.interval_hours)} 小时</small></td><td>${badge(r.data.tracking?'自动复查':'手动检查')}<small>${esc(r.data.type==='apify'?'Apify / 可产生费用':r.data.type==='rss'?'RSS':'公开网页')}</small></td><td>${badge(r.data.last_error?'失败':r.due?'待复核':'已取得快照',r.data.last_error?'bad':r.due?'':'good')}<small>${esc(r.data.last_error||'')}</small></td><td><div class="actions">${btn('立即检查','collect',`data-id="${r.id}"`,'small-btn')}${edit(r)}${r.data.latest_snapshot?btn('快照','snapshot',`data-id="${r.data.latest_snapshot}"`,'small-btn'):''}</div></td></tr>`).join('')}</tbody></table></div>`:empty('添加需要持续观察的来源','从竞品价格页、功能说明或 RSS 开始。'))+
 section('运行记录',jobs.length?jobs.slice(0,12).map(j=>`<div class="row"><div class="row-title"><h3>${esc(find(j.source_id)?.name||j.source_id)}</h3>${badge({queued:'等待',running:'运行中',done:'完成',error:'失败',waiting:'远端等待',interrupted:'中断'}[j.status]||j.status,j.status==='error'?'bad':j.status==='done'?'good':'')}</div><p>${date(j.created)} ${esc(j.error||'')}</p>${j.result.snapshot_id?btn('打开快照','snapshot',`data-id="${j.result.snapshot_id}"`,'small-btn'):''}${resumable(j)?btn('恢复 / 再检查','resume',`data-id="${j.id}" data-source="${j.source_id}"`,'small-btn'):''}${j.result.cost_usd!=null?`<small>远端返回用量费用：$${esc(j.result.cost_usd)}</small>`:''}</div>`).join(''):empty('还没有采集任务','检查来源后会保存结果、错误及费用信息。'));
}
function competitorsPage() {
 const list=records('competitor');
 return header('竞品与替代方案','比较同一个任务的完成方式。公开资料未发现某个功能，不等于产品不支持。',btn('添加竞品','new','data-kind="competitor"','primary'))+
 section('功能与价格矩阵',list.length?`<div class="table-wrap"><table><thead><tr><th>产品</th><th>价格与口径</th><th>功能 / 交付</th><th>限制与替代</th><th>核查</th></tr></thead><tbody>${list.map(r=>`<tr><td class="title">${esc(r.name)}<small>${sourceLink(r.data.url)}</small><small>${esc(projectName(r.project_id))}</small></td><td>${r.data.price==null?'未录入':esc(r.data.currency||'USD')+' '+esc(r.data.price)}<small>${esc(r.data.billing||'计费口径待查')}</small></td><td>${esc(r.data.features||'待核查')}</td><td>${esc(r.data.limitations||'待核查；不自动认定为缺陷')}</td><td>${date(r.data.checked_at)}<small>${esc(r.data.price_status||'核查状态待确认')}</small>${edit(r)}</td></tr>`).join('')}</tbody></table></div>`:empty('添加直接竞品和免费替代','原生功能、表格和人工服务也应放入比较。'));
}
function opportunitiesPage() {
 const list=records('opportunity');
 return header('机会、交付与利润条件','模型是条件化情景；实际收款、成本和复用记录单独计算。',btn('新增机会','new','data-kind="opportunity"','primary'))+
 `<div class="opportunity-list">${list.map(r=>{const a=r.assessment, e=a.economics;return `<article class="opportunity"><div class="opportunity-main"><div class="row-title"><h2>${esc(r.name)}</h2>${badge(stage[r.data.stage]||'待补证','blue')}</div><p class="hint">${esc(projectName(r.project_id))}</p><dl class="definition"><dt>买单人</dt><dd>${esc(r.data.payer||'待确认')}</dd><dt>交付结果</dt><dd>${esc(r.data.deliverable||'待定义')}</dd><dt>已有替代</dt><dd>${esc(r.data.alternatives||'待核查')}</dd><dt>进入渠道</dt><dd>${esc(r.data.channel||'待验证')}</dd><dt>切入假设</dt><dd>${esc(r.data.hypothesis||'待验证')}</dd></dl><p class="hint">${esc(r.data.unknowns||'请明确仍未知的条件')}</p><div class="tag-row">${badge(a.approved+' 条已审核证据')}${badge(a.counter+' 条已审核反证',a.counter?'bad':'')}${a.counter_pending?badge(a.counter_pending+' 条反证待审核'):''}${badge(a.independent_threads+' 个审核后来源分组')}</div><div class="actions">${edit(r)}${btn('记录验证','trial',`data-id="${r.id}"`)}${btn('记录决策','decision',`data-id="${r.id}"`)}</div></div><aside class="opportunity-side"><small>实际收款验证</small><div class="number">${a.paid_trials} 次</div><small>${a.repeat_trials} 次记录了复用</small><div class="financials"><small>月经营结余情景</small><div class="number">${e.profit==null?'未知':esc(r.data.currency||'USD')+' '+e.profit.toFixed(2)}</div><small>${e.status==='unknown'?'请填写价格、成本、获客支出和客户数':'这是输入假设的计算，不是收入预测'}</small>${e.break_even!=null?`<p class="meta-line">给定成本条件，盈亏平衡需 ${e.break_even} 位客户</p>`:''}</div><ul class="unknowns">${a.missing.map(x=>`<li>待补：${esc(x)}</li>`).join('')}</ul></aside></article>`;}).join('')||empty('定义可以检验的机会','从明确交付开始，关联同一方向的证据。')}</div>`;
}
function knowledgePage() {
 return header('调用过去的研究','原始事实、反证与决策一起检索。笔记可在 Obsidian 打开，人工分析不会被系统摘要覆盖。',btn('同步档案笔记','sync'))+
 `<section class="section"><div class="section-body"><div class="knowledge-query"><label for="knowledge-input" class="sr-only">检索知识库</label><input id="knowledge-input" type="search" placeholder="输入具体任务、竞品或否决原因"><button class="primary" data-action="knowledge-search">检索本地知识库</button></div><p class="meta-line">知识库目录：${esc(state.vault)}</p><div id="knowledge-results">${empty('先读旧结论，再补新事实','检索匹配笔记及人工分析；结果保留来源与核查时间。')}</div></div></section>`;
}
function historyPage() {
 const list=[...records('decision'),...records('experiment'),...records('research')].sort((a,b)=>b.created.localeCompare(a.created));
 return header('验证与决策历史','记录当时的依据、前提和实际结果，方便下一轮检查是否发生变化。',btn('保存研究笔记','new','data-kind="research"'))+
 section('最近记录',list.length?list.map(r=>`<div class="row"><div class="row-title"><h3>${esc(r.name)}</h3>${badge(kindNames[r.kind])}</div><p>${esc(projectName(r.project_id))} / ${date(r.created)}</p><p>${esc(r.data.reason||r.data.summary||r.data.question||r.data.outcome||r.data.analysis?.slice(0,220)||'打开查看完整记录')}</p>${edit(r)}</div>`).join(''):empty('把下一次行动记录下来','访谈、交付、付款和放弃理由都是长期情报。'));
}
function settingsPage() {
 const c=state.connections;
 return header('数据与可选连接','核心流程在本地运行；付费数据与 AI 连接只在你主动操作时使用。')+
 section('连接状态',`<div class="section-body"><div class="connection"><div><h3>公开网页、RSS 与线索搜索</h3><p>无需 API 密钥。实际可用性取决于来源和网络。</p></div>${badge('已接入','good')}</div>${[['Apify',c.apify,'设置 APIFY_TOKEN。每个 Actor 配置输入和运行费用上限。'],['DataForSEO',c.dataforseo,'设置 DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD。每次最多 20 个关键词。'],['AI 辅助研判',c.ai,'设置 OPENROUTER_API_KEY / OPENROUTER_MODEL。发送所选方向的证据摘要、最多 5 条历史摘录和最近 5 条决策。']].map(([name,enabled,desc])=>`<div class="connection"><div><h3>${name}</h3><p>${desc}</p></div>${badge(enabled?'已配置':'未配置',enabled?'good':'')}</div>`).join('')}<div class="actions">${btn('查询关键词需求','keywords','',c.dataforseo?'':'')}${btn('AI 辅助研究','ai')}</div></div>`)+
 connectionForm()+
 section('导出与备份',`<div class="section-body"><p>数据库、知识库与原始快照保存在：<br>${esc(state.root)}</p><div class="actions"><a class="download" href="/api/export?type=markdown${currentProject?'&project='+currentProject:''}">导出研究报告</a><a class="download" href="/api/export?type=json">导出记录 JSON</a><a class="download" href="/api/export?type=csv${currentProject?'&project='+currentProject:''}">导出证据 CSV</a>${btn('创建一致性备份','backup')}${btn('导入证据','import')}</div><p class="meta-line">备份包含 SQLite 数据库、知识库和原始文件。连接凭据单独保存，不包含在备份或导出中。</p></div>`)+
 section('运行方式',`<div class="section-body"><p>在 Obsidian 中将 <strong>${esc(state.vault)}</strong> 作为已有知识库打开。</p><p class="hint">自动复查需要本地进程持续运行；电脑休眠或程序停止时不会执行。收费 Actor 默认仅手动运行。Painbase、TrustMRR 和 Reddit 不做自动网页采集，可使用许可允许的导出或手动证据。</p></div>`);
}

function connectionForm() {
 const groups=[['Apify',[['APIFY_TOKEN','API Token','password']]],['DataForSEO',[['DATAFORSEO_LOGIN','登录名','text'],['DATAFORSEO_PASSWORD','API 密码','password']]],['AI / OpenRouter',[['OPENROUTER_API_KEY','API Key','password'],['OPENROUTER_MODEL','模型 ID','text']]]];
 return section('使用你自己的 API Key',`<div class="section-body"><p>这些连接均为可选。填写后保存即可使用，不需要重启；留空保留原配置。</p><p class="hint">凭据只保存在本机独立配置文件中，不回显、不写入浏览器本地存储，也不随研究导出或备份。文件未加密，请使用你信任的电脑。</p><form id="connection-form" autocomplete="off">${groups.map(([label,items])=>`<fieldset class="connection-group"><legend>${label}</legend><div class="form-grid">${items.map(([name,label,type])=>`<div class="field"><label for="key-${name}">${label}</label><input id="key-${name}" name="${name}" type="${type}" autocomplete="off" spellcheck="false" maxlength="4096" value="${name==='OPENROUTER_MODEL'?esc(state.model||''):''}" placeholder="${state.configured?.[name]?'已配置；留空保留':'填写你自己的值'}"></div>`).join('')}</div>${btn('清除此连接','clear-connection',`data-keys="${items.map(x=>x[0]).join(',')}"`,'small-btn')}</fieldset>`).join('')}<p id="connection-result" role="status"></p><button type="submit" class="primary">保存本机连接配置</button></form></div>`);
}

document.addEventListener('submit',async e=>{
 if(e.target.id!=='connection-form')return;
 e.preventDefault();const form=e.target,save=form.querySelector('[type="submit"]');save.disabled=true;
 const values=Object.fromEntries([...new FormData(form)].filter(([,value])=>value.trim()).map(([key,value])=>[key,value]));
 try{await api('/api/connections',{values});form.reset();await refresh();notice('连接配置已保存在本机；密钥不会回显。');}
 catch(error){$('#connection-result').textContent=error.message;}
 finally{save.disabled=false;}
});

const fields = {
 project:[['audience','目标人群'],['task','要完成的具体任务','area'],['keywords','英文检索关键词'],['region','市场 / 地区'],['language','语言'],['notes','研究范围与备注','area']],
 source:[['type','来源类型','select',{web:'公开网页',rss:'RSS / Atom',apify:'Apify Actor'}],['url','来源 URL','url'],['interval_hours','复核间隔（小时）','number'],['capture_allowed','已确认本次公开页面采集与保存范围','check'],['save_raw','保存原始响应文件','check'],['tracking','程序运行时自动复查公开网页 / RSS','check'],['actor_id','Apify Actor ID（owner/name）'],['actor_input','Actor 输入 JSON','json'],['max_charge','Apify 单次费用上限（美元）','number'],['allow_apify','允许手动运行此收费 Actor','check'],['policy','来源使用范围 / 备注','area']],
 evidence:[['url','原始来源 URL','url'],['summary','具体陈述与背景','area'],['quote','短摘录 / 可定位原文','area'],['claim_type','陈述类型','select',{fact:'有来源事实','self-report':'用户或厂商自述',inference:'推断'}],['direction','证据方向','select',dirs],['review_status','审核状态','select',review],['resolved','问题是否已解决','select',{unknown:'未知',yes:'已有解决办法',no:'自述仍未解决'}],['observed_at','核查日期'],['thread_key','同一讨论分组（可留空）'],['role','角色及独立性核查'],['notes','反证、推广或其他限制','area']],
 competitor:[['url','官方产品 URL','url'],['price','公开标价（未知留空）','number'],['currency','币种'],['billing','计费口径（月付 / 年付 / 每次）'],['checked_at','核查日期'],['price_status','价格来源与核查说明'],['features','功能与完成的任务','area'],['limitations','限制、未核查功能与替代','area'],['evidence_ids','关联证据','evidence']],
 opportunity:[['stage','商业验证阶段','select',stage],['payer','谁付钱'],['deliverable','一次可验收的交付','area'],['alternatives','已有付费 / 免费替代','area'],['channel','可执行的获客路径','area'],['hypothesis','切入点与待验证假设','area'],['unknowns','未知条件与反证','area'],['currency','经济情景币种'],['evidence_ids','关联证据（同一方向）','evidence'],['economics.price','每客户月收入假设','number'],['economics.variable','每客户月可变成本假设','number'],['economics.fixed','月固定成本假设','number'],['economics.acquisition','当月获客支出假设','number'],['economics.customers','当月付费客户数假设','number']],
 experiment:[['opportunity_id','关联机会','opportunity'],['date','验证 / 收款日期','date'],['payment','实际收款（未知留空）','number'],['cost','实际交付成本（未知留空）','number'],['currency','实际收款币种'],['repeat','此次已有实际再次使用 / 购买','check'],['outcome','结果、成本口径与记录依据','area']],
 decision:[['opportunity_id','关联机会','opportunity'],['choice','选择','select',{continue:'继续验证',adjust:'调整切口',park:'暂不推进'}],['reason','决策理由与依据','area'],['conditions','重新考虑的条件','area'],['next_action','下一步行动','area']],
 research:[['summary','研究摘要','area'],['counter','反证与已解决案例','area'],['unknowns','仍未知的数据','area'],['next_action','下一步','area']]
};
function fieldValue(data,key){return key.includes('.')?data[key.split('.')[0]]?.[key.split('.')[1]]:data[key];}
function fieldHtml(field,data,projectId) {
 const [key,label,type='text',options]=field, value=fieldValue(data,key);
 if(type==='check')return `<div class="field full"><label class="check"><input type="checkbox" name="${key}" ${value?'checked':''}>${esc(label)}</label></div>`;
 let input;
 if(type==='area'||type==='json')input=`<textarea name="${key}" id="f-${key}">${esc(type==='json'?JSON.stringify(value||{},null,2):value||'')}</textarea>`;
 else if(type==='select')input=`<select name="${key}" id="f-${key}">${Object.entries(options).map(([k,l])=>`<option value="${k}" ${value===k?'selected':''}>${esc(l)}</option>`).join('')}</select>`;
 else if(type==='opportunity')input=`<select name="${key}" id="f-${key}" required><option value="">请选择机会</option>${state.records.filter(x=>x.kind==='opportunity'&&x.project_id===projectId).map(x=>`<option value="${x.id}" ${value===x.id?'selected':''}>${esc(x.name)}</option>`).join('')}</select>`;
 else if(type==='evidence')input=`<div class="checkbox-list">${state.records.filter(x=>x.kind==='evidence'&&x.project_id===projectId).map(x=>`<label class="check"><input type="checkbox" name="evidence_ids" value="${x.id}" ${(value||[]).includes(x.id)?'checked':''}>${esc(x.name)} (${review[x.data.review_status]||'待审核'})</label>`).join('')||'<small>这个方向还没有证据。</small>'}</div>`;
 else input=`<input name="${key}" id="f-${key}" type="${type==='number'?'number':type==='url'?'url':type==='date'?'date':'text'}" ${type==='number'?'min="0" step="any"':''} value="${esc(value??'')}">`;
 return `<div class="field ${['area','json','evidence'].includes(type)?'full':''}"><label for="f-${key}">${esc(label)}</label>${input}${key.startsWith('economics.')?'<small>输入假设用于情景计算，未填写保持未知。</small>':''}</div>`;
}
function openEditor(kind,record=null,prefill={},projectOverride='') {
 editorKind=kind;editorRecord=record;
 const projectId=record?.project_id||projectOverride||currentProject||state.records.find(r=>r.kind==='project')?.id||'';
 const defaults={source:{type:'web',interval_hours:336,save_raw:true,capture_allowed:false,max_charge:1},evidence:{claim_type:'self-report',review_status:'pending',direction:'neutral',resolved:'unknown',observed_at:new Date().toISOString().slice(0,10)},competitor:{currency:'USD'},opportunity:{stage:'needs-evidence',currency:'USD',evidence_ids:[],economics:{}},experiment:{currency:'USD',date:new Date().toISOString().slice(0,10)}};
 const data={...(defaults[kind]||{}),...(record?.data||{}),...prefill};
 editorData=data;
 $('#editor-title').textContent=(record?'编辑':'新增')+kindNames[kind];$('#form-error').textContent='';
 $('#form-fields').innerHTML=`<div class="field"><label for="record-name">标题</label><input id="record-name" name="name" required maxlength="180" value="${esc(record?.name||prefill.name||'')}"></div>`+(kind!=='project'?`<div class="field"><label for="record-project">关联研究方向</label><select id="record-project" name="project_id" ${record?'disabled':''} required>${state.records.filter(r=>r.kind==='project').map(r=>`<option value="${r.id}" ${projectId===r.id?'selected':''}>${esc(r.name)}</option>`).join('')}</select></div>`:'')+`<div class="form-grid" id="dynamic-fields">${fields[kind].map(f=>fieldHtml(f,data,projectId)).join('')}</div>`;
 $('#editor').showModal();
 if(kind!=='project')$('#record-project').addEventListener('change',e=>{try{const current=readFormData(kind);delete current.source_id;delete current.snapshot_id;editorData=current;$('#dynamic-fields').innerHTML=fields[kind].map(f=>fieldHtml(f,current,e.target.value)).join('');$('#form-error').textContent='';}catch(error){$('#form-error').textContent=error.message;}});
}
function readFormData(kind) {
 const form=new FormData($('#record-form')), data={...editorData};
 for(const [key,,type='text'] of fields[kind]) {
  let value;
  if(type==='check')value=form.has(key);
  else if(type==='evidence')value=form.getAll(key);
  else if(type==='json')value=JSON.parse(form.get(key)||'{}');
  else if(type==='number')value=form.get(key)===''?null:Number(form.get(key));
  else value=form.get(key)||'';
  if(key.includes('.')){const [group,sub]=key.split('.');data[group]={...(data[group]||{}),[sub]:value};}else data[key]=value;
 }
 return data;
}
$('#record-form').addEventListener('submit',async e=>{
 e.preventDefault();const save=$('#save-record');save.disabled=true;save.textContent='正在保存…';$('#form-error').textContent='';
 try {const form=new FormData(e.currentTarget),result=await api('/api/records',{kind:editorKind,name:form.get('name'),project_id:editorKind==='project'?'':editorRecord?.project_id||form.get('project_id'),data:readFormData(editorKind),id:editorRecord?.id});$('#editor').close();await refresh();notice(result.vault_status==='conflict'?'记录已保存；笔记存在编辑冲突，请在知识库合并':'已保存到本地档案和知识库');}
 catch(err){$('#form-error').textContent=err.message;}
 finally{save.disabled=false;save.textContent='保存到档案与知识库';}
});
function read(title,html){$('#reader-title').textContent=title;$('#reader-content').innerHTML=html;if(!$('#reader').open)$('#reader').showModal();}
function projectSelect(){return `<label for="action-project">关联研究方向</label><select id="action-project">${state.records.filter(r=>r.kind==='project').map(r=>`<option value="${r.id}" ${r.id===currentProject?'selected':''}>${esc(r.name)}</option>`).join('')}</select>`;}
function discoverDialog(){read('发现公开线索',`<p class="hint">先检索历史，再取最多 30 条新结果。技术社区存在人群偏差，结果需要人工审核。</p><div class="field">${projectSelect()}</div><div class="field"><label for="discover-query">具体任务关键词</label><input id="discover-query" value="${esc(find(currentProject)?.data.keywords||'')}" placeholder="例如 Shopify CSV variants"></div><div class="field"><label for="discover-provider">公开来源</label><select id="discover-provider"><option value="hackernews">Hacker News / Algolia</option><option value="github">GitHub Issues</option></select></div><div id="action-result" role="status"></div>${btn('检索历史并收集线索','run-discover','','primary')}`);}
function importDialog(){read('导入证据',`<p class="hint">支持 JSON 数组或 CSV，字段：name、url、summary、claim_type、direction、observed_at。每次最多 1000 条，导入后统一待审核，相同 URL 与陈述跳过。</p><div class="field">${projectSelect()}</div><div class="field"><label for="import-format">格式</label><select id="import-format"><option value="json">JSON 数组</option><option value="csv">CSV</option></select></div><div class="field"><label for="import-file">从文件读取（可选）</label><input type="file" id="import-file" accept=".json,.csv,.txt"></div><div class="field"><label for="import-text">导入内容</label><textarea id="import-text" placeholder='[{"name":"陈述标题","url":"https://...","summary":"具体问题"}]'></textarea></div><div id="action-result" role="status"></div>${btn('校验并导入','run-import','','primary')}`);$('#import-file').addEventListener('change',async e=>{const f=e.target.files[0];if(f){$('#import-text').value=await f.text();$('#import-format').value=f.name.endsWith('.csv')?'csv':'json';}});}
function paidDialog(type){read(type==='ai'?'AI 辅助研判':'关键词需求查询',`<p class="hint">${type==='ai'?'此操作将所选方向的证据摘要、最多 5 条历史摘录和最近 5 条决策发送到配置的模型，可能产生费用；生成结果保持待审核。':'此操作调用配置的 DataForSEO 账号，按供应商收费。当前查询美国英文口径，每次最多 20 个词。'}</p><div class="field">${projectSelect()}</div><div class="field"><label for="paid-input">${type==='ai'?'研究问题':'关键词，每行一个'}</label><textarea id="paid-input">${type==='ai'?'请分析这个方向的支持证据、反证、差异假设和下一次验证。':''}</textarea></div><div id="action-result" role="status"></div>${btn('提交这次查询','run-'+type,'','primary')}`);}
function retryPaidDialog(source){read('核查上次收费任务',`<p>上次启动没有取得远端任务 ID，可能已经启动或收费。请先到 <a href="https://console.apify.com/actors/runs" target="_blank" rel="noopener noreferrer">Apify 控制台</a>核查；已启动的任务可在控制台取得输出后手动导入。</p><label class="check"><input type="checkbox" id="paid-retry-confirm">我已核查上次运行记录，仍决定新建一次可能收费的任务。</label><div id="action-result" role="status"></div>${btn('核查后新建任务','confirmed-collect',`data-id="${source.id}" disabled`,'primary')}`);$('#paid-retry-confirm').addEventListener('change',e=>{$('[data-action="confirmed-collect"]').disabled=!e.target.checked;});}
async function runAction(button,fn){const original=button.textContent;button.disabled=true;button.textContent='正在处理…';try{await fn();}catch(err){const target=$('#action-result');if(target)target.textContent=err.message;else notice(err.message);}finally{button.disabled=false;button.textContent=original;}}
document.addEventListener('click',async e=>{
 const b=e.target.closest('[data-action]');if(!b)return;const action=b.dataset.action,id=b.dataset.id;
 if(action==='new'){if(b.dataset.kind!=='project'&&!state.records.some(r=>r.kind==='project')){notice('请先新建研究方向');return;}openEditor(b.dataset.kind);}
 else if(action==='edit')openEditor(find(id).kind,find(id));
 else if(action==='close-dialog')$('#editor').close();
 else if(action==='close-reader')$('#reader').close();
 else if(action==='close-search')$('#search-dialog').close();
 else if(action==='clear-connection')await runAction(b,async()=>{await api('/api/connections',{values:Object.fromEntries(b.dataset.keys.split(',').map(key=>[key,'']))});await refresh();notice('已清除此连接。若环境变量或 .env 中仍保留原值，请自行删除；当前连接已停用。');});
 else if(action==='select-project'){currentProject=id;$('#project-filter').value=id;render();}
 else if(action==='search')openSearch();
 else if(action==='trial'){const o=find(id);openEditor('experiment',null,{opportunity_id:id,name:'验证：'+o.name},o.project_id);}
 else if(action==='decision'){const o=find(id);openEditor('decision',null,{opportunity_id:id,name:'决策：'+o.name},o.project_id);}
 else if(action==='discover')discoverDialog();
 else if(action==='import')importDialog();
 else if(action==='keywords'||action==='ai')paidDialog(action);
 else if(action==='collect'&&find(id)?.start_uncertain)retryPaidDialog(find(id));
 else if(action==='collect'||action==='resume'||action==='confirmed-collect')await runAction(b,async()=>{await api('/api/collect',{source_id:action==='resume'?b.dataset.source:id,resume_id:action==='resume'?id:undefined,confirmed_new:action==='confirmed-collect'&&$('#paid-retry-confirm')?.checked===true});if(action==='confirmed-collect')$('#reader').close();await refresh();notice('任务已开始，可在运行记录查看进度');});
 else if(action==='snapshot')await runAction(b,async()=>{const s=await api('/api/snapshot?id='+encodeURIComponent(id));read('来源快照',`<p class="hint">${date(s.checked_at)} / ${sourceLink(s.url)}</p><div class="actions">${btn('从快照建立证据','snapshot-evidence',`data-id="${s.id}"`)}</div><pre class="snapshot-text">${esc(s.text)}</pre>`);});
 else if(action==='snapshot-evidence'){const s=await api('/api/snapshot?id='+encodeURIComponent(id)),source=find(s.source_id);$('#reader').close();currentProject=source.project_id;openEditor('evidence',null,{name:'快照证据：'+source.name,url:s.url,source_id:s.source_id,snapshot_id:s.id,observed_at:s.checked_at,summary:s.text.slice(0,1000)});}
 else if(action==='change'){const c=state.changes.find(x=>x.id===id);read('新旧内容差异',`<p class="hint">${date(c.created)}。变化可能包含导航或日期更新，需要人工判断是否影响决策。</p><pre class="snapshot-text">${esc(c.diff)}</pre>${btn('标记已核查','review-change',`data-id="${id}"`)}`);}
 else if(action==='review-change')await runAction(b,async()=>{await api('/api/changes/review',{id});$('#reader').close();await refresh();});
 else if(action==='sync')await runAction(b,async()=>{const r=await api('/api/sync',{});notice(`已同步 ${r.synced} 份档案；${r.conflict} 份存在冲突。`);});
 else if(action==='backup')await runAction(b,async()=>{const r=await api('/api/backup',{});notice('备份已创建：\n'+r.path);});
 else if(action==='knowledge-search')await runAction(b,async()=>{const hits=await api('/api/search?q='+encodeURIComponent($('#knowledge-input').value)+(currentProject?'&project='+currentProject:''));renderHits(hits,'#knowledge-results');});
 else if(action==='read-note')await runAction(b,async()=>{const r=await api('/api/note?path='+encodeURIComponent(b.dataset.path));if($('#search-dialog').open)$('#search-dialog').close();read(r.path,`<pre class="reader-text">${esc(r.text)}</pre>`);});
 else if(action==='run-discover')await runAction(b,async()=>{const r=await api('/api/discover',{project_id:$('#action-project').value,query:$('#discover-query').value,provider:$('#discover-provider').value});if($('#action-result'))$('#action-result').textContent=`导入 ${r.imported} 条待审核线索，跳过 ${r.skipped} 条重复。`;await refresh();});
 else if(action==='run-import')await runAction(b,async()=>{const type=$('#import-format').value,text=$('#import-text').value,r=await api('/api/import',{project_id:$('#action-project').value,format:type,text,rows:type==='json'?JSON.parse(text):undefined});if($('#action-result'))$('#action-result').textContent=`导入 ${r.imported} 条，跳过 ${r.skipped} 条重复。`;await refresh();});
 else if(action==='run-keywords'||action==='run-ai')await runAction(b,async()=>{const ai=action==='run-ai',r=await api(ai?'/api/ai':'/api/keywords',{project_id:$('#action-project').value,question:ai?$('#paid-input').value:undefined,keywords:ai?undefined:$('#paid-input').value.split('\n').map(x=>x.trim()).filter(Boolean)});if($('#action-result'))$('#action-result').textContent='已保存研究结果，可在决策与历史或知识库阅读。';await refresh();});
});
function renderHits(hits,target){$(target).innerHTML=hits.length?hits.map((h,i)=>`<button class="search-hit" data-action="read-note" data-path="${esc(h.path)}" data-index="${i}"><strong>${esc(h.path)}</strong><span>${esc(h.excerpt)}</span></button>`).join(''):empty('未找到匹配记录','试试具体竞品名称或更短的任务关键词。');}
function openSearch(){if(!$('#search-dialog').open)$('#search-dialog').showModal();$('#search-input').focus();}
$('#search-open').addEventListener('click',openSearch);
$('#search-input').addEventListener('input',()=>{clearTimeout(searchTimer);const version=++searchVersion,query=$('#search-input').value,project=currentProject;searchHits=[];searchIndex=-1;$('#search-results').innerHTML='';searchTimer=setTimeout(async()=>{try{const hits=await api('/api/search?q='+encodeURIComponent(query)+(project?'&project='+project:''));if(version!==searchVersion||project!==currentProject)return;searchHits=hits;renderHits(searchHits,'#search-results');}catch(e){if(version===searchVersion)notice(e.message);}},250);});
$('#search-input').addEventListener('keydown',e=>{if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();const size=searchHits.length;if(!size)return;searchIndex=(searchIndex+(e.key==='ArrowDown'?1:-1)+size)%size;$('#search-results').querySelectorAll('.search-hit').forEach((x,i)=>x.classList.toggle('selected',i===searchIndex));}else if(e.key==='Enter'&&searchIndex>=0){e.preventDefault();$('#search-results').querySelector(`[data-index="${searchIndex}"]`)?.click();}});
$('#project-filter').addEventListener('change',e=>{currentProject=e.target.value;render();});
document.addEventListener('change',e=>{if(e.target.id==='evidence-status')document.querySelectorAll('tr[data-review]').forEach(r=>r.hidden=!!e.target.value&&r.dataset.review!==e.target.value);});
document.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();openSearch();}if(e.key==='Enter'&&e.target.id==='knowledge-input'){e.preventDefault();document.querySelector('[data-action="knowledge-search"]')?.click();}});
window.addEventListener('hashchange',render);
refresh().catch(e=>{$('#content').innerHTML=empty('无法读取本地服务',e.message+'。请确认启动程序仍在运行。');});
setInterval(async()=>{try{const active=state.jobs.some(j=>['queued','running'].includes(j.status));await refresh(false);if(active&&currentView!=='settings'&&!$('#editor').open&&!$('#reader').open)render();}catch{}},5000);
