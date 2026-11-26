// Run from the repository root with macOS JavaScriptCore's jsc.
const elements = {};
function element() { return {textContent: '', value: '', checked: false, disabled: false, hidden: false,
    showModal() { this.open = true; }, close() { this.open = false; }, scrollIntoView() {}, dataset: {}, style: {}, children: [], appendChild(child) { this.children.push(child); }, addEventListener() {}, reportValidity() { return true; }}; }
const document = {createElement: element, hidden: false, addEventListener() {}, getElementById(id) { return elements[id] || (elements[id] = element()); }};
let nextTimer = 0;
const timers = new Map();
function setTimeout(callback, delay) { const id = ++nextTimer; timers.set(id, {callback, delay}); return id; }
function clearTimeout(id) { timers.delete(id); }
class AbortController { constructor() { this.signal = {aborted: false}; } abort() { this.signal.aborted = true; } }
let fetch;
function assert(value, message) { if (!value) throw new Error(message); }
async function drain() { for (let i = 0; i < 40; i++) await Promise.resolve(); }
const saved = {timezone: 'Europe/Stockholm', lights_on: '05:00', lights_off: '21:00', ambient_enabled: true, ec_alarm_low: 1.2, ec_alarm_high: 2.5, ec_max_ms: 2.72};
saved.lighting = {morning: {start: '05:00', brightness_percent: 40}, day: {start: '07:00', brightness_percent: 100}, evening: {start: '19:00', brightness_percent: 50}, night: {start: '21:00', brightness_percent: 0}};
function status() { return {clock: {local_time: '2026-10-31 12:00:00', timezone: 'Europe/Stockholm'},
    fan: {pwm_percent: 65}, lighting: {phase: 'day', brightness_percent: 100, next_phase: 'evening', next_transition: '19:00', on: true, mode: 'scheduled', next_switch: {on: false, time: '21:00'}, active: true, clock_valid: true},
    water: {fill_percent: 75, volume_l: 8, status: 'good'}, nutrients: {value_ms: 1.8, status: 'optimal'},
    temperature: {value_c: 22.1, status: 'valid'}}; }
function response(data, ok=true) { return {ok, json: () => Promise.resolve(data)}; }
eval(readFile('grow.js') + `
(async () => {
    showPage('/garden');
    assert(!$('garden').hidden && $('settings').hidden && $('settings-menu').hidden, 'dashboard contains no settings form');
    showPage('/settings');
    assert($('garden').hidden && !$('settings-menu').hidden && $('settings').hidden && $('back-settings').hidden, 'settings menu view');
    showPage('/grow-settings');
    assert($('garden').hidden && $('settings-menu').hidden && !$('settings').hidden && !$('back-settings').hidden, 'grow settings view and back navigation');
    showPage('/garden');
    populateSettings(saved);
    initPlantGuide();
    assert($('plant-ec').children.length === 14, 'all 14 plants local');
    assert($('use-plant-range').disabled, 'plant selection optional');
    fetch = () => { throw new Error('plant guide attempted network request'); };
    const expected = [['Lettuce',0.8,1.4],['Basil',1,1.6],['Mint',1.2,2],['Parsley',1.2,2],
        ['Coriander / Cilantro',1,1.8],['Spinach',1.8,2.3],['Kale',1.2,2],['Pak choi',1.5,2],
        ['Strawberry',1.2,1.8],['Tomato',2,3.5],['Cucumber',1.7,2.5],['Sweet pepper',1.8,2.8],
        ['Chili / Habanero',1.8,3],['Eggplant',2,3]];
    expected.forEach(([name, min, max]) => {
        $('plant-ec').value = name; updatePlantGuide();
        assert($('plant-range').textContent === min.toFixed(1)+' – '+max.toFixed(1)+' mS/cm', 'range for '+name);
        assert($('ec-low').value === 1.2 && $('ec-high').value === 2.5 && !dirty, 'selection leaves saved values alone');
    });
    settingsLoaded = true;
    $('plant-ec').value = 'Basil'; updatePlantGuide(); applyPlantRange();
    assert($('ec-low').value === '1.0' && $('ec-high').value === '1.6' && dirty, 'apply edits draft only');
    await loadSettings();
    assert($('ec-high').value === '1.6', 'offline reconnect does not overwrite draft');
    $('ec-high').value = '1.7';
    $('plant-ec').value = 'Tomato'; updatePlantGuide();
    assert($('ec-high').value === '1.7', 'manual edit retained when selecting another plant');
    assert(!$('plant-range-limit').hidden, 'unsupported guide range explained');
    applyPlantRange();
    await saveSettings({preventDefault() {}});
    assert($('settings-status').textContent.includes('2.72'), 'unsupported thresholds rejected before request');
    $('plant-ec').value = ''; updatePlantGuide();
    assert($('use-plant-range').disabled, 'manual thresholds do not require plant');
    dirty = false; settingsLoaded = false;
    populateSettings(saved);
    assert($('morning-brightness').value === 40 && $('night-start').value === '21:00', 'four phases populated');
    $('morning-brightness').value = '0'; updateDuration();
    assert($('morning-percent').textContent === '0%', 'zero brightness draft displayed');
    $('day-start').value = '05:00'; updateDuration();
    assert($('schedule-duration').textContent.includes('distinct'), 'ambiguous phase warning');
    populateSettings(saved);
    renderStatus(status());
    assert($('nutrients-value').textContent === '1.80', 'mS/cm formatted');
    assert($('temperature-value').textContent === '22.1', 'temperature formatted');
    assert($('lighting-value').textContent === 'Day' && $('lighting-brightness').textContent === '100%', 'active phase and output');
    assert($('lighting-next').textContent === 'Next: Evening at 19:00', 'next phase');
    const night = status(); night.lighting.phase = 'night'; night.lighting.brightness_percent = 0; night.fan.pwm_percent = 100; renderStatus(night);
    assert($('fan-pwm').textContent === '100%' && $('lighting-brightness').textContent === '0%', 'night fan independent');
    night.fan.pwm_percent = 0; renderStatus(night);
    assert($('fan-pwm').textContent === '0%', 'zero fan output');
    const disabled = status(); disabled.capabilities = {water_level: false};
    disabled.water = {status: 'disabled', fill_percent: null, volume_l: null}; renderStatus(disabled);
    assert($('water-card').hidden, 'disabled water card hidden');
    assert($('garden-summary').textContent === 'Your plants are growing.', 'disabled water causes no warning');
    const fault = status(); fault.capabilities = {water_level: true};
    fault.water = {status: 'unavailable', fill_percent: null, volume_l: null}; renderStatus(fault);
    assert(!$('water-card').hidden && $('water-value').textContent === '--', 'installed fault stays visible');
    const manual = status(); manual.lighting.mode = 'manual'; manual.lighting.override_ends = 'mode_change_or_restart'; renderStatus(manual);
    assert($('lighting-status').textContent === 'Manual lighting', 'manual mode explicit');
    assert($('lighting-detail').textContent.includes('switch back or restart'), 'manual lifetime explicit');
    const bad = status(); bad.water = {status: 'unavailable', fill_percent: 75}; bad.nutrients = {status: 'unavailable', value_ms: null};
    bad.clock.local_time = null; renderStatus(bad);
    assert($('water-value').textContent === '--', 'invalid water hidden');
    assert($('nutrients-status').textContent === 'Sensor unavailable', 'invalid nutrient status');
    assert(!$('clock-warning').hidden, 'clock warning');
    connectionLost();
    for (const key of ['lighting', 'water', 'nutrients', 'temperature']) assert($(key+'-value').textContent === '--', 'stale '+key+' hidden');
    assert(!$('connection-error').hidden, 'connection error visible');
    let signal;
    fetch = (url, options) => { signal = options.signal; return new Promise(() => {}); };
    const stalled = request('/api/status').catch(error => error.message); await drain();
    Array.from(timers.values()).find(t => t.delay === 8000).callback();
    assert((await stalled).includes('timed out') && signal.aborted, 'request timeout aborts');
    assert(timers.size === 0, 'request deadline cleared');
    fetch = () => Promise.resolve({ok: true, json: () => new Promise(() => {})});
    const stalledBody = request('/api/status').catch(error => error.message); await drain();
    Array.from(timers.values()).find(t => t.delay === 8000).callback();
    assert((await stalledBody).includes('timed out'), 'body timeout');
    fetch = url => Promise.resolve(response(url === '/api/settings' ? saved : status()));
    await loadSettings();
    await loadLiveStatus();
    assert(settingsLoaded && $('water-value').textContent === 75, 'load recovery');
    populateSettings(saved); $('ec-low').value = 3;
    let posts = 0;
    fetch = () => { posts++; return Promise.resolve(response({})); };
    await saveSettings({preventDefault() {}});
    assert(posts === 0 && $('settings-status').textContent.includes('greater than'), 'invalid form never sent');
    populateSettings(saved);
    fetch = (url, options) => {
        if (options.method === 'POST') {
            posts++; assert(options.body.includes('morning_brightness=40') && options.body.includes('night_start=21%3A00'), 'phase settings included'); assert(options.body.includes('ec_alarm_low=1.2'), 'EC units preserved');
            assert(options.body.includes('ambient_enabled=1'), 'toggle included');
            return Promise.resolve(response({message: 'Settings saved successfully.', settings: saved}));
        }
        return Promise.resolve(response(status()));
    };
    await saveSettings({preventDefault() {}});
    assert(posts === 1 && $('settings-status').textContent === 'Settings saved successfully.', 'save confirmed');
    fetch = (url, options) => Promise.resolve(response(options.method === 'POST' ? {error: 'Flash full'} : status(), options.method !== 'POST'));
    await saveSettings({preventDefault() {}});
    assert($('settings-status').dataset.state === 'error' && $('settings-status').textContent.includes('Flash full'), 'save failure visible');
    renderStatus(status());
    document.hidden = true;
    pauseForVisibility();
    assert($('connection-text').textContent === 'Connected to Grow', 'hiding page never invents a connection failure');
    document.hidden = false;
    let backgroundReject;
    fetch = () => new Promise((_, reject) => { backgroundReject = reject; });
    const oldRequest = loadLiveStatus(); await drain();
    showPage('/settings');
    backgroundReject(new Error('navigation cancelled request')); await oldRequest;
    assert($('connection-text').textContent === 'Connected to Grow', 'old dashboard failure cannot affect settings');
    fetch = url => { assert(url === '/api/portal', 'menu never measures sensors'); return Promise.resolve(response({main_available: true})); };
    await loadLiveStatus();
    assert(!$('settings-menu').hidden, 'settings remains available');
    showPage('/garden');
    fetch = () => new Promise((_, reject) => { backgroundReject = reject; });
    const modalRequest = loadLiveStatus(); await drain();
    confirmPortalExit();
    backgroundReject(new Error('background timeout')); await modalRequest;
    assert($('connection-text').textContent === 'Connected to Grow', 'background failure cannot replace exit confirmation');
    $('exit-confirm').close(); exitConfirmOpen = false;
    let exitPosts = 0;
    fetch = (url, options) => {
        exitPosts++;
        assert(url === '/api/portal/exit' && options.method === 'POST', 'exit is a separate POST');
        return Promise.resolve(response({closing: true, message: 'Portal closed'}));
    };
    dirty = true;
    confirmPortalExit();
    assert($('exit-confirm').open && !$('exit-unsaved').hidden && exitPosts === 0, 'confirmation does not save or exit');
    $('exit-confirm').close();
    exitConfirmOpen = false;
    assert(!portalClosed && exitPosts === 0, 'cancel leaves portal active');
    confirmPortalExit();
    await exitPortal();
    assert(portalClosed && !$('portal-complete').hidden && $('garden').hidden && $('settings').hidden, 'acknowledged exit displays completion');
    assert(exitPosts === 1 && dirty, 'exit does not save edits');
    await loadLiveStatus();
    assert(exitPosts === 1, 'polling stops after exit');
    document.hidden = true; fetch = () => { throw new Error('hidden page polled'); }; await loadLiveStatus();
    print('Grow UI request, rendering, validation and recovery checks passed');
})().catch(error => { print(error.message); print(error.stack); quit(1); });
`);
