const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../estoque_app/static/purchase_requests.js'),'utf8');
class Element{
 constructor(tag,text){this.tag=tag;this.textContent=text;this.children=[];this.value='';}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=children;}
 setAttribute(name,value){this[name]=value;}
}
function context(){
 const elements=new Map(),messages=[];
 const ctx=vm.createContext({qty:String,date:String,messages,
  $:id=>{if(!elements.has(id))elements.set(id,new Element('div'));return elements.get(id);},
  node:(tag,text)=>new Element(tag,text),message:(...args)=>messages.push(args)});
 vm.runInContext(source.slice(source.indexOf('let allocationInputs=[];'),source.indexOf('function showDetails(row)')),ctx);
 return ctx;
}
const row={id:'request-12345678',sku_codigo:'MAT',quantity:'10',unidade:'PC',remaining_quantity:'6',allocated_quantity:'4',purchase_orders:[{purchase_order_id:'a',numero_oc:'1',quantity:'4'}]};
const orders=[{id:'a',numero_oc:'1',available_quantity:'4',fornecedor_nome:'A'},{id:'b',numero_oc:'2',available_quantity:'6',fornecedor_nome:'B'}];
test('pedidos anteriores são preenchidos sem apagar vínculo e novas O.C.s começam em zero',()=>{
 const ctx=context();ctx.renderAllocationInputs(row,orders);
 const inputs=ctx.$('pr-allocate-lines').children.filter(c=>c.tag==='label').map(c=>c.children[0]);
 assert.deepEqual(inputs.map(c=>c.value),['4','0']);
 assert.match(ctx.$('pr-allocate-request').textContent,/restante: 6/);
 assert.match(ctx.$('pr-allocate-details').textContent,/Total distribuído entre pedidos: 4/);
});
test('envia duas alocações e ignora pedidos com zero; total usa quantidade solicitada',()=>{
 const ctx=context();ctx.renderAllocationInputs(row,orders);
 const inputs=ctx.$('pr-allocate-lines').children.filter(c=>c.tag==='label').map(c=>c.children[0]);inputs[1].value='6';
 const result=ctx.readAllocationInputs(row);
 assert.deepEqual(JSON.parse(JSON.stringify(result)),[{purchase_order_id:'a',quantity:'4.000'},{purchase_order_id:'b',quantity:'6.000'}]);
 assert.equal(ctx.messages.length,0);
});
test('rejeita excesso por pedido, excesso total, negativo, quatro decimais e não finito',()=>{
 for(const values of [['5','0'],['4','7'],['-1','0'],['1.0001','0'],['NaN','0'],['0','0']]){
  const ctx=context();ctx.renderAllocationInputs(row,orders);
  const inputs=ctx.$('pr-allocate-lines').children.filter(c=>c.tag==='label').map(c=>c.children[0]);inputs.forEach((input,i)=>input.value=values[i]);
  assert.equal(ctx.readAllocationInputs(row),null);assert.equal(ctx.messages.length,1);
 }
});
test('alocação parcial é aceita para continuar em compras',()=>{
 const ctx=context();ctx.renderAllocationInputs({...row,purchase_orders:[]},orders);
 const inputs=ctx.$('pr-allocate-lines').children.filter(c=>c.tag==='label').map(c=>c.children[0]);inputs[0].value='2.125';
 assert.deepEqual(JSON.parse(JSON.stringify(ctx.readAllocationInputs(row))),[{purchase_order_id:'a',quantity:'2.125'}]);
});
