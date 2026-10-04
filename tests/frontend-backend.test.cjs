const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { JSDOM } = require('jsdom');
const root = resolve(__dirname, '../frontend');
const response = (id, message) => ({ session_id: id, assistant_message: message,
  assessment: { status: 'collecting', missing_fields: ['annual_income'], profile: {}, assumptions: {} },
  needs_assessment: { illustrative_gap: null, breakdown: {} }, disclaimer: 'Illustrative estimate.' });
function boot(t) {
  const html = readFileSync(resolve(root, 'index.html'), 'utf8');
  const dom = new JSDOM(html, { url: 'https://lifeline.test/', runScripts: 'dangerously', pretendToBeVisual: true });
  t.after(() => dom.window.close());
  const w = dom.window, requests = [];
  w.matchMedia = () => ({ matches: true });
  w.scrollTo = () => {};
  w.requestAnimationFrame = () => 0;
  w.fetch = async (url, options) => {
    requests.push({ url, body: JSON.parse(options.body) });
    return { ok: true, json: async () => response('session-' + requests.length, 'Backend answer ' + requests.length) };
  };
  for (const file of ['vendor/marked.umd.js', 'vendor/purify.min.js', 'content.js', 'speech.js', 'backend-api.js', 'app.js']) {
    const script = w.document.createElement('script');
    script.textContent = readFileSync(resolve(root, file), 'utf8');
    w.document.body.appendChild(script);
  }
  return { w, doc: w.document, requests };
}
async function until(check) {
  for (let i = 0; i < 100; i++) {
    if (check()) return;
    await new Promise((done) => setTimeout(done, 5));
  }
  throw new Error('UI did not update');
}
test('new UI routes chat to backend, reuses session, and resets on new chat', async (t) => {
  const { w, doc, requests } = boot(t);
  const send = async (message, answer) => {
    doc.querySelector('#message-input').value = message;
    doc.querySelector('#composer').dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
    await until(() => doc.querySelector('#messages').textContent.includes(answer));
  };
  await send('First question', 'Backend answer 1');
  await send('Second question', 'Backend answer 2');
  assert.deepEqual(requests.map((r) => r.body.session_id), [undefined, 'session-1']);
  assert.ok(requests.every((r) => r.url.endsWith('/api/turn')));
  doc.querySelector('#new-chat-btn').click();
  await send('Fresh question', 'Backend answer 3');
  assert.equal(requests[2].body.session_id, undefined);
  assert.equal(doc.querySelector('#messages').textContent.includes('Backend answer 1'), false);
});

test('voice transcript uses the same backend session as text chat', async (t) => {
  const { w, doc, requests } = boot(t);
  class Recognition {
    constructor() { Recognition.current = this; }
    start() {}
    abort() {}
    say(text) { const result = [{ transcript: text }]; result.isFinal = true; this.onresult({ results: [result] }); }
  }
  w.SpeechRecognition = Recognition;
  w.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
  w.speechSynthesis = { cancel() {}, speak(utterance) { utterance.onstart?.(); utterance.onend?.(); } };
  doc.querySelector('#message-input').value = 'Typed first';
  doc.querySelector('#composer').dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  await until(() => requests.length === 1);
  await until(() => doc.querySelector('#messages').textContent.includes('Backend answer 1'));
  doc.querySelector('#voice-btn').click();
  Recognition.current.say('Spoken second');
  await until(() => requests.length === 2);
  await until(() => doc.querySelector('#messages').textContent.includes('Backend answer 2'));
  assert.equal(requests[1].body.session_id, 'session-1');
  assert.equal(requests[1].body.message, 'Spoken second');
});
