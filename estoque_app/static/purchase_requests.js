(() => {
"use strict";
const configNode = document.getElementById("pr-config");
if (!configNode) return;
const cfg = JSON.parse(configNode.textContent);
const $ = id => document.getElementById(id);
const labels = {SOLICITADA:"Solicitada",EM_COMPRAS:"Em compras",CONCLUIDA:"Concluído",CANCELADA:"Cancelada"};
const referenceReviewLabels={VINCULADA:"Vinculada automaticamente",VINCULADA_PARCIAL:"Vinculada parcialmente; revisar",AMBIGUA:"Referência ambígua; revisar",SEM_MATCH:"Sem correspondência exata",SEM_REFERENCIA:"Sem referência anterior",REVISADA_MANUALMENTE:"Revisada manualmente"};
const date = value => !value ? "—" : /^\d{4}-\d{2}-\d{2}$/.test(value) ? value.split("-").reverse().join("/") : new Date(value).toLocaleString("pt-BR",{timeZone:"America/Sao_Paulo"});
const qty = value => Number(value).toLocaleString("pt-BR",{maximumFractionDigits:3});
const node = (tag,text,className) => {const n=document.createElement(tag);if(text!=null)n.textContent=text;if(className)n.className=className;return n;};
let page=1,total=0,rows=[],key=crypto.randomUUID(),loading=false,closingAnticipationRow=null;
let editingRow=null,editSkuTimer=null,allocatingRow=null,allocationOptions=[];
let loadSerial=0;
function message(text,error=false){$("pr-message").textContent=text;$("pr-message").classList.toggle("pr-error",error);}
async function api(path="",payload){
 const response=await fetch(cfg.api+path,{method:payload?"POST":"GET",headers:{"Accept":"application/json","Content-Type":"application/json","X-CSRF-Token":cfg.csrf},...(payload?{body:JSON.stringify(payload)}:{})});
 const data=await response.json().catch(()=>({}));
 if(!response.ok||!data.ok)throw new Error(data.error||"Não foi possível consultar o workflow. Recarregue e tente novamente.");
 return data;
}
const today=()=>new Date().toLocaleDateString("en-CA",{timeZone:"America/Sao_Paulo"});
function button(text,fn){const b=node("button",text);b.type="button";b.addEventListener("click",fn);return b;}
function render(){
 const body=$("pr-rows");body.replaceChildren();
 for(const row of rows){
  const tr=node("tr");const pending=["SOLICITADA","EM_COMPRAS"].includes(row.status);
  const anticipation=row.request_type==="ANTECIPACAO";
  if(cfg.can_manage){const cell=node("td"),check=node("input");check.type="checkbox";check.value=row.id;check.className="pr-select";check.disabled=!pending||anticipation;check.title=anticipation?"Esta solicitação deve ser tratada como antecipação do pedido vigente.":"";cell.append(check);tr.append(cell);}
  const orderNumber=row.numero_oc||row.anticipation_numero_oc;
  const supplier=row.fornecedor_nome||row.anticipation_fornecedor_nome;
  const orderStatus=row.purchase_status||row.anticipation_purchase_status;
  const values=["SOL-"+row.id.slice(0,8).toUpperCase(),row.origin==="ESTOQUE"?"Almoxarifado":"PCP",anticipation?"ANTECIPAÇÃO":"NOVA COMPRA",row.sku_codigo,row.descricao,row.unidade,qty(row.quantity),date(row.needed_at),row.status,row.reference_display||row.reference,row.requested_by,date(row.created_at),row.buyer||"—",date(row.updated_at),orderNumber?(anticipation&&!row.numero_oc?"Antecipar · OC "+orderNumber:orderNumber):"—",supplier||"—",orderStatus||"—",anticipation?qty(row.anticipation_pending_quantity||0):"—",date(row.anticipation_confirmed_delivery_date),date(row.completed_at),row.completed_by||"—",row.notes];
  values.forEach((value,index)=>{
   const cell=node("td",value||"—");
   if(index===9)cell.className="pr-reference-cell";
   if(index===2){const badge=node("span",value,"pr-badge "+(anticipation?"ANTECIPACAO":"COMPRA_NOVA"));if(anticipation){badge.style.background="#fff0d1";badge.style.color="#804900";}cell.replaceChildren(badge);}
   if(index===8){cell.replaceChildren(node("span",labels[row.status]||row.status,"pr-badge "+row.status));}
   if(index===7&&pending&&row.needed_at<today())cell.className="pr-overdue";
   if(index===14&&anticipation){cell.title="Pedido mais próximo da data de necessidade: "+date(row.anticipation_delivery_date);}
   tr.append(cell);
  });
  const actions=node("td");actions.append(button("Histórico",()=>showHistory(row.id)));
  const isOwner=String(row.requested_by_id)===String(cfg.user_id);
  const canEditOrigin=cfg.can_edit_origin&&row.origin===cfg.origin&&row.status==="SOLICITADA";
  const canEditRequest=pending&&(cfg.can_manage||canEditOrigin||(isOwner&&row.status==="SOLICITADA"));
  if(canEditRequest){actions.append(button("Editar",()=>openEdit(row)));actions.append(button("Excluir",()=>excludeRequest(row)));}
  if(cfg.can_manage){
   if(row.status==="SOLICITADA")actions.append(button(anticipation?"Registrar solicitação ao fornecedor":"Assumir",()=>act(row,anticipation?"SOLICITAR_ANTECIPACAO":"ASSUMIR")));
   if(pending)actions.append(button("Alocar O.C. existente",()=>openAllocateOrder(row)));
   if(anticipation&&row.status==="EM_COMPRAS")actions.append(button("Confirmar data negociada",()=>openAnticipationConfirmation(row)));
   if(anticipation&&pending)actions.append(button("Converter em nova O.C.",()=>act(row,"CONVERTER_NOVA_COMPRA")));
   if(row.status==="CANCELADA")actions.append(button("Reabrir",()=>act(row,"REABRIR")));
   actions.append(button("Observação",()=>act(row,"OBSERVACAO")));
  }
  tr.append(actions);body.append(tr);
 }
 if(!rows.length){const tr=node("tr"),td=node("td","Nenhuma solicitação para os filtros selecionados.");td.colSpan=cfg.can_manage?24:23;tr.append(td);body.append(tr);}
 $("pr-page").textContent=total+" solicitações · página "+page+" de "+Math.max(1,Math.ceil(total/100));
 $("pr-prev").disabled=page<=1;$("pr-next").disabled=page*100>=total;
}
async function load(){
 const serial=++loadSerial;
 try{
  const params=new URLSearchParams({page,q:$("pr-search").value,status:$("pr-status").value,origin:$("pr-origin").value,from:$("pr-from").value,to:$("pr-to").value});
  const data=await api("?"+params);if(serial!==loadSerial)return;
  rows=data.items;total=data.total;page=data.page;render();
  $("pr-metrics").replaceChildren();
  for(const [code,label]of Object.entries(labels)){const box=node("div",label,"pr-metric");box.append(node("strong",data.counts[code]||0));$("pr-metrics").append(box);}
  const overdue=node("div","Necessidade vencida","pr-metric pr-overdue");overdue.append(node("strong",data.overdue));$("pr-metrics").append(overdue);
 }catch(e){message(e.message,true);}
}
async function fillSkuOptions(select,query,selectedCode=""){
 const data=await api("/options?q="+encodeURIComponent(query||""));
 select.replaceChildren(node("option","Selecione um SKU ativo"));select.firstChild.value="";
 const items=data.items||[];
 if(selectedCode&&!items.some(item=>item.sku_codigo===selectedCode))items.unshift({sku_codigo:selectedCode,descricao:"SKU atual",unidade:""});
 for(const item of items){const option=node("option",item.sku_codigo+" · "+item.descricao+(item.unidade?" ("+item.unidade+")":""));option.value=item.sku_codigo;select.append(option);}
}
function initWorkOrderPicker(form){
 const root=form?.querySelector("[data-pr-reference-picker]");
 if(!root)return null;
 const search=root.querySelector("[data-pr-work-order-search]");
 const results=root.querySelector("[data-pr-work-order-results]");
 const selected=root.querySelector("[data-pr-work-order-selected]");
 const choices=new Map();let available=[];let timer=null;let serial=0;
 function draw(){
  selected.replaceChildren();
  if(!choices.size){selected.append(node("span","Nenhuma O.S. vinculada.","pr-reference-empty"));return;}
  for(const [id,item] of choices){
   const chip=node("span",null,"pr-reference-chip");chip.append(node("span",item.label||("O.S. "+(item.numero_os||item.item_number||id))));
   const remove=button("×",()=>{choices.delete(id);draw();drawResults();});remove.title="Remover vínculo";chip.append(remove);selected.append(chip);
  }
 }
 function drawResults(){
  results.replaceChildren();
  if(!available.length){results.append(node("span","Nenhuma O.S. aberta encontrada. Ajuste a busca.","pr-reference-empty"));return;}
  for(const item of available){
   const id=String(item.id);const label=node("label",null,"pr-reference-option");const check=node("input");check.type="checkbox";check.checked=choices.has(id);
   check.addEventListener("change",()=>{if(check.checked)choices.set(id,item);else choices.delete(id);draw();});
   label.append(check,node("span",item.label||("O.S. "+(item.numero_os||item.item_number||id))));results.append(label);
  }
 }
 async function load(query=""){
  const request=++serial;
  try{const data=await api("/work-orders?q="+encodeURIComponent(query));if(request!==serial)return;available=data.items||[];drawResults();}
  catch(error){if(request===serial){available=[];results.replaceChildren(node("span",error.message,"pr-reference-empty"));}}
 }
 search.addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(()=>load(search.value.trim()),250);});
 draw();load("");
 return {
  ids:()=>Array.from(choices.keys()),
  setSelected(items){choices.clear();for(const item of items||[])if(item?.id)choices.set(String(item.id),item);draw();drawResults();},
  clear(){choices.clear();draw();drawResults();search.value="";load("");}
 };
}
const createReferencePicker=$("pr-form")?initWorkOrderPicker($("pr-form")):null;
const editReferencePicker=$("pr-edit-form")&&(cfg.can_submit||cfg.can_edit_origin||cfg.can_manage)
 ?initWorkOrderPicker($("pr-edit-form")):null;
async function openEdit(row){
 editingRow=row;
 const form=$("pr-edit-form");form.elements.quantity.value=row.quantity;form.elements.needed_at.value=row.needed_at;
 form.elements.sector.value=row.sector||"GERAL";form.elements.notes.value=row.notes||"";form.elements.reason.value="";
 editReferencePicker?.setSelected(row.work_orders||[]);
 $("pr-edit-sku-search").value=row.sku_codigo;
 try{await fillSkuOptions($("pr-edit-sku"),row.sku_codigo,row.sku_codigo);form.elements.sku_codigo.value=row.sku_codigo;$("pr-edit-request").showModal();}
 catch(e){message(e.message,true);}
}
async function excludeRequest(row){
 if(!confirm("Excluir esta solicitação? Ela será cancelada e mantida no histórico de rastreabilidade."))return;
 const reason=prompt("Informe o motivo da exclusão/cancelamento:");if(!reason||!reason.trim())return;
 try{await api("/"+encodeURIComponent(row.id)+"/action",{action:"EXCLUIR",reason:reason.trim(),version:row.version});message("Solicitação excluída e preservada no histórico.");await load();}
 catch(e){message(e.message,true);}
}
async function showHistory(id){
 try{
  const data=await api("/"+encodeURIComponent(id)+"/history"),content=$("pr-history-content");content.replaceChildren();
  content.append(node("h3",data.request.sku_codigo+" · "+data.request.descricao));
  content.append(node("p",qty(data.request.quantity)+" "+data.request.unidade+" · Necessidade: "+date(data.request.needed_at)+" · Solicitante: "+data.request.requested_by));
  for(const event of data.events){
   const box=node("section",null,"pr-event");
   box.append(node("strong",event.action==="NORMALIZACAO_REFERENCIAS"?"Padronização de referências históricas":event.action.replaceAll("_"," ")),node("p",event.actor+" · "+date(event.created_at)));
   const before=event.before_data||{},after=event.after_data||{};
   if(before.status!==after.status)box.append(node("p",(labels[before.status]||"Nova solicitação")+" → "+(labels[after.status]||after.status)));
   if(event.action==="EDITAR"){
    const fields={sku_codigo:"SKU",descricao:"Descrição",unidade:"Unidade",quantity:"Quantidade",needed_at:"Data de necessidade",sector:"Setor",reference:"Referência",notes:"Observações",request_type:"Classificação"};
    for(const [key,label] of Object.entries(fields))if(String(before[key]??"")!==String(after[key]??""))box.append(node("p",label+": "+(before[key]||"—")+" → "+(after[key]||"—")));
    const beforeOrders=(before.work_orders||[]).map(item=>item.label||item.numero_os||item.id).join(", ");
    const afterOrders=(after.work_orders||[]).map(item=>item.label||item.numero_os||item.id).join(", ");
    if(beforeOrders!==afterOrders)box.append(node("p","O.S. vinculadas: "+(beforeOrders||"—")+" → "+(afterOrders||"—")));
   }
   if(event.action==="NORMALIZACAO_REFERENCIAS"){
    const result=after.reference_backfill_result||"—";
    box.append(node("p",result==="REVISADA_MANUALMENTE"?"Vínculos históricos revisados manualmente.":"Resultado da vinculação: "+(referenceReviewLabels[result]||result)));
    box.append(node("p","Setor padronizado: "+(after.sector||"GERAL")));
    const linked=(after.work_orders||[]).map(item=>item.label||item.numero_os||item.id).join(", ");
    box.append(node("p","O.S. vinculadas: "+(linked||"nenhuma correspondência exata encontrada")));
    if((after.unresolved_tokens||[]).length)box.append(node("p","Tokens sem correspondência automática: "+after.unresolved_tokens.join(", ")));
   }
   if(after.buyer&&before.buyer!==after.buyer)box.append(node("p","Comprador: "+after.buyer));
   if(after.purchase_order)box.append(node("p","Pedido: "+after.purchase_order.numero_oc+" · Fornecedor: "+after.purchase_order.fornecedor_nome+" · "+after.purchase_order.status));
   if(after.purchase_order_id)box.append(node("p","Pedido vinculado (ID): "+after.purchase_order_id));
   if(after.anticipation_confirmed_delivery_date&&before.anticipation_confirmed_delivery_date!==after.anticipation_confirmed_delivery_date)box.append(node("p","Nova data de entrega negociada: "+date(after.anticipation_confirmed_delivery_date)));
   if(before.purchase_order_id&&!after.purchase_order_id)box.append(node("p","Pedido anterior: "+before.purchase_order_id+" · solicitação reaberta"));
   if(event.reason)box.append(node("p",event.reason));
   content.append(box);
  }
  $("pr-history").showModal();
 }catch(e){message(e.message,true);}
}
function openAnticipationConfirmation(row){
 closingAnticipationRow=row;
 $("pr-confirm-anticipation-order").textContent="O.C. "+(row.anticipation_numero_oc||"—")+" · "+(row.anticipation_fornecedor_nome||"Fornecedor não informado")+" · "+row.sku_codigo;
 $("pr-confirm-anticipation-date").value="";
 $("pr-confirm-anticipation-reason").value="";
 $("pr-confirm-anticipation").showModal();
}
async function openAllocateOrder(row){
 allocatingRow=row;
 const select=$("pr-allocate-select"),details=$("pr-allocate-details");
 select.replaceChildren(Object.assign(node("option","Selecione uma O.C."),{value:""}));
 details.textContent="";$("pr-allocate-reason").value="";
 $("pr-allocate-request").textContent="SOL-"+row.id.slice(0,8).toUpperCase()+" · "+row.sku_codigo+" · "+qty(row.quantity)+" "+row.unidade;
 try{
  const data=await api("/"+encodeURIComponent(row.id)+"/orders");allocationOptions=data.items||[];
  if(!allocationOptions.length){allocatingRow=null;return message("Não há O.C. vigente com saldo suficiente para esta solicitação.",true);}
  for(const order of allocationOptions){
   const option=node("option","OC "+(order.numero_oc||"—")+" · "+(order.fornecedor_nome||"Fornecedor não informado")+" · saldo "+qty(order.available_quantity));
   option.value=order.id;select.append(option);
  }
  select.onchange=()=>{
   const selected=allocationOptions.find(order=>order.id===select.value);
   details.textContent=selected?"Entrega prevista: "+date(selected.delivery_date)+" · saldo disponível para alocação: "+qty(selected.available_quantity):"";
  };
  $("pr-allocate-order").showModal();
 }catch(error){allocatingRow=null;message(error.message,true);}
}
async function allocateExistingOrder(){
 const row=allocatingRow,orderId=$("pr-allocate-select").value,reason=$("pr-allocate-reason").value.trim();
 if(!row||!orderId||!reason)return message("Selecione a O.C. e registre a confirmação/justificativa.",true);
 const save=$("pr-allocate-save");save.disabled=true;
 try{
  await api("/"+encodeURIComponent(row.id)+"/action",{action:"ALOCAR_PEDIDO",purchase_order_id:orderId,version:row.version,reason});
  $("pr-allocate-order").close();allocatingRow=null;allocationOptions=[];
  message("O.C. vinculada e solicitação concluída.");await load();
 }catch(error){message(error.message,true);}
 finally{save.disabled=false;}
}
async function confirmAnticipation(){
 const row=closingAnticipationRow,confirmedDate=$("pr-confirm-anticipation-date").value,reason=$("pr-confirm-anticipation-reason").value.trim();
 if(!row)return;
 if(!confirmedDate||!reason)return message("Informe a nova data negociada e o retorno/protocolo do fornecedor.",true);
 $("pr-confirm-anticipation-save").disabled=true;
 try{
  await api("/"+encodeURIComponent(row.id)+"/action",{action:"CONFIRMAR_ANTECIPACAO",version:row.version,confirmed_delivery_date:confirmedDate,reason});
  $("pr-confirm-anticipation").close();closingAnticipationRow=null;
  message("Nova data negociada registrada; solicitação encerrada e vinculada à O.C. existente.");await load();
 }catch(e){message(e.message,true);}
 finally{$("pr-confirm-anticipation-save").disabled=false;}
}
async function act(row,action){
 const reason=prompt(action==="OBSERVACAO"?"Observação/retorno do fornecedor para a linha do tempo:":action==="SOLICITAR_ANTECIPACAO"?"Após contatar o fornecedor sobre a antecipação da OC indicada, informe o protocolo ou retorno. O sistema registra o contato, mas não envia mensagem ao fornecedor:":action==="CONVERTER_NOVA_COMPRA"?"Justifique por que a antecipação não atende e esta solicitação precisa virar uma nova O.C.:":"Informe o motivo para "+action.toLowerCase()+":");
 if(!reason||!reason.trim())return;
 try{await api("/"+encodeURIComponent(row.id)+"/action",{action,reason,version:row.version});message("Atualização registrada no histórico.");await load();}catch(e){message(e.message,true);}
}
$("pr-close-history").onclick=()=>$("pr-history").close();
$("pr-confirm-anticipation-cancel").onclick=()=>{$("pr-confirm-anticipation").close();closingAnticipationRow=null;};
$("pr-confirm-anticipation-save").onclick=confirmAnticipation;
$("pr-allocate-cancel").onclick=()=>{$("pr-allocate-order").close();allocatingRow=null;allocationOptions=[];};
$("pr-allocate-save").onclick=allocateExistingOrder;
$("pr-edit-cancel").onclick=()=>{$("pr-edit-request").close();editingRow=null;};
$("pr-edit-sku-search").oninput=()=>{clearTimeout(editSkuTimer);editSkuTimer=setTimeout(()=>fillSkuOptions($("pr-edit-sku"),$("pr-edit-sku-search").value).catch(e=>message(e.message,true)),250);};
$("pr-edit-form").onsubmit=async event=>{
 event.preventDefault();if(!editingRow)return;
 const payload=Object.fromEntries(new FormData(event.target));payload.work_order_ids=editReferencePicker?editReferencePicker.ids():(editingRow.work_order_ids||[]);payload.action="EDITAR";payload.version=editingRow.version;
 $("pr-edit-save").disabled=true;
 try{await api("/"+encodeURIComponent(editingRow.id)+"/action",payload);$("pr-edit-request").close();editingRow=null;message("Solicitação editada e registrada no histórico.");await load();}
 catch(e){message(e.message,true);}
 finally{$("pr-edit-save").disabled=false;}
};
$("pr-reload").onclick=()=>load();
$("pr-filter").onclick=()=>{page=1;load();};
$("pr-prev").onclick=()=>{page--;load();};
$("pr-next").onclick=()=>{page++;load();};
if($("pr-convert"))$("pr-convert").onclick=()=>{
 const ids=Array.from(document.querySelectorAll(".pr-select:checked")).map(n=>n.value);
 if(!ids.length)return message("Selecione ao menos uma solicitação pendente.",true);
 location.href="/?tab=oc&purchase_requests="+encodeURIComponent(ids.join(","));
};
if($("pr-form")){
 let timer,optionsSerial=0;
 async function options(){
  const serial=++optionsSerial;
  try{const data=await api("/options?q="+encodeURIComponent($("pr-sku-search").value));
   if(serial!==optionsSerial)return;
   $("pr-sku").replaceChildren(node("option","Selecione um SKU ativo"));$("pr-sku").firstChild.value="";
   for(const item of data.items){const option=node("option",item.sku_codigo+" · "+item.descricao+" ("+item.unidade+")");option.value=item.sku_codigo;$("pr-sku").append(option);}
  }catch(e){message(e.message,true);}
 }
 $("pr-sku-search").oninput=()=>{clearTimeout(timer);timer=setTimeout(options,300);};
 $("pr-form").onsubmit=async event=>{
  event.preventDefault();if(loading)return;
  const payload=Object.fromEntries(new FormData(event.target));payload.sku_codigo=$("pr-sku").value;payload.work_order_ids=createReferencePicker?.ids()||[];payload.idempotency_key=key;
  loading=true;$("pr-submit").disabled=true;
  try{const data=await api("",payload);key=crypto.randomUUID();event.target.reset();createReferencePicker?.clear();message(data.request.request_type==="ANTECIPACAO"?"Solicitação SOL-"+data.request.id.slice(0,8).toUpperCase()+" identificada como ANTECIPAÇÃO do pedido "+(data.anticipation_order?.numero_oc||"já vigente")+" e encaminhada à fila do comprador.":"Solicitação SOL-"+data.request.id.slice(0,8).toUpperCase()+" enviada e registrada no histórico.");page=1;await load();}
  catch(e){message(e.message,true);}
  finally{loading=false;$("pr-submit").disabled=false;}
 };
 options();
}
load();
})();
