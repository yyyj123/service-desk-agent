'use strict';
const $=id=>document.getElementById(id);
let user=null,csrf='',conversation=null,busy=false,viewOwner=null,recoveryTimer=null;
function requestError(message,status){const error=new Error(message);error.status=status;return error;}
function setBusy(value){busy=value;$('send').disabled=value;$('question').disabled=value;}
function signedOut(){user=null;csrf='';clearTimeout(recoveryTimer);setBusy(false);$('workspace').hidden=true;$('login-view').hidden=false;if($('mobile-drawer').open)$('mobile-drawer').close();}
const labels={reset_password:'密码重置',request_access:'权限申请',create_ticket:'创建工单',pending:'待处理',completed:'已完成（模拟）',rejected:'已取消'};
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
async function api(path,options={}){const response=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,...options.headers}});let data;try{data=await response.json();}catch{throw new Error('服务返回异常，请稍后重试。');}if(!response.ok){if(response.status===401&&user){signedOut();}throw requestError(typeof data.detail==='string'?data.detail:'请求格式无效，请检查输入。',response.status);}return data;}
function notice(message=''){$('notice').textContent=message;}
function show(view){for(const name of ['chat','actions','audit','knowledge','analytics']){$(`${name}-view`).hidden=name!==view;$(`nav-${name}`).classList.toggle('selected',name===view);}$('page-title').textContent={chat:'自助咨询',actions:user?.role==='admin'?'操作与审批':'我的申请',audit:'审计记录',knowledge:'知识库管理',analytics:'运营统计'}[view];notice();}
async function enter(){user=await api('/api/auth/me');csrf=user.csrf;if(viewOwner!==user.id){const changed=viewOwner!==null;viewOwner=user.id;conversation=null;setBusy(false);if(changed)$('messages').replaceChildren();evidence([]);$('trace').replaceChildren();$('question').value='';show('chat');}$('login-view').hidden=true;$('workspace').hidden=false;const isAdmin=user.role==='admin';$('workspace').classList.toggle('employee-view',!isAdmin);document.querySelector('.evidence').hidden=!isAdmin;document.body.classList.toggle('employee-session',!isAdmin);$('nav-actions').textContent=isAdmin?'操作与审批':'我的申请';$('identity').textContent=`${user.username} · ${user.role==='admin'?'管理员':'员工'}`;for(const name of ['audit','knowledge','analytics'])$('nav-'+name).hidden=user.role!=='admin';const state=await api('/api/status');$('system-status').textContent=isAdmin?`知识库已就绪 · ${state.chunks} 个片段 · 操作需确认`:'查询资料、获取解答或提交服务申请';await refreshConversations();await refreshActions();await recoverPending();}
$('login-form').addEventListener('submit',async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;$('login-error').textContent='';try{const result=await api('/api/auth/login',{method:'POST',body:JSON.stringify({username:$('username').value,password:$('password').value})});csrf=result.csrf_token;$('password').value='';await enter();}catch(error){$('login-error').textContent=error.message;}finally{button.disabled=false;}});
$('logout').onclick=async()=>{try{await api('/api/auth/logout',{method:'POST'});location.reload();}catch(e){notice(e.message);}};
async function refreshConversations(){
 const rows=await api('/api/conversations');$('conversations').replaceChildren();
 for(const row of rows){const b=node('button',row.title);b.onclick=async()=>{if(busy)return;try{
 conversation=row.id;show('chat');$('messages').replaceChildren();let last=null;
 for(const m of await api(`/api/conversations/${row.id}`)){const wrap=appendMessage(m.role,m.content,m.detail?.mode);if(m.role==='assistant'){addSources(wrap,m.detail?.citations||[]);addFeedback(wrap,m.id);if(m.detail)last=m.detail;}}
 evidence(last?.citations||[]);$('trace').replaceChildren(...(last?.trace||[]).map(t=>node('div',t.tool+' · '+t.outcome)));
 if(!last){$('evidence-empty').textContent='该历史会话没有保存引用；新回答会保留引用与工具记录。';$('trace').textContent='没有已保存的工具记录。';}
 $('conversations').classList.remove('mobile-open');$('toggle-history').setAttribute('aria-expanded','false');$('toggle-history').textContent='展开最近会话';
 }catch(e){notice(e.message);}};$('conversations').append(b);}
}
function appendMessage(role,text,mode){const wrap=node('article',undefined,`message ${role}`);const head=node('div',role==='user'?'你':'服务助手','message-head');if(mode)head.append(node('span',{knowledge:'知识库回答',general:'通用建议',blocked:'请求被拦截'}[mode]||mode,'mode'));const body=node('div',text,'message-body');if(role==='assistant')renderAnswer(body,text);wrap.append(head,body);$('messages').append(wrap);return wrap;}
function evidence(rows){$('citations').replaceChildren();$('evidence-empty').hidden=rows.length>0;for(const row of rows){const d=node('details',undefined,'citation');d.append(node('summary',row.title),node('small',row.source),node('p',row.text));$('citations').append(d);}}
function actionItem(action){const n=node('article',undefined,'action');const expired=action.expires*1000<Date.now();n.append(node('span',expired&&action.status==='pending'?'已过期':labels[action.status]||action.status,'status'),node('strong',labels[action.kind]||action.kind));let payload;try{payload=JSON.parse(action.payload);}catch{payload={};}n.append(node('p',`申请人：${action.username||user.username}`),node('p',Object.entries(payload).map(([k,v])=>`${({resource:'资源',reason:'原因',summary:'内容'})[k]||k}：${v}`).join('\n')));if(action.status==='pending'&&!expired){const buttons=node('div',undefined,'buttons');const own=action.user_id===user.id;const available=action.kind==='request_access'?(user.role==='admin'&&!own?['approve','reject']:own?['reject']:[]):own?['confirm','reject']:[];for(const decision of available){const b=node('button',{approve:'批准模拟授权',confirm:'确认模拟执行',reject:'取消 / 拒绝'}[decision],decision==='reject'?'':'primary');b.onclick=async()=>{b.disabled=true;try{await api(`/api/actions/${action.id}/decision`,{method:'POST',body:JSON.stringify({decision})});notice('操作状态已更新。本环境未修改真实企业账号。');await refreshActions();}catch(e){notice(e.message);b.disabled=false;}};buttons.append(b);}n.append(buttons);if(action.kind==='request_access'&&own)n.append(node('small','等待另一位管理员审批。'));}return n;}
async function refreshActions(){const actions=await api('/api/actions');$('actions-list').replaceChildren();$('pending-actions').replaceChildren();$('employee-pending-list').replaceChildren();for(const item of actions){$('actions-list').append(actionItem(item));if(user.role!=='admin'&&item.user_id===user.id&&item.status==='pending'&&item.expires*1000>Date.now())$('employee-pending-list').append(actionItem(item));if(item.status==='pending'&&item.expires*1000>Date.now()&&item.user_id===user.id)$('pending-actions').append(actionItem(item));}$('employee-pending').hidden=user.role==='admin'||!$('employee-pending-list').children.length;if(!actions.length)$('actions-list').append(node('p','还没有操作申请。可以在咨询中提出密码重置、资源权限或工单请求。','empty'));if(!$('pending-actions').children.length)$('pending-actions').append(node('p','暂无待确认操作','empty'));}
async function readChatStream(body,onEvent){
 const response=await fetch('/api/chat/stream',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(body)});
 if(!response.ok){const error=await response.json();if(response.status===401)signedOut();throw requestError(error.detail||'请求失败',response.status);}
 const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',result=null;
 while(true){const {value,done}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});let split;
 while((split=buffer.indexOf('\n\n'))>=0){const raw=buffer.slice(0,split);buffer=buffer.slice(split+2);if(!raw.startsWith('data: '))continue;const event=JSON.parse(raw.slice(6));if(event.type==='error')throw requestError(event.data.message,event.data.status);onEvent(event);if(event.type==='done')result=event.data;}
 if(done)break;
 }
 if(!result)throw new Error('连接中断，服务仍可能完成请求。刷新页面可恢复结果，请勿重复提交。');
 return result;
}
function finishAnswer(wrap,result){
 conversation=result.conversation_id;renderAnswer(wrap.querySelector('.message-body'),result.answer);
 const head=wrap.querySelector('.message-head');head.textContent='服务助手';head.append(node('span',{knowledge:'知识库回答',general:'通用建议',blocked:'请求被拦截'}[result.mode]||result.mode,'mode'));
 addSources(wrap,result.citations||[]);evidence(result.citations);$('trace').replaceChildren(...result.trace.map(t=>node('div',t.tool+' · '+t.outcome)));addFeedback(wrap,result.message_id);
}
async function recoverPending(){
 if(!user)return;const owner=user.id,key='pending-chat-'+owner,id=sessionStorage.getItem(key);if(!id)return;
 clearTimeout(recoveryTimer);setBusy(true);
 try{const state=await api('/api/chat/requests/'+encodeURIComponent(id));if(user?.id!==owner)return;
 if(state.status==='complete'){sessionStorage.removeItem(key);setBusy(false);notice('上次请求已完成，已恢复回答。');const wrap=appendMessage('assistant','');finishAnswer(wrap,state.result);await refreshConversations();await refreshActions();}
 else if(state.status==='failed'){sessionStorage.removeItem(key);setBusy(false);notice('上次生成失败，请先核对待办中是否已有操作申请。');await refreshActions();}
 else{notice('上次请求仍在处理中，正在等待恢复结果…');recoveryTimer=setTimeout(recoverPending,3000);}
 }catch(e){if(user?.id!==owner)return;if(e.status===404){sessionStorage.removeItem(key);setBusy(false);notice('上次请求未被接收，可以重新发送。');}else{notice(e.message);recoveryTimer=setTimeout(recoverPending,5000);}}
}
async function send(text){
 if(busy||!user||!text.trim())return;if(sessionStorage.getItem('pending-chat-'+user.id)){await recoverPending();return;}show('chat');notice();busy=true;$('send').disabled=true;$('question').disabled=true;
 evidence([]);$('trace').replaceChildren();const welcome=document.querySelector('.welcome');if(welcome)welcome.remove();appendMessage('user',text);
 const wrap=appendMessage('assistant',''),body=wrap.querySelector('.message-body');const progress=node('div','正在连接…','busy');wrap.append(progress);
 const requestId=crypto.randomUUID(),key='pending-chat-'+user.id;sessionStorage.setItem(key,requestId);
 try{const result=await readChatStream({message:text,conversation_id:conversation,request_id:requestId},event=>{
 if(event.type==='status')progress.textContent=user.role==='admin'?event.data:'正在为你查询…';
 if(event.type==='answer_start')body.textContent=event.data.prefix||'';
 if(event.type==='delta'){body.textContent+=event.data;progress.textContent='正在生成回答…';}
 if(event.type==='tool')$('trace').append(node('div',event.data.tool+' · '+event.data.outcome));
 });finishAnswer(wrap,result);sessionStorage.removeItem(key);$('question').value='';await refreshConversations();await refreshActions();}
 catch(e){notice(e.message);if(e.status){sessionStorage.removeItem(key);if(!body.textContent)body.textContent=e.message;}else{if(!body.textContent)body.textContent='连接中断，正在核对请求结果，请勿重复提交。';recoveryTimer=setTimeout(recoverPending,1000);}}
 finally{progress.remove();setBusy(!!sessionStorage.getItem(key));if(!busy&&user)$('question').focus();}
}
function addFeedback(wrap,messageId){
 if(!messageId)return;const box=node('details',undefined,'answer-feedback');box.append(node('summary','评价这条回答'));
 const comment=node('textarea');comment.rows=2;comment.maxLength=500;comment.placeholder='可选：哪里有帮助，或哪里需要改进';comment.setAttribute('aria-label','反馈说明');box.append(comment);
 const status=node('span','','feedback-status');status.setAttribute('role','status');const actions=node('div',undefined,'form-actions');
 for(const [rating,label] of [[1,'有帮助'],[-1,'未解决']]){const button=node('button',label);button.type='button';button.onclick=async()=>{button.disabled=true;try{await api('/api/feedback',{method:'POST',body:JSON.stringify({message_id:messageId,rating,comment:comment.value})});status.textContent='已记录：'+label;}catch(e){status.textContent=e.message;}finally{button.disabled=false;}};actions.append(button);}
 box.append(actions,status);wrap.append(box);
}
$('chat-form').onsubmit=e=>{e.preventDefault();send($('question').value);};
$('question').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();send($('question').value);}});
document.querySelectorAll('[data-question]').forEach(b=>b.onclick=()=>send(b.dataset.question));
$('new-chat').onclick=()=>{if(busy)return;conversation=null;show('chat');$('messages').replaceChildren(node('div','新的会话已就绪。请描述需要解决的问题。','empty'));evidence([]);$('trace').textContent='还没有工具调用。';$('question').value='';$('question').focus();};
$('toggle-history').onclick=()=>{const open=$('conversations').classList.toggle('mobile-open');$('toggle-history').setAttribute('aria-expanded',String(open));$('toggle-history').textContent=open?'收起最近会话':'展开最近会话';};
$('nav-chat').onclick=()=>show('chat');
$('nav-actions').onclick=async()=>{show('actions');try{await refreshActions();}catch(e){notice(e.message);}};
async function audit(){const data=await api('/api/admin/audit');$('audit-integrity').textContent=data.integrity?'审计链校验通过 · 显示最近 100 条记录':'审计链校验失败，需要调查';$('audit-list').replaceChildren();for(const event of data.events){const row=node('tr');for(const text of [new Date(event.ts*1000).toLocaleString('zh-CN'),event.event,event.target.slice(0,12)||'—',event.actor.slice(0,12)])row.append(node('td',text));$('audit-list').append(row);}}
$('nav-audit').onclick=async()=>{show('audit');try{await audit();}catch(e){notice(e.message);}};
$('refresh-actions').onclick=()=>refreshActions().catch(e=>notice(e.message));
$('refresh-audit').onclick=()=>audit().catch(e=>notice(e.message));
document.querySelector('#login-form button[type="submit"]').disabled=false;
enter().catch(()=>{});

async function loadDemoAccounts(){
  try {
    const response=await fetch('/api/demo-accounts',{cache:'no-store'});
    if(!response.ok)return;
    const data=await response.json();
    for(const account of data.accounts){
      const row=node('div','','demo-account');
      row.append(node('strong',account.username==='admin'?'管理员 · 审批与审计':'员工 · 咨询与申请'));
      row.append(node('div','账号：'+account.username));
      row.append(node('div','密码：'+account.password,'demo-password'));
      const fill=node('button','填入'+(account.username==='admin'?'管理员':'员工')+'账号');
      fill.type='button';
      fill.onclick=()=>{$('username').value=account.username;$('password').value=account.password;$('login-error').textContent='';document.querySelector('#login-form button[type="submit"]').focus();};
      row.append(fill);$('demo-accounts').append(row);
    }
    $('demo-login').hidden=!data.accounts.length;
  }catch{/* Manual login remains available if demo details cannot be loaded. */}
}
loadDemoAccounts();

let editingDocument=null;
function resetDocumentForm(){editingDocument=null;$('knowledge-form').reset();$('knowledge-form-title').textContent='上传文档';$('upload-document').textContent='上传并入库';$('cancel-document').hidden=true;}
async function loadKnowledge(){
 $('knowledge-list').textContent='正在读取…';
 try{const docs=await api('/api/admin/knowledge');$('knowledge-list').replaceChildren();if(!docs.length)$('knowledge-list').append(node('p','尚未上传文档。可从上方添加第一份资料。','empty'));
 for(const doc of docs){const row=node('article',undefined,'knowledge-row');row.append(node('h3',doc.title),node('p',`${doc.source} · ${doc.visibility==='admin'?'仅管理员':'员工可见'} · 第 ${doc.revision} 版 · ${doc.chunks} 个片段 · 已入库`));const actions=node('div',undefined,'form-actions');
 const edit=node('button','替换文档');edit.onclick=()=>{editingDocument=doc;$('document-title').value=doc.title;$('document-visibility').value=doc.visibility;$('document-file').value='';$('knowledge-form-title').textContent='替换：'+doc.title;$('upload-document').textContent='保存新版本';$('cancel-document').hidden=false;$('knowledge-form').scrollIntoView({behavior:'smooth'});};
 const remove=node('button','删除');remove.onclick=async()=>{if(!confirm('删除“'+doc.title+'”？删除后不再参与新问题检索。'))return;remove.disabled=true;try{await api('/api/admin/knowledge/'+doc.id+'?revision='+doc.revision,{method:'DELETE'});if(editingDocument?.id===doc.id)resetDocumentForm();await loadKnowledge();}catch(e){notice(e.message);remove.disabled=false;}};
 actions.append(edit,remove);row.append(actions);$('knowledge-list').append(row);}}
 catch(e){$('knowledge-list').textContent=e.message;}
}
$('knowledge-form').onsubmit=async e=>{
 e.preventDefault();const file=$('document-file').files[0];if(!file)return;const target=editingDocument,title=$('document-title').value,visibility=$('document-visibility').value;
 if(file.size>1000000){$('document-status').textContent='文件超过 1 MB，请拆分后上传。';return;}
 const button=$('upload-document');button.disabled=true;$('cancel-document').disabled=true;$('document-status').textContent='正在解析并建立索引，请稍候…';
 try{const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});
 const body={title,filename:file.name,visibility,content_base64:encoded,revision:target?.revision||null};
 const result=await api('/api/admin/knowledge'+(target?'/'+target.id:''),{method:target?'PUT':'POST',body:JSON.stringify(body)});
 resetDocumentForm();$('document-status').textContent=`入库成功：第 ${result.revision} 版，共 ${result.chunks} 个片段。`;await loadKnowledge();
 }catch(e){$('document-status').textContent=e.message;}finally{button.disabled=false;$('cancel-document').disabled=false;}
};
$('cancel-document').onclick=resetDocumentForm;
$('nav-knowledge').onclick=()=>{if(busy)return;show('knowledge');loadKnowledge();};$('refresh-knowledge').onclick=loadKnowledge;
async function loadAnalytics(){
 try{const data=await api('/api/admin/analytics');$('analytics-summary').replaceChildren();
 for(const [label,value] of [['请求数',data.requests],['知识库回答',data.knowledge],['通用兜底',data.general],['已拦截',data.blocked],['反馈数',data.feedback_count],['点赞率',data.satisfaction===null?'暂无反馈':Math.round(data.satisfaction*100)+'%'],['完整回答 P95',data.p95_seconds===null?'暂无数据':data.p95_seconds.toFixed(2)+' 秒']]){const group=node('div');group.append(node('dt',label),node('dd',String(value)));$('analytics-summary').append(group);}
 for(const [id,rows] of [['top-questions',data.top_questions.map(r=>r.question+' · '+r.count+' 次')],['unanswered-questions',data.unanswered],['daily-requests',data.daily.map(r=>r.date+' · '+r.count+' 次')]]){$(id).replaceChildren(...(rows.length?rows:['暂无数据']).map(text=>node('li',text)));}
 $('feedback-list').replaceChildren(...data.feedback.map(r=>node('p',(r.rating===1?'有帮助':'未解决')+' · '+r.question+(r.comment?' — '+r.comment:''))));if(!data.feedback.length)$('feedback-list').textContent='暂无反馈。';
 }catch(e){notice(e.message);}
}
$('nav-analytics').onclick=()=>{if(busy)return;show('analytics');loadAnalytics();};$('refresh-analytics').onclick=loadAnalytics;

function renderAnswer(element,text){
 element.replaceChildren();
 // Render only emphasis and inline code; model-provided HTML is always literal text.
 const pattern=/(\*\*[^*\n]+\*\*|`[^`\n]+`)/g;let end=0;
 for(const match of text.matchAll(pattern)){element.append(document.createTextNode(text.slice(end,match.index)));const bold=match[0].startsWith('**');element.append(node(bold?'strong':'code',match[0].slice(bold?2:1,bold?-2:-1)));end=match.index+match[0].length;}
 element.append(document.createTextNode(text.slice(end)));
}

function addSources(wrap,rows){if(user?.role==='admin'||!rows.length)return;const box=node('details',undefined,'answer-sources');box.append(node('summary','查看来源'));for(const row of rows){const item=node('details',undefined,'citation');item.append(node('summary',row.title),node('small',row.source),node('p',row.text));box.append(item);}wrap.append(box);}

// Native modal dialog supplies focus trapping, Escape and an inert background.
const phoneLayout=matchMedia('(max-width:600px)');
const drawer=$('mobile-drawer'),rail=$('navigation-rail');
const mobileDetails=node('details',undefined,'mobile-admin-details');mobileDetails.append(node('summary','回答详情'));
const detailPanel=document.querySelector('.evidence');
function closeDrawer(){if(drawer.open)drawer.close();}
function syncDrawer(){
 closeDrawer();
 if(phoneLayout.matches){drawer.append(rail);rail.insertBefore(mobileDetails,rail.querySelector('.identity'));mobileDetails.append(detailPanel);}else{$('workspace').prepend(rail);$('chat-view').append(detailPanel);mobileDetails.remove();}
}
$('open-menu').onclick=()=>{drawer.showModal();document.body.classList.add('drawer-open');};
$('close-menu').onclick=closeDrawer;
drawer.addEventListener('close',()=>{document.body.classList.remove('drawer-open');});
drawer.addEventListener('click',event=>{if(event.target===drawer){const rect=drawer.getBoundingClientRect();if(event.clientX>rect.right||event.clientX<rect.left)closeDrawer();}});
rail.addEventListener('click',event=>{if(event.target.closest('.nav-button,#new-chat,#conversations button,#logout'))closeDrawer();});
phoneLayout.addEventListener('change',syncDrawer);
syncDrawer();
