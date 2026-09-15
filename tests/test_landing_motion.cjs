const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../deploy/vps/landing/index.html'), 'utf8');
const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(match => match[1]);
scripts.forEach(script => new vm.Script(script));
const motion = scripts.find(script => script.includes('const running = new Set()'));
assert.ok(motion);

function setup({reduced = false, supported = true, hash = ''} = {}) {
  const animations = [];
  const listeners = {};
  let observer;
  const preference = {matches: reduced, addEventListener: (name, callback) => { listeners[name] = callback; }};
  const element = () => ({
    contains(target) { return target === this; },
    animate(frames, options) {
      const animation = {frames, options, effect: {target: this}, cancelled: false,
        cancel() { this.cancelled = true; this.oncancel?.(); }};
      animations.push(animation);
      return animation;
    }
  });
  const hero = [element(), element()];
  const card = element();
  const document = {
    activeElement: null,
    querySelectorAll: selector => selector.startsWith('.hero') ? hero : [card],
    addEventListener: (name, callback) => { listeners[name] = callback; }
  };
  class Observer {
    constructor(callback) { this.callback = callback; this.observed = new Set(); observer = this; }
    observe(target) { this.observed.add(target); }
    unobserve(target) { this.observed.delete(target); }
    disconnect() { this.observed.clear(); }
  }
  vm.runInNewContext(motion, {window: {matchMedia: () => preference, IntersectionObserver: Observer},
    Element: {prototype: {animate: supported}}, IntersectionObserver: Observer, document, location: {hash}});
  return {animations, listeners, preference, observer, document, card, hero};
}

assert.equal(setup({reduced: true}).animations.length, 0);
assert.equal(setup({supported: false}).animations.length, 0);
assert.equal(setup({hash: '#contact'}).animations.length, 0);
const page = setup();
assert.equal(page.animations.length, 2);
page.observer.callback([{target: page.card, isIntersecting: true}]);
assert.equal(page.animations.length, 3);
assert.equal(page.observer.observed.has(page.card), false);
page.document.activeElement = page.card;
page.listeners.focusin();
assert.equal(page.animations[2].cancelled, true);
page.preference.matches = true;
page.listeners.change({matches: true});
assert.ok(page.animations.every(animation => animation.cancelled));
page.observer.callback([{target: page.hero[0], isIntersecting: true}]);
assert.equal(page.animations.length, 3);
const focused = setup({hash: '#contact'});
focused.document.activeElement = focused.card;
focused.observer.callback([{target: focused.card, isIntersecting: true}]);
assert.equal(focused.animations.length, 0);
assert.match(html, /@media\(prefers-reduced-motion:reduce\)/);
console.log('Landing script syntax and motion behaviour checks passed.');
