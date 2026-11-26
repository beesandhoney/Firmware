'use strict';
let portalClosed = false;
let pageView = 'garden';
let viewVersion = 0;
let pageLeaving = false;
let exitConfirmOpen = false;
let settingsLoaded = false;
let saving = false;
let polling = false;
let dirty = false;
let pollTimer;
let liveStatusPromise = Promise.resolve();
const $ = id => document.getElementById(id);
const finite = value => typeof value === 'number' && Number.isFinite(value);

// SW-SPEC-EC-001: local reference data, independent of saved alarm settings.
const PLANT_EC_GUIDE = [
    {name: 'Lettuce', min: 0.8, max: 1.4},
    {name: 'Basil', min: 1.0, max: 1.6},
    {name: 'Mint', min: 1.2, max: 2.0},
    {name: 'Parsley', min: 1.2, max: 2.0},
    {name: 'Coriander / Cilantro', min: 1.0, max: 1.8},
    {name: 'Spinach', min: 1.8, max: 2.3},
    {name: 'Kale', min: 1.2, max: 2.0},
    {name: 'Pak choi', min: 1.5, max: 2.0},
    {name: 'Strawberry', min: 1.2, max: 1.8},
    {name: 'Tomato', min: 2.0, max: 3.5},
    {name: 'Cucumber', min: 1.7, max: 2.5},
    {name: 'Sweet pepper', min: 1.8, max: 2.8},
    {name: 'Chili / Habanero', min: 1.8, max: 3.0},
    {name: 'Eggplant', min: 2.0, max: 3.0}
];
let ecMaxMs = 3.977;
function selectedPlant() {
    return PLANT_EC_GUIDE.find(plant => plant.name === $('plant-ec').value);
}
function updatePlantGuide() {
    const plant = selectedPlant();
    $('plant-suggestion').hidden = !plant;
    $('use-plant-range').disabled = !plant;
    $('plant-range-title').textContent = plant ? 'Suggested EC for ' + plant.name : '';
    $('plant-range').textContent = plant ? plant.min.toFixed(1) + ' – ' + plant.max.toFixed(1) + ' mS/cm' : '';
    $('plant-range-limit').hidden = !plant || plant.max <= ecMaxMs;
}
function initPlantGuide() {
    PLANT_EC_GUIDE.forEach(plant => {
        const option = document.createElement('option');
        option.value = plant.name;
        option.textContent = plant.name;
        $('plant-ec').appendChild(option);
    });
    $('plant-ec').addEventListener('change', updatePlantGuide);
    $('use-plant-range').addEventListener('click', applyPlantRange);
    updatePlantGuide();
}
function markUnsaved() {
    dirty = true;
    $('settings-status').textContent = 'You have unsaved changes.';
    $('settings-status').dataset.state = 'good';
}
function applyPlantRange() {
    const plant = selectedPlant();
    if (!plant || !settingsLoaded || saving) return;
    $('ec-low').value = plant.min.toFixed(1);
    $('ec-high').value = plant.max.toFixed(1);
    markUnsaved();
}

function request(url, options = {}) {
    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    let timeout;
    const deadline = new Promise((_, reject) => {
        timeout = setTimeout(() => {
            if (controller) controller.abort();
            reject(new Error('Connection timed out. Please try again.'));
        }, 8000);
    });
    const operation = Promise.resolve().then(() => fetch(url, Object.assign({}, options, {
        cache: 'no-store', signal: controller ? controller.signal : undefined
    }))).then(async response => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'The device could not complete the request.');
        return data;
    });
    return Promise.race([operation, deadline]).finally(() => clearTimeout(timeout));
}
function setState(card, state) { $(card + '-card').dataset.state = state; }
function durationText(minutes) {
    const hours = Math.floor(minutes / 60), remainder = minutes % 60;
    return remainder ? hours + ' h ' + remainder + ' min' : hours + ' hours';
}
function scheduleMinutes(value) {
    return /^([01]\d|2[0-3]):[0-5]\d$/.test(value) ? Number(value.slice(0, 2)) * 60 + Number(value.slice(3)) : null;
}
function updateDuration() {
    const phases = ['morning', 'day', 'evening', 'night'];
    const starts = phases.map(phase => scheduleMinutes($(phase + '-start').value));
    const durations = starts.map((start, i) => (starts[(i + 1) % 4] - start + 1440) % 1440);
    const valid = starts.every(value => value !== null) && durations.every(value => value > 0) && durations.reduce((a,b) => a+b, 0) === 1440;
    $('schedule-duration').textContent = valid ? 'Morning → Day → Evening → Night · Repeats every 24 hours' : 'Choose distinct times in Morning, Day, Evening, Night order over 24 hours.';
    phases.slice(0, 3).forEach(phase => $(phase + '-percent').textContent = $(phase + '-brightness').value + '%');
    $('ambient-label').textContent = $('ambient-enabled').checked ? 'Enabled' : 'Disabled';
}
function populateSettings(data) {
    $('timezone').value = data.timezone;
    ['morning', 'day', 'evening', 'night'].forEach(phase => {
        $(phase + '-start').value = data.lighting[phase].start;
        if (phase !== 'night') $(phase + '-brightness').value = data.lighting[phase].brightness_percent;
    });
    $('ambient-enabled').checked = data.ambient_enabled === true;
    $('ec-low').value = data.ec_alarm_low === undefined ? data.ec_low_ms : data.ec_alarm_low;
    $('ec-high').value = data.ec_alarm_high === undefined ? data.ec_high_ms : data.ec_alarm_high;
    if (finite(data.ec_max_ms)) ecMaxMs = data.ec_max_ms;
    $('ec-low').max = $('ec-high').max = ecMaxMs;
    $('ec-supported-range').textContent = 'Supported alert range: 0–' + ecMaxMs + ' mS/cm.';
    updatePlantGuide();
    updateDuration();
}
async function loadSettings() {
    try {
        const data = await request('/api/settings');
        $('wifi-link').hidden = false;
        $('wifi-nav').hidden = data.wifi_settings_available === false;
        $('exit-portal').hidden = data.portal_exit_available === false;
        if (!dirty) populateSettings(data);
        settingsLoaded = true;
        $('settings-fields').disabled = false;
        $('save-button').disabled = false;
        $('settings-status').textContent = 'Your saved garden routine.';
        $('settings-status').dataset.state = 'good';
        return true;
    } catch (error) {
        $('settings-status').textContent = 'Could not load settings. Reconnecting…';
        $('settings-status').dataset.state = 'error';
        return false;
    }
}
function markConnected() {
    $('connection').dataset.state = 'good';
    $('connection-text').textContent = 'Connected to Grow';
    $('connection-error').hidden = true;
}
function renderStatus(data) {
    if (!data || !data.lighting || !data.water || !data.nutrients || !data.temperature || !data.clock) {
        throw new Error('Incomplete device response.');
    }
    const light = data.lighting, water = data.water, nutrients = data.nutrients, temperature = data.temperature;
    const clock = data.clock;
    $('connection').dataset.state = 'good';
    $('connection-text').textContent = 'Connected to Grow';
    $('connection-error').hidden = true;
    $('clock-warning').hidden = !!clock.local_time;
    $('local-time').textContent = clock.local_time ? clock.local_time.slice(11, 16) + ' · ' + clock.timezone : 'Local time unavailable';
    $('lighting-value').textContent = ['morning', 'day', 'evening', 'night'].includes(light.phase) ? light.phase[0].toUpperCase() + light.phase.slice(1) : 'Unavailable';
    $('lighting-brightness').textContent = finite(light.brightness_percent) ? light.brightness_percent + '%' : 'Unavailable';
    $('fan-pwm').textContent = data.fan && finite(data.fan.pwm_percent) ? data.fan.pwm_percent + '%' : 'Unavailable';
    $('lighting-next').textContent = light.next_phase && light.next_transition ? 'Next: ' + light.next_phase[0].toUpperCase() + light.next_phase.slice(1) + ' at ' + light.next_transition : '';
    const lightIcon = (light.on === false ? 'moon' : 'brightness') + '-svgrepo-com.svg';
    $('lighting-icon').src = typeof GROW_ICON_URLS !== 'undefined' ? GROW_ICON_URLS[lightIcon] : '/icons/' + lightIcon;
    let detail = 'Scheduled lighting', label = 'Following your schedule';
    let lightState = 'good';
    if (light.on !== true && light.on !== false) { label = 'Status unavailable'; detail = 'Waiting for the light controller'; lightState = 'error'; }
    else if (light.mode === 'manual') { label = 'Manual lighting'; detail = light.override_ends === 'mode_change_or_restart' ? 'Until you switch back or restart' : 'Controlled by your device’s manual mode'; }
    else if (!light.clock_valid) { label = 'Time not synchronized'; detail = 'Waiting for a reliable local time'; lightState = 'warning'; }
    else if (!light.active) { label = 'Lighting paused'; detail = 'The device is starting up'; lightState = 'warning'; }
    else if (light.ambient_adjusted) { label = 'Adjusted for room brightness'; detail = 'Your daily schedule stays the same'; }
    else if (light.next_switch) { label = (light.next_switch.on ? 'Starts at ' : 'Until ') + light.next_switch.time; }
    $('lighting-status').textContent = label;
    $('lighting-detail').textContent = detail;
    setState('lighting', lightState);
    const waterEnabled = !data.capabilities || data.capabilities.water_level !== false;
    $('water-card').hidden = !waterEnabled;
    const waterValid = ['good', 'low'].includes(water.status) && finite(water.fill_percent) && finite(water.volume_l);
    $('water-value').textContent = waterValid ? Math.round(water.fill_percent) : '--';
    $('water-status').textContent = waterValid ? water.status === 'low' ? 'Low water' : 'Good' : water.status === 'out_of_range' ? 'Outside operating range' : 'Sensor unavailable';
    $('water-detail').textContent = waterValid ? water.volume_l.toFixed(1) + ' litres in your reservoir' : 'Water level cannot be confirmed';
    const fill = waterValid ? Math.min(100, Math.max(0, water.fill_percent)) + '%' : '0%';
    $('water-fill').style.width = fill;
    $('water-icon-fill').style.height = fill;
    setState('water', waterValid ? water.status === 'low' ? 'warning' : 'good' : 'error');
    const nutrientValid = finite(nutrients.value_ms) && ['optimal', 'low', 'high'].includes(nutrients.status);
    $('nutrients-value').textContent = nutrientValid ? nutrients.value_ms.toFixed(2) : '--';
    $('nutrients-status').textContent = nutrientValid ? {optimal: 'Optimal', low: 'Low nutrients', high: 'High nutrients'}[nutrients.status] : 'Sensor unavailable';
    $('nutrients-detail').textContent = nutrientValid ? {optimal: 'Within your chosen range', low: 'Below your chosen range', high: 'Above your chosen range'}[nutrients.status] : 'Nutrient level cannot be confirmed';
    setState('nutrients', nutrientValid ? nutrients.status === 'optimal' ? 'good' : 'warning' : 'error');
    const temperatureValid = finite(temperature.value_c) && temperature.status === 'valid';
    $('temperature-value').textContent = temperatureValid ? temperature.value_c.toFixed(1) : '--';
    $('temperature-status').textContent = temperatureValid ? 'Current temperature' : 'Sensor unavailable';
    setState('temperature', temperatureValid ? 'good' : 'error');
    const needsCare = (waterEnabled && (!waterValid || water.status === 'low')) || !nutrientValid || nutrients.status !== 'optimal' || !temperatureValid || lightState !== 'good';
    $('garden-summary').textContent = needsCare ? 'Your garden needs a little attention.' : 'Your plants are growing.';
}
function connectionLost() {
    $('connection').dataset.state = 'error';
    $('connection-text').textContent = 'Connection lost';
    $('connection-error').hidden = false;
    $('garden-summary').textContent = 'Waiting to reconnect to your garden.';
    ['lighting', 'water', 'nutrients', 'temperature'].forEach(card => {
        $(card + '-value').textContent = '--';
        $(card + '-status').textContent = 'Connection lost';
        setState(card, 'error');
    });
    $('lighting-detail').textContent = 'Lighting status unavailable';
    $('lighting-brightness').textContent = $('fan-pwm').textContent = 'Unavailable';
    $('lighting-next').textContent = '';
    $('water-detail').textContent = 'Water level unavailable';
    $('nutrients-detail').textContent = 'Nutrient level unavailable';
    $('water-fill').style.width = '0%';
    $('water-icon-fill').style.height = '0%';
    $('local-time').textContent = 'Device time unavailable';
    $('clock-warning').hidden = true;
}
function loadLiveStatus() {
    if (portalClosed || pageLeaving || exitConfirmOpen || document.hidden || polling || saving) return liveStatusPromise;
    polling = true;
    const version = viewVersion, view = pageView;
    liveStatusPromise = (async () => {
        try {
            if (view === 'garden') {
                const data = await request('/api/status');
                if (version === viewVersion && !pageLeaving && !exitConfirmOpen && !portalClosed) renderStatus(data);
            } else if (view === 'grow-settings' && !settingsLoaded) {
                if (await loadSettings()) markConnected();
            } else {
                await request('/api/portal');
                if (version === viewVersion && !pageLeaving && !exitConfirmOpen && !portalClosed) markConnected();
            }
        } catch (error) {
            if (version === viewVersion && !pageLeaving && !document.hidden && !exitConfirmOpen && !saving && !portalClosed) connectionLost();
        } finally { polling = false; }
    })();
    return liveStatusPromise;
}
async function saveSettings(event) {
    event.preventDefault();
    if (!settingsLoaded || saving) return;
    if (!$('settings-form').reportValidity()) return;
    const low = Number($('ec-low').value), high = Number($('ec-high').value);
    if (!$('ec-low').value.toString().trim() || !$('ec-high').value.toString().trim() ||
        !finite(low) || !finite(high) || low < 0 || high > ecMaxMs || low >= high || Math.round(low * 1000) >= Math.round(high * 1000)) {
        $('settings-status').textContent = low >= high ? 'High EC alert must be greater than Low EC alert.' :
            'Enter EC alerts from 0 to ' + ecMaxMs + ' mS/cm, at least 0.001 mS/cm apart.';
        $('settings-status').dataset.state = 'error';
        return;
    }
    const fields = {timezone: $('timezone').value,
        ambient_enabled: $('ambient-enabled').checked ? '1' : '0', ec_alarm_low: $('ec-low').value, ec_alarm_high: $('ec-high').value};
    ['morning', 'day', 'evening', 'night'].forEach(phase => {
        fields[phase + '_start'] = $(phase + '-start').value;
        if (phase !== 'night') fields[phase + '_brightness'] = $(phase + '-brightness').value;
    });
    saving = true;
    $('save-button').disabled = true;
    $('settings-fields').disabled = true;
    $('settings-status').textContent = 'Saving your settings…';
    try {
        await liveStatusPromise;
        const data = await request('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
            body: Object.keys(fields).map(key => encodeURIComponent(key) + '=' + encodeURIComponent(fields[key])).join('&')});
        if (!data.settings || data.message !== 'Settings saved successfully.') throw new Error('The device did not confirm saving.');
        populateSettings(data.settings);
        dirty = false;
        $('settings-status').textContent = 'Settings saved successfully.';
        $('settings-status').dataset.state = 'good';
    } catch (error) {
        $('settings-status').textContent = 'Could not confirm saving: ' + error.message;
        $('settings-status').dataset.state = 'error';
    } finally {
        saving = false;
        $('save-button').disabled = false;
        $('settings-fields').disabled = false;
        await loadLiveStatus();
    }
}
function startPolling() {
    if (portalClosed || pageLeaving || exitConfirmOpen || document.hidden) return;
    clearTimeout(pollTimer);
    loadLiveStatus().finally(() => {
        if (!portalClosed && !pageLeaving && !exitConfirmOpen && !document.hidden) pollTimer = setTimeout(startPolling, 5000);
    });
}
function showPage(path) {
    viewVersion++;
    pageView = path === '/settings' ? 'settings' : path === '/grow-settings' ? 'grow-settings' : 'garden';
    const menu = path === '/settings', settings = path === '/grow-settings';
    $('garden').hidden = menu || settings;
    $('settings').hidden = !settings;
    $('settings-menu').hidden = !menu;
    $('garden-nav').hidden = menu || settings;
    $('settings-nav').hidden = !menu && !settings;
    $('back-settings').hidden = menu;
    document.title = 'Grow · ' + (menu ? 'Settings' : settings ? 'Grow Settings' : 'My Garden');
}
function confirmPortalExit() {
    exitConfirmOpen = true;
    clearTimeout(pollTimer);
    $('exit-unsaved').hidden = !dirty;
    $('exit-error').textContent = '';
    $('exit-confirm').showModal();
}
function cancelPortalExit() {
    if (saving) return;
    $('exit-confirm').close();
    exitConfirmOpen = false;
    startPolling();
}
function pauseForVisibility() {
    if (document.hidden) clearTimeout(pollTimer);
    else if (!portalClosed && !pageLeaving) startPolling();
}
function navigatePage(path) {
    clearTimeout(pollTimer);
    window.history.pushState({}, '', path);
    showPage(path);
    liveStatusPromise.finally(startPolling);
}
async function exitPortal() {
    $('exit-confirm-button').disabled = true;
    $('exit-cancel').disabled = true;
    saving = true;
    clearTimeout(pollTimer);
    try {
        await liveStatusPromise;
        const data = await request('/api/portal/exit', {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: ''});
        if (!data.closing) throw new Error('The device did not confirm portal shutdown.');
        portalClosed = true;
        $('exit-confirm').close();
        $('portal-complete').hidden = false;
        $('garden').hidden = $('settings').hidden = true;
        $('exit-portal').disabled = true;
        exitConfirmOpen = false;
        $('connection').dataset.state = 'good';
        $('connection-error').hidden = true;
        $('connection-text').textContent = 'Portal closed';
        $('portal-complete').scrollIntoView();
    } catch (error) {
        $('exit-error').textContent = 'Could not confirm shutdown: ' + error.message;
    } finally {
        saving = false;
        $('exit-confirm-button').disabled = $('exit-cancel').disabled = false;
        if (!portalClosed) startPolling();
    }
}
document.addEventListener('DOMContentLoaded', () => {
    showPage(window.location.pathname);
    $('exit-portal').addEventListener('click', confirmPortalExit);
    $('exit-cancel').addEventListener('click', cancelPortalExit);
    $('exit-confirm').addEventListener('cancel', event => { event.preventDefault(); cancelPortalExit(); });
    $('exit-confirm-button').addEventListener('click', exitPortal);
    initPlantGuide();
    $('settings-form').addEventListener('submit', saveSettings);
    $('settings-form').addEventListener('input', event => {
        if (event.target.id === 'plant-ec') return;
        markUnsaved();
        updateDuration();
    });
    document.addEventListener('click', event => {
        const link = event.target.closest('a');
        if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        const path = link.getAttribute('href');
        if (['/', '/garden', '/settings', '/grow-settings'].includes(path)) {
            event.preventDefault();
            navigatePage(path);
        }
    });
    window.addEventListener('popstate', () => { showPage(window.location.pathname); liveStatusPromise.finally(startPolling); });
    window.addEventListener('pagehide', () => { pageLeaving = true; clearTimeout(pollTimer); });
    window.addEventListener('pageshow', () => { pageLeaving = false; startPolling(); });
    document.addEventListener('visibilitychange', pauseForVisibility);
    startPolling();
});
