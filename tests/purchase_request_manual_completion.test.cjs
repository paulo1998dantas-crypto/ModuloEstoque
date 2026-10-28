const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.join(__dirname,'..'),app=fs.existsSync(path.join(root,'estoque_app'))?'estoque_app':'compras_app';
const source=fs.readFileSync(path.join(root,app,'static/purchase_requests.js'),'utf8');
const code=source.slice(source.indexOf('async function completeManually(row){'),source.indexOf('async function excludeRequest(row){'));
function context(answers,confirmed=true){const calls=[];const ctx=vm.createContext({prompt:()=>answers.shift(),confirm:()=>confirmed,encodeURIComponent,api:async(...args)=>calls.push(args),message(){},load:async()=>calls.push('loaded')});vm.runInContext(code,ctx);return{ctx,calls};}
const row={id:'12345678-abcd-4321-4321-123456789abc',version:4};
test('conclusão manual exige motivo, evidência e confirmação antes de gravar',async()=>{
 for(const [answers,confirmed] of [[[null],true],[['Motivo suficiente',null],true],[['Motivo suficiente','Evidência suficiente'],false]]){const{ctx,calls}=context(answers,confirmed);await ctx.completeManually(row);assert.equal(calls.length,0);}
});
test('conclusão manual envia versão e ID de confirmação, sem criar pedido',async()=>{
 const{ctx,calls}=context([' Atendimento por estoque ',' Saldo e necessidade conferidos ']);await ctx.completeManually(row);
 assert.deepEqual(JSON.parse(JSON.stringify(calls[0][1])),{action:'CONCLUIR_MANUAL',version:4,reason:'Atendimento por estoque',evidence:'Saldo e necessidade conferidos',confirmation:row.id});
 assert.equal(calls[1],'loaded');
});
