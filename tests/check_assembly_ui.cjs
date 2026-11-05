/* Optional UI behavior smoke: node tests/check_assembly_ui.cjs. No browser/CAD/network. */
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
class Element {
  constructor(tag){ this.tagName=tag;this.children=[];this.value='';this.disabled=false;this.hidden=false;this.files=[];this.listeners={};this._text=''; }
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=[];this._text='';this.append(...nodes);}
  set textContent(v){this._text=String(v);this.children=[];}
  get textContent(){return this._text+this.children.map(n=>typeof n==='string'?n:n.textContent).join('');}
  addEventListener(name,fn){this.listeners[name]=fn;}
}
(async()=>{
 const elements = new Map(), requests = [], history=[];
 const $=id=>{if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id);};
 const rid='abcdef123456', aid='123456abcdef';
 const plan={assembly_version:'1',name:'test',source_hash:'drawing-hash',unit:'mm',unit_confirmed:false,bom_complete:false,
   bom:[{id:'B1',part_number:'BLOCK',description:'<img src=x onerror=alert(1)>',quantity:2,kind:'part',evidence:[{page:1,text:'BLOCK QTY 2'}],confirmed:false,issues:[]}],
   models:[],occurrences:[],mates:[],expected:{size:null,size_evidence:''},issues:[]};
 let attempt=0;
 const context={document:{getElementById:$,createElement:tag=>new Element(tag)},location:{hash:`#${rid}`},
  history:{replaceState:(_a,_b,hash)=>history.push(hash)},FileReader:class{readAsDataURL(){this.result='data:application/octet-stream;base64,VEVTVA==';this.onload();}},
  fetch:async(path,options={})=>{
   const body=options.body?JSON.parse(options.body):null;requests.push({path,body});let data;
   if(path==='/api/config')data={providers:{gemini:true},default:'gemini'};
   else if(path==='/api/assembly/read')data={id:aid,result_id:rid,plan,blockers:['Confirm BOM'],status:'needs_information',approved:false};
   else if(path==='/api/assembly/model')data={sha256:'a'.repeat(64),source_name:'block.SLDPRT'};
   else if(path==='/api/assembly/review')data={id:`${++attempt}`.padStart(12,'0'),result_id:rid,plan:body.plan,blockers:['Supply placements'],status:'needs_information',approved:false};
   else throw Error(`Unexpected endpoint ${path}`);
   return {ok:true,json:async()=>data};
  }};
 const source=fs.readFileSync('app/web/assembly.html','utf8').split('<script>')[1].split('</script>')[0];
 await vm.runInNewContext(source,context);
 assert.equal($('result').value,rid);assert.equal($('validate').disabled,true);
 await $('read').onclick();
 assert.equal($('validate').disabled,false);assert.equal($('download').hidden,false);
 assert.ok($('bom').textContent.includes('<img src=x onerror=alert(1)>'));
 await $('placeholders').onclick();
 let edited=JSON.parse($('plan').value);
 assert.equal(edited.occurrences.length,2);assert.ok(edited.occurrences.every(o=>o.placement===null&&!o.fixed));
 assert.equal($('download').hidden,true);
 $('model').files=[{name:'block.SLDPRT',size:10}];$('bom-id').value='B1';$('configuration').value='Default';$('provenance').value='Reviewed custom part job';$('origin').value='custom_features';
 await $('attach').onclick();edited=JSON.parse($('plan').value);
 assert.equal(edited.models.length,1);assert.equal(edited.models[0].confirmed,false);assert.equal(edited.models[0].origin,'custom_features');
 assert.equal($('download').hidden,true);
 await $('validate').onclick();
 assert.equal($('download').hidden,false);assert.ok($('download-link').href.includes('/000000000001/plan'));
 assert.equal(requests.find(r=>r.path==='/api/assembly/review').body.plan.models[0].sha256,'a'.repeat(64));
 $('plan').listeners.input();assert.equal($('download').hidden,true);assert.equal($('commands').textContent,'');
 $('result').value='changed';$('result').listeners.input();assert.equal($('plan').value,'');assert.equal($('validate').disabled,true);
 assert.ok(requests.every(r=>!r.path.includes('execute')));
 console.log('Assembly UI smoke passed: BOM, unplaced occurrences, model binding, immutable download, stale edit protection.');
})().catch(error=>{console.error(error);process.exitCode=1;});
