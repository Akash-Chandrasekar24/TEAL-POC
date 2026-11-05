const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('app/web/index.html', 'utf8');
const source = html.slice(html.indexOf('async function readExtractionResponse('), html.indexOf('\nasync function run()'));
const events = [];
const context = vm.createContext({TextDecoder, window:{dispatchEvent:event=>events.push(event.detail)}, CustomEvent:class {constructor(name, init){this.detail=init.detail;}}});
vm.runInContext(source, context);
const read = context.readExtractionResponse;
function response(items, trailingNewline=true) {
  const bytes = Buffer.from(items.map(JSON.stringify).join('\n') + (trailingNewline?'\n':''));
  let offset=0;
  return {headers:{get:()=> 'application/x-ndjson'},body:{getReader:()=>({
    async read(){return offset<bytes.length?{done:false,value:bytes.subarray(offset,++offset)}:{done:true};},
    async cancel(){}, releaseLock(){}
  })}};
}
(async()=>{
  const result = await read(response([
    {event:'started',total:2},
    {event:'page',total:2,page:{no:2,text:'Ø38 ±0.1'}},
    {event:'page',total:2,page:{no:1}},
    {event:'result',result:{id:'drawing'}}
  ],false));
  assert.equal(result.id,'drawing');
  assert.equal(events[1].pages[0].text,'Ø38 ±0.1');
  assert.deepEqual(Array.from(events[2].pages,p=>p.no),[1,2]);
  await assert.rejects(read(response([{event:'error',id:'failed-run',error:'Unreadable page'}])),error=>error.runId==='failed-run');
  await assert.rejects(read(response([{event:'started',total:4}])),/before extraction finished/);
  assert.equal(await read({headers:{get:()=> 'application/json'},json:async()=>42}),42);
  console.log('Extraction stream UI checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
