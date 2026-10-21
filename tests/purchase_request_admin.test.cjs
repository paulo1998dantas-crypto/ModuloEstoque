const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../estoque_app/static/purchase_requests.js'),'utf8');
const section=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b));
class Element{
 constructor(tag,text){this.tag=tag;this.textContent=text;this.children=[];this.dataset={};this.value='';this.checked=false;this.classList={add(){}};}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(...nodes){this.children=nodes;}
 setAttribute(){}
 addEventListener(){}
 reset(){}
 showModal(){this.open=true;}
 close(){this.open=false;}
}
const row=(status='CONCLUIDA')=>({id:'12341234-1234-1234-1234-123412341234',sku_codigo:'MAT-001',descricao:'Material',quantity:'2.000',unidade:'PC',version:4,status,request_type:'COMPRA_NOVA'});
function context(admin=true){
 const elements=new Map(),calls=[];
 const ctx=vm.createContext({cfg:{can_admin:admin,can_manage:false},page:1,total:1,rows:[],labels:{},calls,
  $:id=>{if(!elements.has(id))elements.set(id,new Element('div'));return elements.get(id);},
  node:(tag,text)=>new Element(tag,text),button:(text,fn)=>Object.assign(new Element('button',text),{onclick:fn}),
  qty:String,date:String,today:()=> '2026-10-21',encodeURIComponent,
  showDetails(){},showHistory(){},openEdit(){},excludeRequest(){},message:(...a)=>calls.push(['message',...a]),
  api:async(...a)=>{calls.push(['api',...a]);return{ok:true};},load:async()=>calls.push(['load'])});
 vm.runInContext(section('let adminRequest=null;','async function excludeRequest(row){'),ctx);
 return ctx;
}
const buttons=e=>e.children.flatMap(c=>[c,...buttons(c)]).filter(c=>c.tag==='button');
test('somente ADMIN vê ambas ações em todos status inclusive concluído/cancelado',()=>{
 for(const admin of [true,false])for(const status of ['SOLICITADA','EM_COMPRAS','CONCLUIDA','CANCELADA']){
  const ctx=context(admin);ctx.rows=[row(status)];
  vm.runInContext(section('function render(){','async function load(){'),ctx);ctx.render();
  const texts=buttons(ctx.$('pr-rows')).map(b=>b.textContent);
  assert.equal(texts.includes('Excluir definitivamente'),admin);
  assert.equal(texts.includes('Alterar quantidade (ADMIN)'),admin);
 }
});
test('abrir/cancelar não grava; exclusão exige confirmação e envia ID e versão',async()=>{
 const ctx=context();ctx.openAdminRequest(row(),true);
 assert.equal(ctx.calls.length,0);assert.equal(ctx.$('pr-admin-dialog').open,true);
 assert.match(ctx.$('pr-admin-warning').textContent,/TODOS/);
 await ctx.$('pr-admin-form').onsubmit({preventDefault(){}});
 assert.equal(ctx.calls.length,0);
 ctx.$('pr-admin-confirm').checked=true;
 await ctx.$('pr-admin-form').onsubmit({preventDefault(){}});
 const payload=ctx.calls.find(c=>c[0]==='api')[2];
 assert.deepEqual(JSON.parse(JSON.stringify(payload)),{action:'EXCLUIR_DEFINITIVO',version:4,confirmation:row().id});
 assert.equal(ctx.$('pr-admin-dialog').open,false);assert.equal(ctx.page,1);
 const cancelled=context();cancelled.openAdminRequest(row(),true);cancelled.$('pr-admin-cancel').onclick();
 assert.equal(cancelled.calls.length,0);
});
test('correção envia só quantidade e motivo sem mudar status/pedido',async()=>{
 const ctx=context();ctx.openAdminRequest(row(),false);
 ctx.$('pr-admin-quantity').value='3.5';ctx.$('pr-admin-reason').value='Correção';
 await ctx.$('pr-admin-form').onsubmit({preventDefault(){}});
 const payload=ctx.calls.find(c=>c[0]==='api')[2];
 assert.deepEqual(JSON.parse(JSON.stringify(payload)),{action:'EDITAR_QUANTIDADE_ADMIN',version:4,quantity:'3.5',reason:'Correção'});
});
test('falha mantém diálogo com erro; não confirma exclusão nem atualiza tabela',async()=>{
 const ctx=context();ctx.openAdminRequest(row(),true);ctx.$('pr-admin-confirm').checked=true;
 ctx.api=async()=>{throw Error('Registro alterado por outro usuário');};
 await ctx.$('pr-admin-form').onsubmit({preventDefault(){}});
 assert.equal(ctx.$('pr-admin-dialog').open,true);
 assert.match(ctx.$('pr-admin-error').textContent,/outro usuário/);
 assert.equal(ctx.calls.length,0);assert.equal(ctx.$('pr-admin-save').disabled,false);
});
