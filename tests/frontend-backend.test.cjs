const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { JSDOM } = require('jsdom');
const root = resolve(__dirname, '../frontend');
const response = (id, message) => ({ session_id: id, assistant_message: message,
  assessment: { status: 'collecting', missing_fields: ['annual_income'], profile: {}, assumptions: {} },
  needs_assessment: { illustrative_gap: null, breakdown: {} }, disclaimer: 'Illustrative estimate.' });
function boot(t, { reduceMotion = true } = {}) {
  const html = readFileSync(resolve(root, 'index.html'), 'utf8');
  const dom = new JSDOM(html, { url: 'https://lifeline.test/', runScripts: 'dangerously', pretendToBeVisual: true });
  t.after(() => dom.window.close());
  const w = dom.window, requests = [];
  w.matchMedia = (query) => ({ matches: query.includes("prefers-reduced-motion") ? reduceMotion : true });
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
  await until(() => Recognition.current);
  Recognition.current.say('Spoken second');
  await until(() => requests.length === 2);
  await until(() => doc.querySelector('#messages').textContent.includes('Backend answer 2'));
  assert.equal(requests[1].body.session_id, 'session-1');
  assert.equal(requests[1].body.message, 'Spoken second');
});


test('home orb opens voice, closing contracts it, and top-bar orb returns home', (t) => {
  const { w, doc } = boot(t, { reduceMotion: false });
  w.HTMLElement.prototype.getBoundingClientRect = function () {
    return { left: 300, top: 150, width: this.id === 'voice-orb' ? 240 : 132, height: 132 };
  };
  assert.equal(doc.querySelector('.devpanel').hidden, true);
  doc.querySelector('#hero-voice-btn').click();
  assert.equal(doc.querySelector('#voice').hidden, false);
  assert.equal(doc.querySelector('#voice').classList.contains('is-opening'), true);
  assert.equal(doc.querySelector('.voice-canvas').hidden, true);
  doc.querySelector('#end-voice-btn').click();
  assert.equal(doc.querySelector('#voice').classList.contains('is-closing'), true);
  assert.equal(doc.querySelector('#voice').hidden, false);
  const end = new w.Event('animationend', { bubbles: true });
  Object.defineProperty(end, 'animationName', { value: 'voice-contract' });
  doc.querySelector('#voice').dispatchEvent(end);
  assert.equal(doc.querySelector('#voice').hidden, true);
  doc.querySelector('#brand-home').click();
  assert.equal(doc.querySelector('#home').hidden, false);
  assert.equal(doc.querySelector('#voice').hidden, true);
});

test('coverage overview and resource bars display backend amounts without changing the estimate', async (t) => {
  const { w, doc } = boot(t);
  w.fetch = async () => ({ ok: true, json: async () => ({ ...response('estimate', 'Your estimate is ready.'),
    assessment: { status: 'ready', missing_fields: [], profile: {}, assumptions: {} },
    needs_assessment: { illustrative_gap: 123456, breakdown: { gross_need: 250000, total_offsets: 126544,
      components: [{ label: 'Income replacement', amount: 250000 }], offsets: [{ label: 'Savings', amount: 126544 }] } }
  }) });
  doc.querySelector('#message-input').value = 'Calculate';
  doc.querySelector('#composer').dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  await until(() => doc.querySelector('.estimate-amount'));
  assert.equal(doc.querySelector('.estimate-amount').textContent, '$123,456');
  assert.equal(doc.querySelector('.estimate-need strong').textContent, '$250,000');
  assert.equal(doc.querySelector('.estimate-resources strong').textContent, '$126,544');
  assert.equal(doc.querySelector('.estimate-need .estimate-track span').style.width, '100%');
  assert.equal(doc.querySelector('#documents-count').textContent, '1');
  assert.ok(doc.querySelector('.document-choice .document-thumbnail'));
  doc.querySelector('#voice-btn').click();
  assert.equal(doc.querySelector('.voice-canvas').hidden, true);
  doc.querySelector('#voice-documents-btn').click();
  assert.equal(doc.querySelector('.voice-canvas').hidden, false);
  assert.ok(doc.querySelector('#voice-canvas-content .artifact-card'));
  assert.equal(doc.querySelector('#voice-documents-btn').getAttribute('aria-expanded'), 'true');
  doc.querySelector('#voice-workspace-close').click();
  assert.equal(doc.querySelector('.voice-canvas').hidden, true);
  assert.equal(doc.activeElement, doc.querySelector('#voice-documents-btn'));
});

test('volume monitor reacts to microphone samples and releases audio resources on abort', async (t) => {
  const { w } = boot(t);
  let stopped = 0, closed = 0;
  const levels = [];
  Object.defineProperty(w.navigator, 'mediaDevices', { value: {
    getUserMedia: async () => ({ getTracks: () => [{ stop() { stopped++; } }] })
  } });
  w.AudioContext = class {
    resume() { return Promise.resolve(); }
    close() { closed++; return Promise.resolve(); }
    createAnalyser() { return { fftSize: 0, getFloatTimeDomainData(samples) { samples.fill(.1); } }; }
    createMediaStreamSource() { return { connect() {} }; }
  };
  const controller = new w.AbortController();
  await w.eval('LifelineSpeech').startMeter((level) => levels.push(level), controller.signal);
  assert.ok(levels[0] > 0 && levels[0] <= 1);
  controller.abort();
  assert.equal(stopped, 1);
  assert.equal(closed, 1);
  assert.equal(levels.at(-1), 0);
});

test('late microphone permission cannot keep a stream alive after voice mode closes', async (t) => {
  const { w } = boot(t);
  let grant, stopped = 0, closed = 0;
  Object.defineProperty(w.navigator, 'mediaDevices', { value: {
    getUserMedia: () => new Promise((resolve) => { grant = resolve; })
  } });
  w.AudioContext = class {
    resume() { return Promise.resolve(); }
    close() { closed++; return Promise.resolve(); }
  };
  const controller = new w.AbortController();
  const pending = w.eval('LifelineSpeech').startMeter(() => {}, controller.signal);
  await until(() => grant);
  controller.abort();
  grant({ getTracks: () => [{ stop() { stopped++; } }] });
  await pending;
  assert.equal(stopped, 1);
  assert.equal(closed, 1);
});

test('voice mute and exit stop monitoring, and old recognition events cannot submit into a new session', async (t) => {
  const { w, doc, requests } = boot(t, { reduceMotion: false });
  w.HTMLElement.prototype.getBoundingClientRect = () => ({ left: 300, top: 150, width: 132, height: 132 });
  let started = 0, stopped = 0;
  w.eval('LifelineSpeech').startMeter = async (_, signal) => {
    started++;
    signal.addEventListener('abort', () => stopped++, { once: true });
    return () => {};
  };
  class Recognition {
    constructor() { Recognition.current = this; }
    start() {}
    abort() {}
  }
  w.SpeechRecognition = Recognition;
  doc.querySelector('#hero-voice-btn').click();
  await until(() => Recognition.current && started === 1);
  const old = Recognition.current;
  assert.equal(started, 1);
  doc.querySelector('#mute-btn').click();
  assert.equal(stopped, 1);
  doc.querySelector('#mute-btn').click();
  await until(() => started === 2);
  doc.querySelector('#end-voice-btn').click();
  assert.equal(stopped, 2);
  const end = new w.Event('animationend');
  Object.defineProperty(end, 'animationName', { value: 'voice-contract' });
  doc.querySelector('#voice').dispatchEvent(end);
  doc.querySelector('#hero-voice-btn').click();
  const result = [{ transcript: 'Stale input' }];
  result.isFinal = true;
  old.onresult({ results: [result] });
  assert.equal(requests.length, 0);
  doc.querySelector('#brand-home').click();
  assert.equal(stopped, 2);
  assert.equal(doc.querySelector('#voice').hidden, true);
});


test('side document icon opens the viewer and restores focus when dismissed', (t) => {
  const { doc } = boot(t);
  const button = doc.querySelector('#documents-btn');
  assert.equal(button.closest('.topbar'), null);
  button.focus();
  button.click();
  assert.equal(doc.querySelector('#documents-panel').hidden, false);
  assert.equal(button.getAttribute('aria-expanded'), 'true');
  doc.querySelector('#documents-close').click();
  assert.equal(doc.querySelector('#documents-panel').hidden, true);
  assert.equal(button.getAttribute('aria-expanded'), 'false');
  assert.equal(doc.activeElement, button);
});
