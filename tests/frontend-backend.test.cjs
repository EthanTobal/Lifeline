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
  for (const file of ['vendor/marked.umd.js', 'vendor/purify.min.js', 'content.js', 'memories.js', 'speech.js', 'backend-api.js', 'app.js']) {
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

test('memory editor saves safe text, survives new chat, and sends notes to the backend', async (t) => {
  const { w, doc, requests } = boot(t);
  const open = doc.querySelector('#memories-btn');
  open.focus();
  open.click();
  const dialog = doc.querySelector('#memories-dlg');
  assert.equal(dialog.hidden, false);
  const input = doc.querySelector('#memory-input');
  const form = doc.querySelector('#memory-form');
  input.value = '<img src=x onerror=alert(1)> I prefer short answers.';
  form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  assert.equal(doc.querySelector('#mem-list img'), null);
  assert.equal(doc.querySelectorAll('#mem-list li').length, 1);
  doc.querySelector('#mem-list button').click();
  input.value = 'I prefer short answers.';
  form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  doc.querySelector('#mem-close').click();
  assert.equal(dialog.hidden, true);
  assert.equal(doc.activeElement, open);
  doc.querySelector('#new-chat-btn').click();
  assert.equal(JSON.parse(w.localStorage.getItem('lifeline-memories'))[0].text, 'I prefer short answers.');
  doc.querySelector('#message-input').value = 'Hello';
  doc.querySelector('#composer').dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  await until(() => requests.length === 1);
  assert.deepEqual(requests[0].body.memories, ['I prefer short answers.']);
  open.click();
  doc.querySelector('#mem-list button:last-child').click();
  assert.equal(doc.querySelectorAll('#mem-list li').length, 0);
  assert.deepEqual(JSON.parse(w.localStorage.getItem('lifeline-memories')), []);
});

test('voice transcript uses the same backend session as text chat', async (t) => {
  const { w, doc, requests } = boot(t);
  w.eval('LifelineMemories').save('I prefer short answers.');
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
  assert.ok(requests.every((request) => request.body.memories[0] === 'I prefer short answers.'));
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

test('coverage overview shows backend amounts and saves a document only on request', async (t) => {
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
  assert.equal(doc.querySelector('#documents-count').textContent, '0');
  assert.equal(doc.querySelectorAll('.document-choice').length, 0);
  assert.equal(doc.querySelector('.estimate-saved').hidden, true);
  doc.querySelector('.estimate-save').click();
  assert.equal(doc.querySelector('#documents-count').textContent, '1');
  assert.ok(doc.querySelector('.document-choice .document-thumbnail'));
  assert.equal(doc.querySelector('.estimate-save').hidden, true);
  assert.equal(doc.querySelector('.estimate-saved').hidden, false);
  assert.equal(doc.querySelector('.estimate-saved').textContent, 'Saved');
  assert.equal(doc.querySelector('.estimate-open').hidden, false);
  assert.equal(doc.activeElement, doc.querySelector('.estimate-open'));
  assert.equal(doc.querySelector('#documents-panel').hidden, true);
  doc.querySelector('.estimate-open').click();
  assert.equal(doc.querySelector('#documents-panel').hidden, false);
  assert.ok(doc.querySelector('#document-content').textContent.includes('$123,456'));
  doc.querySelector('#documents-close').click();
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

test('only the latest question is highlighted and home clears suggested replies', async (t) => {
  const { w, doc } = boot(t);
  let turn = 0;
  w.fetch = async () => ({ ok: true, json: async () => response('question',
    ++turn === 1 ? 'Here is some context.\n\n**What is your annual income?**' : 'Thanks.\n\nHow many children depend on you?') });
  assert.equal(doc.querySelectorAll('#home .topic').length, 0);
  assert.ok(doc.querySelector('#path-policy').compareDocumentPosition(doc.querySelector('#voice-cta')) & w.Node.DOCUMENT_POSITION_FOLLOWING);
  const send = () => {
    doc.querySelector('#message-input').value = 'Answer';
    doc.querySelector('#composer').dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  };
  send();
  await until(() => doc.querySelector('.current-question'));
  assert.equal(doc.querySelector('.current-question').textContent, 'What is your annual income?');
  send();
  assert.equal(doc.querySelector('.current-question'), null);
  await until(() => doc.querySelector('.current-question'));
  assert.equal(doc.querySelectorAll('.current-question').length, 1);
  assert.equal(doc.querySelector('.current-question').textContent, 'How many children depend on you?');
  doc.querySelector('#brand-home').click();
  assert.equal(doc.querySelector('.current-question'), null);
  assert.equal(doc.querySelector('#composer-suggestions').children.length, 0);
  assert.equal(doc.querySelector('#composer-suggestions-wrap').hidden, true);
  doc.querySelector('#continue-chat-btn').click();
  assert.equal(doc.querySelector('#composer-suggestions-wrap').hidden, true);
});

test('suggestion overflow control scrolls the row and disappears at the end', async (t) => {
  const { w, doc } = boot(t);
  const row = doc.querySelector('#composer-suggestions');
  Object.defineProperties(row, { scrollWidth: { value: 800, configurable: true }, clientWidth: { value: 300, configurable: true } });
  let scroll;
  row.scrollBy = (options) => { scroll = options; };
  doc.querySelector('#message-input').value = 'Hello';
  doc.querySelector('#composer').dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  await until(() => !row.hidden);
  const more = doc.querySelector('#suggestions-more');
  assert.equal(more.hidden, false);
  more.click();
  assert.equal(scroll.left, 200);
  assert.equal(scroll.behavior, 'instant');
  row.scrollLeft = 500;
  row.dispatchEvent(new w.Event('scroll'));
  assert.equal(more.hidden, true);
  row.scrollLeft = 0;
  Object.defineProperty(row, 'clientWidth', { value: 1000, configurable: true });
  w.dispatchEvent(new w.Event('resize'));
  assert.equal(more.hidden, true);
});

test('saving a recalculated estimate updates the saved copy and previous cards', async (t) => {
  const { w, doc } = boot(t);
  let gap = 100;
  w.fetch = async () => ({ ok: true, json: async () => ({ ...response('save', 'Your estimate.'),
    assessment: { status: 'ready', missing_fields: [], profile: {}, assumptions: {} },
    needs_assessment: { illustrative_gap: gap, breakdown: { gross_need: gap, total_offsets: 0, components: [], offsets: [] } },
  }) });
  const send = () => {
    doc.querySelector('#message-input').value = 'Calculate';
    doc.querySelector('#composer').dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  };
  send();
  await until(() => doc.querySelectorAll('.estimate-save').length === 1);
  doc.querySelector('.estimate-save').click();
  gap = 0;
  send();
  await until(() => doc.querySelectorAll('.estimate-save').length === 2);
  const buttons = doc.querySelectorAll('.estimate-save');
  assert.equal(buttons[0].hidden, true);
  assert.equal(buttons[1].hidden, false);
  assert.equal(doc.querySelectorAll('.estimate-amount')[1].textContent, '$0');
  assert.equal(doc.querySelectorAll('.estimate-need .estimate-track span')[1].style.width, '0%');
  buttons[1].click();
  assert.equal(doc.querySelector('#documents-count').textContent, '1');
  assert.equal(buttons[0].hidden, false);
  assert.equal(buttons[1].hidden, true);
  assert.equal(doc.querySelectorAll('.estimate-open')[0].hidden, true);
  assert.ok(doc.querySelector('#document-content').textContent.includes('$0'));
  let printed = 0;
  w.print = () => { printed++; };
  doc.querySelectorAll('.estimate-print')[0].click();
  assert.equal(printed, 1);
  assert.equal(doc.querySelector('#documents-count').textContent, '1');
  assert.equal(buttons[0].hidden, true);
  assert.equal(buttons[1].hidden, false);
  assert.ok(doc.querySelector('#lifeline-print').textContent.includes('$100'));
  w.dispatchEvent(new w.Event('afterprint'));
  assert.equal(doc.querySelector('#lifeline-print'), null);
});

test('estimate improvements preserve product recommendations, no-need states, and comparisons', async (t) => {
  const { w, doc } = boot(t);
  let recommendation = {
    product: { name: 'Example Term', policy_type: 'Term life', plain_language: 'Coverage for a fixed period.',
      coverage_duration: '20 years', benefits: ['Fixed period of cover'], primary_limitation: 'Cover ends with the term.' },
    match: { estimated_additional_coverage: 120000, reasons: ['Fits your stated goal'], assumptions: ['Assumed a 20-year need'] },
    pricing: { message: 'Ask an advisor for a quote.' },
  };
  let comparison;
  w.fetch = async () => ({ ok: true, json: async () => ({ ...response('recommendation', 'Here are your options.'),
    assessment: { status: 'ready', missing_fields: [], profile: {}, assumptions: {} }, recommendation, comparison,
    needs_assessment: { illustrative_gap: 123456, breakdown: { gross_need: 250000, total_offsets: 126544, components: [], offsets: [] } },
  }) });
  const send = () => {
    doc.querySelector('#message-input').value = 'Options';
    doc.querySelector('#composer').dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  };
  send();
  await until(() => doc.querySelector('.estimate-amount'));
  assert.equal(doc.querySelector('.estimate-amount').textContent, '$120,000');
  assert.ok(doc.querySelector('.recommendation-intro').textContent.includes('Example Term'));
  assert.ok(doc.querySelector('.recommendation-explanation').textContent.includes('Ask an advisor for a quote.'));
  assert.equal(doc.querySelector('.recommendation-math').open, false);
  doc.querySelector('.estimate-save').click();
  doc.querySelector('.estimate-open').click();
  assert.ok(doc.querySelector('#document-content').textContent.includes('Example Term'));
  assert.ok(doc.querySelector('#document-content').textContent.includes('Total needs: $250,000'));
  doc.querySelector('#documents-close').click();
  comparison = { has_more: true };
  send();
  await until(() => doc.querySelector('#composer-suggestions').textContent.includes('Show me all my options'));
  assert.equal(doc.querySelectorAll('.needs-assessment-card').length, 1);
  comparison = undefined;
  recommendation = { match: { no_additional_coverage: true, estimated_additional_coverage: 0 } };
  send();
  await until(() => doc.querySelectorAll('.estimate-amount').length === 2);
  assert.equal(doc.querySelectorAll('.estimate-amount')[1].textContent, '$0');
  assert.ok(doc.querySelectorAll('.recommendation-intro')[1].textContent.includes('You may already be well covered'));
  recommendation = { match: { estimated_additional_coverage: 123456, unresolved_questions: ['How long do you want cover to last?'] } };
  send();
  await until(() => doc.querySelector('.current-question'));
  assert.equal(doc.querySelector('.current-question').textContent.trim(), 'How long do you want cover to last?');
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
