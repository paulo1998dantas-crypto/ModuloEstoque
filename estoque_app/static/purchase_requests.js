(() => {
"use strict";
const configNode = document.getElementById("pr-config");
if (!configNode) return;
const cfg = JSON.parse(configNode.textContent);
const $ = id => document.getElementById(id);
const labels = {SOLICITADA:"Solicitada",EM_COMPRAS:"Em compras",CONCLUIDA:"Compra concluída",CANCELADA:"Cancelada"};
const date = value => !value ? "—" : /^\d{4}-\d{2}-\d{2}$/.test(value) ? value.split("-").reverse().join("/") : new Date(value).toLocaleString("pt-BR",{timeZone:"America/Sao_Paulo"});
const qty = value => Number(value).toLocaleString("pt-BR",{maximumFractionDigits:3});
const node = (tag,text,className) => {const n=document.createElement(tag);if(text!=null)n.textContent=text;if(className)n.className=className;return n;};
let page=1,total=0,rows=[],key=crypto.randomUUID(),loading=false,closingAnticipationRow=null;
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
  const values=["SOL-"+row.id.slice(0,8).toUpperCase(),row.origin==="ESTOQUE"?"Almoxarifado":"PCP",anticipation?"ANTECIPAÇÃO":"NOVA COMPRA",row.sku_codigo,row.descricao,row.unidade,qty(row.quantity),date(row.needed_at),row.status,row.reference,row.requested_by,date(row.created_at),row.buyer||"—",date(row.updated_at),orderNumber?(anticipation&&!row.numero_oc?"Antecipar · OC "+orderNumber:orderNumber):"—",supplier||"—",orderStatus||"—",anticipation?qty(row.anticipation_pending_quantity||0):"—",date(row.anticipation_confirmed_delivery_date),date(row.completed_at),row.completed_by||"—",row.notes];
  values.forEach((value,index)=>{
   const cell=node("td",value||"—");
   if(index===2){const badge=node("span",value,"pr-badge "+(anticipation?"ANTECIPACAO":"COMPRA_NOVA"));if(anticipation){badge.style.background="#fff0d1";badge.style.color="#804900";}cell.replaceChildren(badge);}
   if(index===8){cell.replaceChildren(node("span",labels[row.status]||row.status,"pr-badge "+row.status));}
   if(index===7&&pending&&row.needed_at<today())cell.className="pr-overdue";
   if(index===14&&anticipation){cell.title="Pedido mais próximo da data de necessidade: "+date(row.anticipation_delivery_date);}
   tr.append(cell);
  });
  const actions=node("td");actions.append(button("Histórico",()=>showHistory(row.id)));
  if(cfg.can_manage){
   if(row.status==="SOLICITADA")actions.append(button(anticipation?"Registrar solicitação ao fornecedor":"Assumir",()=>act(row,anticipation?"SOLICITAR_ANTECIPACAO":"ASSUMIR")));
   if(anticipation&&row.status==="EM_COMPRAS")actions.append(button("Confirmar data negociada",()=>openAnticipationConfirmation(row)));
   if(anticipation&&pending)actions.append(button("Converter em nova O.C.",()=>act(row,"CONVERTER_NOVA_COMPRA")));
   if(pending)actions.append(button("Cancelar",()=>act(row,"CANCELAR")));
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
async function showHistory(id){
 try{
  const data=await api("/"+encodeURIComponent(id)+"/history"),content=$("pr-history-content");content.replaceChildren();
  content.append(node("h3",data.request.sku_codigo+" · "+data.request.descricao));
  content.append(node("p",qty(data.request.quantity)+" "+data.request.unidade+" · Necessidade: "+date(data.request.needed_at)+" · Solicitante: "+data.request.requested_by));
  for(const event of data.events){
   const box=node("section",null,"pr-event");
   box.append(node("strong",event.action.replaceAll("_"," ")),node("p",event.actor+" · "+date(event.created_at)));
   const before=event.before_data||{},after=event.after_data||{};
   if(before.status!==after.status)box.append(node("p",(labels[before.status]||"Nova solicitação")+" → "+(labels[after.status]||after.status)));
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
  const payload=Object.fromEntries(new FormData(event.target));payload.sku_codigo=$("pr-sku").value;payload.idempotency_key=key;
  loading=true;$("pr-submit").disabled=true;
  try{const data=await api("",payload);key=crypto.randomUUID();event.target.reset();message(data.request.request_type==="ANTECIPACAO"?"Solicitação SOL-"+data.request.id.slice(0,8).toUpperCase()+" identificada como ANTECIPAÇÃO do pedido "+(data.anticipation_order?.numero_oc||"já vigente")+" e encaminhada à fila do comprador.":"Solicitação SOL-"+data.request.id.slice(0,8).toUpperCase()+" enviada e registrada no histórico.");page=1;await load();}
  catch(e){message(e.message,true);}
  finally{loading=false;$("pr-submit").disabled=false;}
 };
 options();
}
load();
})();
