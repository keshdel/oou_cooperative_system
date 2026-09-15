const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../deploy/vps/landing/assets/executive-slideshow.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../deploy/vps/landing/index.html'), 'utf8');
const hero = html.match(/<section class="hero"[\s\S]*?<\/section>/)[0];
assert.ok(hero.includes('id="executiveSlideshow"'));
assert.ok(hero.includes('fetchpriority="high"'));
assert.equal((html.match(/id="executiveSlideshow"/g) || []).length, 1);
assert.equal(html.includes('<section class="executive-stories"'), false);
function setup(reduced = false){
  const timers = new Map();
  const events = {};
  let sequence = 0;
  let observer;
  function node(){ return {attrs:{}, firstElementChild:{}, setAttribute(k,v){this.attrs[k]=v;},
    addEventListener(k,v){this[k]=v;}}; }
  const photos = [node(),node()];
  photos.forEach((photo, index) => {
    photo.current = index === 0;
    photo.decode = async () => {};
    photo.classList = {toggle: (name, active) => {photo.current = active;}};
  });
  const nodes = Object.fromEntries(['.photo-controls','.photo-toggle','.photo-count','.previous','.next'].map(k=>[k,node()]));
  const gallery = node();
  gallery.querySelectorAll = () => photos;
  gallery.querySelector = key => nodes[key];
  const preference = {matches:reduced,addEventListener:(event, callback)=>{events.preference=callback;}};
  class Observer { constructor(callback){observer=callback;} observe(){} }
  const document = {hidden:false,getElementById:()=>gallery,addEventListener:(event,callback)=>{events[event]=callback;}};
  const window = {matchMedia:()=>preference,IntersectionObserver:Observer,
    setTimeout(callback,delay){assert.equal(delay,6000);const id=++sequence;timers.set(id,callback);return id;},
    clearTimeout(id){timers.delete(id);}};
  vm.runInNewContext(source,{window,document,IntersectionObserver:Observer});
  observer([{isIntersecting:true}]);
  return {photos,nodes,gallery,preference,events,document,timers};
}
async function flush(){await new Promise(resolve=>setImmediate(resolve));}
(async()=>{
  const page=setup();
  assert.equal(page.timers.size,1);
  [...page.timers.values()][0](); await flush();
  assert.equal(page.photos[1].current,true);
  assert.equal(page.photos[0].attrs['aria-hidden'],'true');
  page.nodes['.photo-controls'].mouseenter(); assert.equal(page.timers.size,0);
  page.nodes['.photo-controls'].mouseleave(); assert.equal(page.timers.size,1);
  page.gallery.focusin(); assert.equal(page.timers.size,0);
  page.nodes['.previous'].click(); await flush();
  assert.equal(page.photos[0].current,true);
  page.photos[1].decode=async()=>{throw new Error('Download failed');};
  page.nodes['.next'].click(); await flush();
  assert.equal(page.photos[0].current,true);
  const reduced=setup(true);
  assert.equal(reduced.timers.size,0);
  assert.equal(reduced.nodes['.photo-toggle'].disabled,true);
  reduced.nodes['.next'].click(); await flush();
  assert.equal(reduced.photos[1].current,true);
  const hidden=setup(); hidden.document.hidden=true; hidden.events.visibilitychange();
  assert.equal(hidden.timers.size,0);
  console.log('Slideshow cycling, manual controls, focus, reduced motion and failed-image checks passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
