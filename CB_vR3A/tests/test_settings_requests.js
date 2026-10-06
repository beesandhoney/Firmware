// Run from the repository root with JavaScriptCore's jsc (or a compatible shell).
const elements = {};
const document = {
    addEventListener() {},
    getElementById(id) { return elements[id] || (elements[id] = {textContent: ''}); }
};
const window = {addEventListener() {}};
const console = {error() {}};
let nextTimer = 0;
const timers = new Map();
function setTimeout(callback, delay) {
    const id = ++nextTimer;
    timers.set(id, {callback, delay});
    return id;
}
function clearTimeout(id) { timers.delete(id); }
class AbortController {
    constructor() { this.signal = {aborted: false}; }
    abort() { this.signal.aborted = true; }
}
class FormData { constructor(form) { this.form = form; } }
class URLSearchParams { toString() { return 'max_volume_l=12&point_count=4'; } }
let fetch;
eval(readFile('settings.html').match(/<script>([\s\S]*?)<\/script>/)[1]);
function assert(value, message) { if (!value) throw new Error(message); }
async function drain() { for (let i = 0; i < 40; i++) await Promise.resolve(); }
function expireRequest() {
    const timer = Array.from(timers.values()).find(item => item.delay === 8000);
    assert(timer, 'request must have a deadline');
    timer.callback();
}
async function runTests() {
    let signal;
    fetch = (url, options) => { signal = options.signal; return new Promise(() => {}); };
    const stalled = fetchNoStore('/live_status').catch(error => error.message);
    expireRequest();
    assert((await stalled).includes('timed out'), 'stalled request must reject');
    assert(signal.aborted, 'timed out request must be aborted');
    assert(timers.size === 0, 'deadline must be cleared');

    fetch = () => Promise.resolve({ok: true, text: () => new Promise(() => {})});
    const stalledBody = fetchNoStore('/current_settings').catch(error => error.message);
    await drain();
    expireRequest();
    assert((await stalledBody).includes('timed out'), 'response body must also have a deadline');

    const urls = [];
    fetch = (url) => {
        urls.push(url);
        if (url.startsWith('/adc_value')) {
            return Promise.resolve({ok: true, text: () => Promise.resolve('1368.0')});
        }
        return Promise.reject(new Error('Ambient sensor unavailable'));
    };
    await loadLiveStatus();
    assert(elements['ec-value'].textContent === 1368, 'EC must display despite ambient failure');
    assert(elements['ec-status'].textContent === 'Live reading', 'EC success must remain visible');
    assert(urls.length === 3 && urls[0].startsWith('/adc_value'), 'EC must be requested first');
    assert(urls.some(url => url.includes('water_level')), 'water must be polled');

    fetch = () => Promise.resolve({ok: true, text: () => Promise.resolve('error'),
        json: () => Promise.resolve({raw: 123})});
    await loadLiveStatus();
    assert(elements['ec-value'].textContent === '--', 'invalid EC must not display NaN or stale value');
    assert(elements['ec-status'].textContent.includes('Invalid EC response'), 'EC error must be visible');
    assert(elements['als-raw'].textContent === 123, 'EC failure must not block ambient');
    document.hidden = true;
    fetch = () => { throw new Error('Hidden page must not poll'); };
    await loadLiveStatus();
    document.hidden = false;
    fetch = () => Promise.resolve({ok: true, text: () => Promise.resolve('1400'),
        json: () => Promise.resolve({raw: 456})});
    await loadLiveStatus();
    assert(elements['ec-value'].textContent === 1400, 'EC must recover on return');
    assert(elements['als-raw'].textContent === 456, 'ambient must recover on return');
    displayLocalTime({timezone: 'Europe/Stockholm', local_time: '2026-09-15 07:00:00'});
    assert(elements['local-time'].textContent === '2026-09-15 07:00:00 (Europe/Stockholm)', 'device local time must display');
    displayLocalTime({timezone: 'Europe/Stockholm', local_time: null});
    assert(elements['local-time'].textContent.includes('Time unavailable'), 'invalid RTC must not display a fabricated time');
    print('6 settings request regression checks passed');
}
runTests().catch(error => { print(error.stack); quit(1); });
