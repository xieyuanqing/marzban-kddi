import assert from 'node:assert/strict';
import worker from './site/_worker.js';
const alias = 'abcdefghijklmnopqrstuv';
const path = '/sub/' + 'a'.repeat(40);
let calls = 0;
let record = {path};
let responseCode = 200;
let redirect = null;
let contentType = 'text/plain';
const env = {PUBLIC_HOST:'share.example.pages.dev', UPSTREAM_ORIGIN:'https://backend.example', SHARE_ORIGIN_KEY:'test-key', SUB_LINKS:{async get(key){assert.equal(key,'s:'+alias);return record;}}};
globalThis.fetch = async (url, options) => {
  calls++;
  assert.equal(new URL(url).origin,'https://backend.example');
  assert.equal(new URL(url).search,'');
  assert.equal(options.redirect,'manual');assert.equal(options.cache,'no-store');
  assert.equal(options.headers['X-Share-Origin-Key'],undefined);
  const h={'Content-Type':contentType,'Subscription-Userinfo':'upload=0; download=0; total=1000000000',
    'Support-Url':'https://backend.example/dashboard/','Profile-Web-Page-Url':'https://backend.example/', 'X-Powered-By':'private-backend'};
  if(redirect)h.Location=redirect;
  return new Response('format payload',{status:responseCode,headers:h});
};
const request = (p,method='GET',host=env.PUBLIC_HOST) => new Request('https://'+host+p,{method,headers:{'User-Agent':'mihomo/1.19','Cookie':'do-not-forward','Authorization':'do-not-forward'}});
let tests = 0;
let r=await worker.fetch(request('/'),env);assert.equal(r.status,200);assert.match(await r.text(),/私密分享/);tests++;
for(const p of ['/dashboard/','/api/users','/_worker.js','/sub/'+'a'.repeat(40),'/s/'+alias+'/info','/s/'+alias+'/usage']){
 r=await worker.fetch(request(p),env);assert.equal(r.status,404);tests++;
}
assert.equal(calls,0);
for(const query of ['?t=123','?_=123','?flag=clash','?url=https://evil.example','?target=sing-box']){
 r=await worker.fetch(request('/s/'+alias+query),env);assert.equal(r.status,200);tests++;
}
r=await worker.fetch(request('/s/'+alias+'/clash-meta?_=123'),env);assert.equal(r.status,200);tests++;
r=await worker.fetch(request('/s/'+alias),env);assert.equal(r.status,200);assert.match(r.headers.get('Cache-Control'),/no-store/);assert.equal(r.headers.get('Support-Url'),null);assert.equal(r.headers.get('Profile-Web-Page-Url'),null);assert.equal(r.headers.get('X-Powered-By'),null);assert.ok(r.headers.get('Subscription-Userinfo'));assert.equal(await r.text(),'format payload');tests++;
for(const suffix of ['/clash-meta','/clash','/sing-box','/v2ray','/v2ray-json']){r=await worker.fetch(request('/s/'+alias+suffix),env);assert.equal(r.status,200);tests++;}
for(const code of [404,410]){responseCode=code;r=await worker.fetch(request('/s/'+alias),env);assert.equal(r.status,404);tests++;}
for(const code of [301,302,403,500]){responseCode=code;redirect='https://backend.example/leak';r=await worker.fetch(request('/s/'+alias),env);assert.equal(r.status,503);assert.equal(r.headers.get('Location'),null);assert.equal(r.headers.get('Subscription-Userinfo'),null);tests++;}
responseCode=200;redirect=null;contentType='text/html';r=await worker.fetch(request('/s/'+alias),env);assert.equal(r.status,503);tests++;
record=null;r=await worker.fetch(request('/s/'+alias),env);assert.equal(r.status,404);tests++;
record={path:'/api/users'};r=await worker.fetch(request('/s/'+alias),env);assert.equal(r.status,404);tests++;
record={path};contentType='text/plain';
r=await worker.fetch(request('/s/'+alias,'POST'),env);assert.equal(r.status,404);tests++;
r=await worker.fetch(request('/s/'+alias,'GET','preview.share.example'),env);assert.equal(r.status,200);tests++;
r=await worker.fetch(request('/s/'+alias+'?t=1','HEAD'),env);assert.equal(r.status,200);assert.equal(await r.text(),'');assert.ok(r.headers.get('Subscription-Userinfo'));tests++;
r=await worker.fetch(request('/','HEAD'),env);assert.equal(r.status,200);assert.equal(await r.text(),'');tests++;
console.log('WORKER_UNIT_TESTS_PASSED',tests);
