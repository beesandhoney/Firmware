'use strict';
const $ = id => document.getElementById(id);
let scanning = false;
let connecting = false;
let awaitingAttempt = false;
async function wifiRequest(path, options = {}) {
    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    let timer;
    const timeout = new Promise((_, reject) => {
        timer = setTimeout(() => {
            if (controller) controller.abort();
            reject(new Error('Request timed out. Select Rescan to try again.'));
        }, 15000);
    });
    const operation = Promise.resolve().then(() => fetch(path, Object.assign({cache: 'no-store', signal: controller ? controller.signal : undefined}, options)))
        .then(async response => {
            let data;
            try { data = await response.json(); }
            catch (error) { throw new Error('Invalid device response. Refresh this page and retry.'); }
            if (!response.ok) throw new Error(data.error || 'Request failed. Please retry.');
            return data;
        });
    try { return await Promise.race([operation, timeout]); }
    finally { clearTimeout(timer); }
}
function wifiMessage(text, error = false, state = 'idle') {
    $('wifi-status').textContent = text;
    $('wifi-result').className = error ? 'notice error' : state === 'connected' ? 'notice success' : 'notice';
    $('wifi-result-title').textContent = error ? 'Wi-Fi connection not confirmed' : state === 'connected' ? 'Wi-Fi connected' : state === 'connecting' ? 'Connecting to Wi-Fi…' : 'Wi-Fi setup';
    $('wifi-status').className = error ? 'notice error' : 'notice';
}
async function scanNetworks() {
    if (scanning || connecting) return;
    scanning = true;
    $('scan-networks').disabled = true;
    $('wifi-connect').disabled = true;
    $('scan-status').textContent = 'Looking for nearby networks…';
    try {
        const data = await wifiRequest('/api/networks');
        if (!data || !Array.isArray(data.networks) || !data.networks.every(name => typeof name === 'string' && name.length > 0)) {
            throw new Error('Network scan failed: invalid response from Grow. Select Rescan to retry.');
        }
        const select = $('wifi-network');
        while (select.options.length > 1) select.remove(1);
        data.networks.forEach(name => {
            const option = document.createElement('option');
            option.value = option.textContent = name;
            select.appendChild(option);
        });
        $('scan-status').textContent = data.networks.length ? 'Select a network, or enter a hidden network’s name.' : 'No networks found. Scan again or enter the network name.';
    } catch (error) { $('scan-status').textContent = 'Scan failed. ' + error.message; }
    finally { scanning = false; $('scan-networks').disabled = false; $('wifi-connect').disabled = false; }
}
async function pollWifi() {
    if (scanning) { setTimeout(pollWifi, 1500); return; }
    try {
        const data = await wifiRequest('/api/portal');
        if (!data || typeof data.connected !== 'boolean') throw new Error('Invalid connection status');
        if (!$('wifi-ssid').value && typeof data.ssid === 'string') $('wifi-ssid').value = data.ssid;
        if (data.connecting) {
            connecting = true;
            awaitingAttempt = false;
            wifiMessage('Please wait while Grow connects. The result will appear here.', false, 'connecting');
        } else if (!awaitingAttempt && data.connected) {
            connecting = false;
            $('wifi-password').value = '';
            const network = data.ssid || $('wifi-ssid').value;
            wifiMessage('Connected to ' + network + '.' + (data.ip ? ' Grow’s network address is ' + data.ip + '.' : '') + ' You can return to My Garden.', false, 'connected');
        } else if (data.last_error && data.last_error !== 'NONE') {
            connecting = awaitingAttempt = false;
            wifiMessage(data.last_error === 'FAIL_AUTH' ? 'Could not connect. Check the Wi-Fi password and try again.' : 'Could not connect. Check the network name and password, then try again.', true);
        }
        if (!data.connecting && !data.connected && (!data.last_error || data.last_error === 'NONE') && !awaitingAttempt) {
            connecting = false;
            wifiMessage('Grow is not connected to Wi-Fi. Select a network and press Connect.');
        }
        $('wifi-fields').disabled = connecting;
    } catch (error) { wifiMessage('Connection to Grow interrupted. Reconnect to the setup network; retrying…', true); }
    setTimeout(pollWifi, 1500);
}
async function connectWifi(event) {
    event.preventDefault();
    if (scanning || connecting || !$('wifi-form').reportValidity()) return;
    const ssid = $('wifi-ssid').value, password = $('wifi-password').value;
    if (!ssid || (password && password.length < 8)) {
        wifiMessage('Enter a network name and a password of at least 8 characters, or leave it empty for an open network.', true);
        return;
    }
    connecting = awaitingAttempt = true;
    $('wifi-fields').disabled = true;
    wifiMessage('Please wait while Grow connects. The result will appear here.', false, 'connecting');
    try {
        await wifiRequest('/api/wifi', {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
            body: 'ssid=' + encodeURIComponent(ssid) + '&password=' + encodeURIComponent(password)});
        awaitingAttempt = false;
        wifiMessage('Credentials received. Waiting for Wi-Fi connection confirmation…', false, 'connecting');
    } catch (error) {
        connecting = awaitingAttempt = false;
        $('wifi-fields').disabled = false;
        wifiMessage(error.message, true);
    }
}
document.addEventListener('DOMContentLoaded', () => {
    $('wifi-network').addEventListener('change', () => { $('wifi-ssid').value = $('wifi-network').value; });
    $('scan-networks').addEventListener('click', scanNetworks);
    $('wifi-form').addEventListener('submit', connectWifi);
    // Serialize initial scan and status requests on the lightweight HTTP server.
    scanNetworks().finally(pollWifi);
});
