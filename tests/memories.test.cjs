const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { JSDOM } = require('jsdom');
const source = readFileSync(resolve(__dirname, '../frontend/memories.js'), 'utf8');
function boot(t, saved) {
  const dom = new JSDOM('', { url: 'https://lifeline.test/', runScripts: 'outside-only' });
  t.after(() => dom.window.close());
  if (saved) dom.window.localStorage.setItem('lifeline-memories', saved);
  dom.window.eval(source + '\nwindow.memories = LifelineMemories;');
  return dom.window;
}
test('legacy saved memories load after a reload; duplicates and invalid entries are rejected', (t) => {
  const w = boot(t, JSON.stringify([{ id: 'old', text: 'My name is Margaret.', at: '2026-10-01' }]));
  assert.equal(w.memories.list()[0].text, 'My name is Margaret.');
  assert.throws(() => w.memories.save('my name is margaret.'), /already saved/);
  assert.throws(() => w.memories.save(' '.repeat(5)), /1 to 500/);
  assert.throws(() => w.memories.save('x'.repeat(501)), /1 to 500/);
  w.memories.save('I prefer short answers.');
  const reloaded = boot(t, w.localStorage.getItem('lifeline-memories'));
  assert.equal(reloaded.memories.list().length, 2);
  reloaded.memories.clear();
  assert.equal(reloaded.memories.list().length, 0);
});
test('corrupt storage is ignored and a failed write does not report a successful save', (t) => {
  const w = boot(t, '{broken');
  assert.equal(w.memories.list().length, 0);
  Object.defineProperty(w.Storage.prototype, 'setItem', { value() { throw new Error('blocked'); } });
  let changed = false;
  w.document.addEventListener('memories-changed', () => { changed = true; });
  assert.throws(() => w.memories.save('My name is Margaret.'), /Could not save/);
  assert.equal(changed, false);
});
