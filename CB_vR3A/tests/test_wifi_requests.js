const elements = {};
function element() { return {value: '', textContent: '', hidden: false, disabled: false, className: '',
    options: [{}], appendChild(option) { this.options.push(option); }, remove(i) { this.options.splice(i, 1); },
    addEventListener() {}, reportValidity() { return true; }}; }
const document = {getElementById(id) { return elements[id] || (elements[id] = element()); }, createElement: element, addEventListener() {}};
let nextTimer = 0;
const timers = new Map();
function setTimeout(callback, delay) { const id = ++nextTimer; timers.set(id, {callback, delay}); return id; }
function clearTimeout(id) { timers.delete(id); }
let fetch;
function response(data, ok=true) { return Promise.resolve({ok, json: () => Promise.resolve(data)}); }
function assert(value, message) { if (!value) throw new Error(message); }
eval(readFile('wifi.js') + `
(async () => {
    fetch = url => { assert(url === '/api/networks', 'local scan only'); return response({networks: ['Home', '<script>']}); };
    await scanNetworks();
    assert($('wifi-network').options.length === 3, 'networks populated');
    assert($('wifi-network').options[2].textContent === '<script>', 'SSID treated as text');
    fetch = () => response({main_available: false, connected: false});
    await pollWifi();
    assert(!$('back-garden').hidden && !$('back-settings').hidden, 'working setup navigation');
    let writes = 0;
    $('wifi-ssid').value = 'Home'; $('wifi-password').value = 'short';
    fetch = () => { writes++; return response({}); };
    await connectWifi({preventDefault() {}});
    assert(writes === 0 && $('wifi-status').className === 'notice error', 'invalid password not submitted');
    $('wifi-password').value = 'secret+%20';
    fetch = (url, options) => {
        writes++;
        assert(url === '/api/wifi' && options.method === 'POST', 'credentials sent in POST');
        assert(options.body.includes('secret%2B%2520'), 'password encoded once');
        return response({message: 'Connecting'});
    };
    await connectWifi({preventDefault() {}});
    assert(connecting && $('wifi-fields').disabled && $('wifi-password').value === 'secret+%20', 'credentials acknowledgement is not connection success');
    fetch = () => response({main_available: true, connecting: false, connected: true, ssid: 'Home', ip: '192.168.1.14'});
    await pollWifi();
    assert(!connecting && !$('back-garden').hidden && !$('back-settings').hidden, 'success preserves navigation');
    assert($('wifi-status').textContent.includes('Connected to Home') && $('wifi-status').textContent.includes('192.168.1.14'), 'specific connection success shown');
    assert($('wifi-result-title').textContent === 'Wi-Fi connected' && $('wifi-password').value === '', 'success banner and password cleared only after connection');
    $('wifi-ssid').value = '';
    await pollWifi();
    assert($('wifi-ssid').value === 'Home', 'network name restored after page reload');
    connecting = true;
    fetch = () => response({main_available: true, connected: false, last_error: 'FAIL_AUTH'});
    await pollWifi();
    assert(!connecting && !$('wifi-fields').disabled && $('wifi-status').textContent.includes('password'), 'failed connection allows retry');
    fetch = () => response({error: 'Scan failed'}, false);
    await scanNetworks();
    assert(!$('scan-networks').disabled && $('scan-status').textContent.includes('Scan failed'), 'scan failure retry');
    fetch = () => response({networks: []});
    await scanNetworks();
    assert($('scan-status').textContent.includes('No networks found'), 'successful empty scan explained');
    for (const data of [{}, null, {networks: ['Home', null]}]) {
        fetch = () => response(data);
        await scanNetworks();
        assert($('scan-status').textContent.includes('Scan failed'), 'malformed scan response visible');
        assert(!$('scan-networks').disabled, 'rescan remains enabled after failure');
    }
    fetch = () => Promise.reject(new Error('Network offline'));
    await scanNetworks();
    assert($('scan-status').textContent.includes('Network offline'), 'request failure visible');
    fetch = () => new Promise(() => {});
    const stalledScan = scanNetworks();
    await Promise.resolve();
    Array.from(timers.values()).find(timer => timer.delay === 15000).callback();
    await stalledScan;
    assert($('scan-status').textContent.includes('timed out') && !$('scan-networks').disabled, 'timeout is visible even without AbortController');
    assert(writes === 1, 'navigation and status never write credentials');
    print('Wi-Fi setup, validation, status and navigation checks passed');
})().catch(error => { print(error.stack); quit(1); });
`);
