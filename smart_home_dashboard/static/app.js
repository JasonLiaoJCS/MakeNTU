/* Smart Home Dashboard - Jetson frontend */
(() => {
  'use strict';

  const STATUS_REFRESH_MS = 5000;
  const CAMERA_REFRESH_MS = 5000;
  const EVENTS_REFRESH_MS = 8000;
  const AI_REFRESH_MS = 5000;

  const state = {
    activeTab: 'live',
    focusRange: 'today',
    devices: [],
    todo: [],
    music: { status: 'stopped' },
    statusTimer: null,
    cameraTimer: null,
    eventsTimer: null,
    aiTimer: null,
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function toast(msg, isError = false) {
    const el = $('#toast');
    el.textContent = msg;
    el.classList.toggle('error', !!isError);
    el.classList.add('show');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.remove('show'), 2400);
  }

  async function api(path, opts = {}) {
    try {
      const res = await fetch(path, {
        headers: { 'Content-Type': 'application/json' },
        ...opts,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.ok === false) {
        return { ok: false, error: data.error || `HTTP ${res.status}`, status: res.status, data };
      }
      return { ok: true, data };
    } catch (err) {
      return { ok: false, error: err.message || String(err) };
    }
  }

  function fmtTime(iso) {
    if (!iso) return '--';
    try {
      return new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    } catch (_) {
      return iso;
    }
  }

  function fmtRelative(iso) {
    if (!iso) return '--';
    try {
      const d = new Date(iso);
      const diff = (Date.now() - d.getTime()) / 1000;
      if (diff < 60) return 'just now';
      if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
      if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
      return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    } catch (_) {
      return iso;
    }
  }

  function escapeHtml(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function asArray(value, key) {
    if (Array.isArray(value)) return value;
    if (value && Array.isArray(value[key])) return value[key];
    return [];
  }

  function setActiveTab(tabName) {
    state.activeTab = tabName;
    $$('.tab-panel').forEach(panel => {
      panel.classList.toggle('active', panel.id === `panel-${tabName}`);
    });
    $$('.tab-bar button, #tabs-top button').forEach(button => {
      button.classList.toggle('active', button.dataset.tab === tabName);
    });
    if (tabName === 'focus') refreshFocus();
    if (tabName === 'control') refreshEvents();
    if (tabName === 'ai') refreshAiTrace();
  }

  function bindTabs() {
    $$('.tab-bar button, #tabs-top button').forEach(btn => {
      btn.addEventListener('click', () => setActiveTab(btn.dataset.tab));
    });
  }

  async function refreshStatus() {
    const res = await api('/api/status');
    const dot = $('#conn-dot');
    const txt = $('#conn-text');

    if (!res.ok) {
      dot.classList.remove('ok');
      dot.classList.add('danger');
      txt.textContent = 'offline';
      $('#header-sub').textContent = res.error || 'connection error';
      return;
    }

    const s = res.data;
    dot.classList.remove('danger', 'warn');
    dot.classList.add('ok');
    txt.textContent = 'connected';
    $('#header-sub').textContent = humanClock(s.time);

    renderHome(s.home, s.health);
    renderWeather(s.weather);
    renderHealth(s.health || {});

    state.devices = asArray(s.devices, 'devices');
    renderDevices();

    state.todo = asArray(s.todo?.items || s.todo, 'items');
    renderTodo();

    state.music = s.music || { status: 'stopped' };
    renderMusic();
  }

  function humanClock(time) {
    const raw = String(time?.time || '');
    if (raw.length >= 4) return `${raw.slice(0, 2)}:${raw.slice(2, 4)}`;
    return 'connected';
  }

  function renderHome(home, health) {
    const h = home || {};
    const cameraOk = !!health?.camera;
    $('#home-mode').textContent = h.mode || (cameraOk ? 'Home' : 'Unknown');
    const detected = h.person_detected;
    $('#person-detected').textContent = detected === true ? 'Yes' : detected === false ? 'No' : '--';
    $('#last-update').textContent = fmtRelative(h.last_update);
    $('#camera-source').textContent = cameraOk ? 'Jetson' : 'fallback';
    $('#camera-status').textContent = cameraOk ? 'Auto-refresh every 5s' : 'Camera fallback active';
  }

  function renderWeather(weather) {
    const w = weather || {};
    $('#weather-temp').textContent = w.temperature_c ?? '--';
    $('#weather-condition').textContent = w.condition || w.location || '--';
    $('#weather-rain').textContent = `${w.rain_probability ?? '--'}%`;
    $('#weather-humidity').textContent = `${w.humidity ?? '--'}%`;
    $('#weather-icon').textContent = weatherEmoji(w.condition);
  }

  function weatherEmoji(cond) {
    const c = String(cond || '').toLowerCase();
    if (c.includes('rain') || c.includes('shower')) return '🌧';
    if (c.includes('storm') || c.includes('thunder')) return '⛈';
    if (c.includes('snow')) return '❄';
    if (c.includes('cloud')) return '⛅';
    if (c.includes('clear') || c.includes('sun')) return '☀';
    if (c.includes('fog') || c.includes('mist')) return '🌫';
    return '⛅';
  }

  function renderHealth(health) {
    const grid = $('#health-grid');
    const items = [
      { key: 'ai_server', label: 'AI Server', icon: '◆' },
      { key: 'tts', label: 'TTS', icon: '◒' },
      { key: 'camera', label: 'Camera', icon: '◉' },
      { key: 'music', label: 'Music', icon: '♪' },
      { key: 'weather', label: 'Weather', icon: '⛅' },
      { key: 'frdm_panel', label: 'FRDM Panel', icon: '▣' },
    ];
    grid.innerHTML = items.map(item => {
      const ok = !!health[item.key];
      return `
        <div class="health-item">
          <span class="health-label">${item.icon} ${item.label}</span>
          <span class="dot ${ok ? 'ok' : 'danger'}"></span>
        </div>
      `;
    }).join('');
  }

  function refreshCamera() {
    const img = $('#camera-img');
    if (!img) return;
    img.src = `/api/camera/latest?ts=${Date.now()}`;
    $('#camera-time').textContent = new Date().toLocaleTimeString(undefined, {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  }

  function bindCamera() {
    const img = $('#camera-img');
    img.addEventListener('error', () => {
      $('#camera-status').textContent = 'Camera endpoint unavailable';
    });
  }

  function deviceIcon(type) {
    return ({ light: '◉', fan: '✦', ac: '❄', plug: '⏻', heater: '☼' })[type] || '◇';
  }

  function deviceTypeLabel(type) {
    return ({ light: 'Light', fan: 'Fan', ac: 'AC', plug: 'Smart Plug', heater: 'Heater' })[type] || (type || 'Device');
  }

  function valueLabel(device) {
    if (device.state !== 'on') return 'Off';
    if (device.value == null) return 'On';
    const unit = device.unit || '';
    if (device.type === 'ac') return `${device.value}°${unit}`;
    if (unit === '%') return `${device.value}${unit}`;
    if (unit) return `${device.value} ${unit}`;
    return `${device.value}`;
  }

  function renderDevices() {
    const list = $('#device-list');
    if (!state.devices.length) {
      list.innerHTML = '<div class="empty"><div class="empty-icon">◇</div>No devices configured</div>';
      return;
    }

    list.innerHTML = state.devices.map(device => {
      const isOn = device.state === 'on';
      const showSlider = isOn && (device.type === 'light' || device.type === 'fan');
      const showStepper = isOn && device.type === 'ac';
      return `
        <div class="card device-card device-card-vertical ${isOn ? 'on' : ''}" data-id="${escapeHtml(device.id)}">
          <div class="device-row-top">
            <div class="device-icon">${deviceIcon(device.type)}</div>
            <div class="device-info">
              <div class="device-name">${escapeHtml(device.name)}</div>
              <div class="device-state">${escapeHtml(deviceTypeLabel(device.type))} · <strong>${escapeHtml(valueLabel(device))}</strong></div>
            </div>
            <div class="device-control">
              <label class="toggle">
                <input type="checkbox" ${isOn ? 'checked' : ''} data-action="toggle" data-id="${escapeHtml(device.id)}">
                <span class="toggle-slider"></span>
              </label>
            </div>
          </div>
          ${showSlider ? `
            <div class="device-slider-row">
              <input type="range" class="range-slider" min="0" max="100" step="5" value="${Number(device.value) || 0}"
                     data-action="slider" data-id="${escapeHtml(device.id)}">
              <span class="range-value">${Number(device.value) || 0}%</span>
            </div>` : ''}
          ${showStepper ? `
            <div class="device-slider-row stepper-row">
              <span class="stepper-label">Temperature</span>
              <div class="stepper">
                <button data-action="stepper-down" data-id="${escapeHtml(device.id)}">−</button>
                <span class="stepper-value">${device.value}°C</span>
                <button data-action="stepper-up" data-id="${escapeHtml(device.id)}">+</button>
              </div>
            </div>` : ''}
        </div>
      `;
    }).join('');

    bindDeviceControls();
  }

  function bindDeviceControls() {
    $$('#device-list [data-action="toggle"]').forEach(input => {
      input.addEventListener('change', event => {
        const id = event.target.dataset.id;
        setDevice(id, { state: event.target.checked ? 'on' : 'off' });
      });
    });

    $$('#device-list [data-action="slider"]').forEach(input => {
      input.addEventListener('input', event => {
        const valueEl = event.target.parentElement.querySelector('.range-value');
        if (valueEl) valueEl.textContent = `${event.target.value}%`;
      });
      input.addEventListener('change', event => {
        const value = parseInt(event.target.value, 10);
        setDevice(event.target.dataset.id, { state: value > 0 ? 'on' : 'off', value });
      });
    });

    $$('#device-list [data-action="stepper-up"], #device-list [data-action="stepper-down"]').forEach(btn => {
      btn.addEventListener('click', event => {
        const id = event.currentTarget.dataset.id;
        const dir = event.currentTarget.dataset.action === 'stepper-up' ? 1 : -1;
        const device = state.devices.find(item => item.id === id);
        if (!device) return;
        const next = Math.max(16, Math.min(30, (Number(device.value) || 26) + dir));
        setDevice(id, { state: 'on', value: next });
      });
    });
  }

  async function setDevice(deviceId, payload) {
    const device = state.devices.find(item => item.id === deviceId);
    if (device) {
      if ('state' in payload) device.state = payload.state;
      if ('value' in payload) device.value = payload.value;
      renderDevices();
    }

    const res = await api(`/api/devices/${encodeURIComponent(deviceId)}/set`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      toast(`Failed: ${res.error}`, true);
      refreshStatus();
      return;
    }
    if (res.data.device) {
      const idx = state.devices.findIndex(item => item.id === deviceId);
      if (idx >= 0) state.devices[idx] = res.data.device;
      renderDevices();
    }
    toast(`${device?.name || 'Device'} updated`);
    refreshEvents();
  }

  function renderTodo() {
    const list = $('#todo-list');
    if (!state.todo.length) {
      list.innerHTML = '<li class="empty"><div class="empty-icon">✓</div>No tasks yet</li>';
      return;
    }

    const sorted = [...state.todo].sort((a, b) => {
      if (a.status !== b.status) return a.status === 'open' ? -1 : 1;
      return (a.id || 0) - (b.id || 0);
    });

    list.innerHTML = sorted.map(item => `
      <li class="todo-item ${item.status === 'done' ? 'done' : ''}" data-id="${item.id}">
        <button class="todo-checkbox" data-action="toggle-todo" data-id="${item.id}" type="button">
          ${item.status === 'done' ? '✓' : ''}
        </button>
        <span class="todo-text">${escapeHtml(item.text)}</span>
        ${item.source && item.source !== 'dashboard' ? `<span class="todo-source">${escapeHtml(item.source)}</span>` : ''}
      </li>
    `).join('');

    $$('#todo-list [data-action="toggle-todo"]').forEach(btn => {
      btn.addEventListener('click', async event => {
        const id = parseInt(event.currentTarget.dataset.id, 10);
        const item = state.todo.find(todo => todo.id === id);
        if (!item || item.status === 'done') return;
        item.status = 'done';
        renderTodo();
        const res = await api(`/api/todo/${id}/done`, { method: 'POST', body: '{}' });
        if (!res.ok) {
          toast(`Could not complete: ${res.error}`, true);
          refreshStatus();
          return;
        }
        state.todo = asArray(res.data.items || res.data.todo, 'items');
        renderTodo();
        refreshEvents();
      });
    });
  }

  function bindTodoForm() {
    const input = $('#todo-input');
    const btn = $('#todo-add-btn');
    const submit = async () => {
      const text = (input.value || '').trim();
      if (!text) return;
      const res = await api('/api/todo', {
        method: 'POST',
        body: JSON.stringify({ text }),
      });
      if (!res.ok) {
        toast(`Could not add: ${res.error}`, true);
        return;
      }
      input.value = '';
      state.todo = asArray(res.data.items || res.data.todo, 'items');
      renderTodo();
      toast('Task added');
      refreshEvents();
    };
    btn.addEventListener('click', submit);
    input.addEventListener('keydown', event => {
      if (event.key === 'Enter') submit();
    });

    $('#todo-clear-btn').addEventListener('click', async () => {
      const res = await api('/api/todo/clear-completed', { method: 'POST', body: '{}' });
      if (!res.ok) {
        toast(`Failed: ${res.error}`, true);
        return;
      }
      state.todo = asArray(res.data.items || res.data.todo, 'items');
      renderTodo();
      toast(`Cleared ${res.data.removed || 0} completed`);
      refreshEvents();
    });
  }

  function renderMusic() {
    const music = state.music || {};
    const status = String(music.status || (music.active ? 'playing' : 'stopped')).toLowerCase();
    const title = music.title || music.youtube_title || music.media_title || music.now_playing_title || music.last_title || '';
    $('#music-title').textContent = title || 'Nothing playing';
    $('#music-artist').textContent = music.artist || music.channel || music.uploader || music.backend || music.last_backend || '--';
    const statusEl = $('#music-status');
    statusEl.textContent = status;
    statusEl.classList.toggle('playing', status === 'playing');
    $('#music-play').textContent = status === 'playing' ? '⏸' : '▶';
  }

  function bindMusicControls() {
    $('#music-play').addEventListener('click', async () => {
      const status = String(state.music?.status || 'stopped').toLowerCase();
      if (status === 'playing') await musicControl('pause');
      else if (status === 'paused') await musicControl('resume');
      else await musicControl('play');
    });
    $('#music-stop').addEventListener('click', () => musicControl('stop'));
    $('#music-next').addEventListener('click', () => musicControl('play'));
  }

  async function musicControl(action, query = '') {
    const res = await api('/api/music/control', {
      method: 'POST',
      body: JSON.stringify({ action, query }),
    });
    if (!res.ok) {
      toast(`Music: ${res.error}`, true);
      return;
    }
    state.music = res.data;
    renderMusic();
    toast(`Music ${action}`);
    refreshEvents();
  }

  async function refreshFocus() {
    const res = await api(`/api/focus/summaries?range=${state.focusRange}`);
    if (!res.ok) {
      toast(`Focus load failed: ${res.error}`, true);
      return;
    }
    renderFocus(res.data);
  }

  function scoreClass(score) {
    if (score >= 75) return 'score-good';
    if (score >= 50) return 'score-mid';
    return 'score-low';
  }

  function renderFocus(data) {
    const summary = data.summary || {};
    const range = data.range || 'today';
    const isMonthly = range === 'month';
    const focusMin = summary.total_focus_min ?? summary.focused_min ?? 0;
    const distractedMin = summary.total_distracted_min ?? summary.distracted_min ?? 0;

    $('#focus-score').textContent = Math.round(summary.average_focus_score || 0);
    $('#focus-time').textContent = Math.round(focusMin);
    $('#focus-percent').textContent = Math.round(summary.average_focus_percent || 0);
    $('#distracted-time').textContent = Math.round(distractedMin);
    $('#distract-sub').textContent = `${(summary.phone_detected_count || 0) + (summary.away_count || 0) + (summary.sleeping_count || 0)} interruptions`;
    $('#session-count').textContent = summary.session_count || 0;
    $('#session-sub').textContent = isMonthly ? 'this month' : 'today';
    $('#focus-score-sub').textContent = isMonthly ? 'monthly avg' : 'today avg';
    $('#phone-count').textContent = summary.phone_detected_count || 0;
    $('#away-count').textContent = summary.away_count || 0;
    $('#sleeping-count').textContent = summary.sleeping_count || 0;
    $('#chart-label').textContent = isMonthly ? `Monthly · ${summary.month || ''}` : `Daily · ${summary.date || ''}`;
    $('#chart-title-section').firstChild.nodeValue = isMonthly ? 'Daily Trend' : 'Hourly Breakdown';

    if (isMonthly) {
      renderChart(summary.daily || [], 'date', ['focus_min', 'distracted_min']);
    } else {
      renderChart((summary.hourly || []).map(hour => ({
        ...hour,
        label: String(hour.hour).padStart(2, '0') + 'h',
      })), 'label', ['focus_min', 'distracted_min']);
    }
    renderSessions(data.sessions || []);
  }

  function renderChart(rows, labelKey, valueKeys) {
    const svg = $('#focus-chart');
    const width = 600;
    const height = 160;
    const padL = 28;
    const padR = 8;
    const padT = 8;
    const padB = 22;
    const innerW = width - padL - padR;
    const innerH = height - padT - padB;

    if (!rows.length) {
      svg.innerHTML = `<text x="${width / 2}" y="${height / 2}" text-anchor="middle" fill="var(--text-subtle)" font-size="13">No data</text>`;
      return;
    }

    const max = Math.max(1, ...rows.flatMap(row => valueKeys.map(key => Number(row[key]) || 0))) * 1.1;
    const groupW = innerW / rows.length;
    const barW = Math.max(4, Math.min(28, groupW * 0.35));
    const colors = ['var(--ok)', 'var(--danger)'];
    let bars = '';
    let labels = '';
    let grid = '';

    for (let i = 0; i <= 3; i += 1) {
      const y = padT + (innerH * i / 3);
      const value = Math.round(max * (1 - i / 3));
      grid += `<line x1="${padL}" y1="${y}" x2="${width - padR}" y2="${y}" stroke="var(--border)" stroke-width="1"/>`;
      grid += `<text x="${padL - 4}" y="${y + 3}" text-anchor="end" fill="var(--text-subtle)" font-size="9">${value}</text>`;
    }

    rows.forEach((row, i) => {
      const cx = padL + groupW * (i + 0.5);
      valueKeys.forEach((key, keyIndex) => {
        const value = Number(row[key]) || 0;
        const barH = (value / max) * innerH;
        const x = cx - barW + (keyIndex * barW);
        const y = padT + innerH - barH;
        bars += `<rect x="${x}" y="${y}" width="${barW - 1}" height="${barH}" fill="${colors[keyIndex]}" rx="2"/>`;
      });

      const showEvery = Math.ceil(rows.length / 8);
      if (i % showEvery === 0 || i === rows.length - 1) {
        const label = labelKey === 'date' ? String(row[labelKey] || '').slice(5) : String(row[labelKey] || '');
        labels += `<text x="${cx}" y="${height - 6}" text-anchor="middle" fill="var(--text-subtle)" font-size="9">${escapeHtml(label)}</text>`;
      }
    });

    svg.innerHTML = grid + bars + labels;
  }

  function renderSessions(sessions) {
    const list = $('#session-list');
    if (!sessions.length) {
      list.innerHTML = '<div class="empty"><div class="empty-icon">◐</div>No sessions yet</div>';
      return;
    }

    list.innerHTML = sessions.map(session => {
      const score = Math.round(session.focus_score || 0);
      const pct = Math.round(session.focus_percent || 0);
      const duration = Math.round(session.duration_min || 0);
      const title = session.report_title || session.title || session.task || '(untitled)';
      const task = session.task || '';
      return `
        <div class="session-card">
          <div class="session-header">
            <div class="session-main">
              <div class="session-task">${escapeHtml(title)}</div>
              ${task ? `<div class="session-goal">Goal · ${escapeHtml(task)}</div>` : ''}
            </div>
            <div class="session-time">${fmtTime(session.started_at)} · ${duration}m</div>
          </div>
          <div class="session-stats">
            <span><span class="session-score ${scoreClass(score)}">${score}</span> score</span>
            <span><strong>${pct}%</strong> focused</span>
            <span><strong>${Math.round(session.focused_min || 0)}</strong>min on task</span>
          </div>
          ${session.recommendation ? `<div class="session-rec">${escapeHtml(session.recommendation)}</div>` : ''}
        </div>
      `;
    }).join('');
  }

  function bindFocusRange() {
    $$('#focus-range button').forEach(btn => {
      btn.addEventListener('click', () => {
        $$('#focus-range button').forEach(button => button.classList.remove('active'));
        btn.classList.add('active');
        state.focusRange = btn.dataset.range;
        refreshFocus();
      });
    });
  }

  async function refreshAiTrace() {
    const res = await api('/api/ai/trace?limit=20');
    if (!res.ok) {
      $('#ai-ready').textContent = 'Offline';
      $('#ai-source').textContent = res.error || 'not available';
      $('#ai-trace-list').innerHTML = `<div class="empty"><div class="empty-icon">◆</div>${escapeHtml(res.error || 'AI trace unavailable')}</div>`;
      return;
    }
    renderAiTrace(res.data);
  }

  function renderAiTrace(data) {
    const entries = Array.isArray(data.entries) ? data.entries : [];
    $('#ai-model').textContent = data.model || '--';
    $('#ai-ready').textContent = data.chat_ready ? 'Ready' : (entries.length ? 'Recent' : 'Offline');
    $('#ai-source').textContent = data.debug_log ? 'debug log' : 'health trace';

    const list = $('#ai-trace-list');
    if (!entries.length) {
      list.innerHTML = '<div class="empty"><div class="empty-icon">◆</div>No AI turns yet</div>';
      return;
    }

    list.innerHTML = entries.map(entry => {
      const meta = [
        entry.timestamp ? escapeHtml(entry.timestamp) : '',
        entry.model ? escapeHtml(entry.model) : '',
        entry.parse_status ? escapeHtml(entry.parse_status) : '',
      ].filter(Boolean).join(' · ');
      const output = entry.output || entry.raw_output || '(no model output captured)';
      return `
        <div class="trace-card">
          <div class="trace-head">
            <div>
              <div class="trace-title">Turn ${escapeHtml(entry.request_id || '')}</div>
              <div class="trace-meta">${meta || escapeHtml(entry.source || '')}</div>
            </div>
            <span class="trace-pill ${entry.ok ? 'ok' : 'warn'}">${entry.ok ? 'ok' : 'trace'}</span>
          </div>
          <div class="trace-dialog">
            <div class="trace-label">User Input</div>
            <div class="trace-bubble user">${escapeHtml(entry.input || '(empty)')}</div>
            <div class="trace-label">Model Output</div>
            <div class="trace-bubble model">${escapeHtml(output)}</div>
          </div>
          ${entry.emotion || entry.screen_mode || entry.head_motion ? `
            <div class="trace-control">
              ${entry.emotion ? `<span>Emotion <strong>${escapeHtml(entry.emotion)}</strong></span>` : ''}
              ${entry.screen_mode ? `<span>Screen <strong>${escapeHtml(entry.screen_mode)}</strong></span>` : ''}
              ${entry.head_motion ? `<span>Motion <strong>${escapeHtml(entry.head_motion)}</strong></span>` : ''}
            </div>
          ` : ''}
        </div>
      `;
    }).join('');
  }

  function bindAiTrace() {
    const button = $('#ai-refresh-btn');
    if (!button) return;
    button.addEventListener('click', refreshAiTrace);
  }

  async function refreshEvents() {
    const res = await api('/api/events?limit=20');
    if (!res.ok) return;
    renderEvents(res.data.events || []);
  }

  function eventDescription(event) {
    switch (event.type) {
      case 'device_set':
        return `<strong>${escapeHtml(event.name || event.device_id)}</strong> turned ${escapeHtml(event.state)}${event.value != null ? ` (${escapeHtml(event.value)})` : ''}`;
      case 'todo_add':
        return `Todo added: <strong>${escapeHtml(event.text)}</strong>`;
      case 'todo_done':
        return `Todo completed: <strong>${escapeHtml(event.text)}</strong>`;
      case 'todo_clear_completed':
        return `Cleared <strong>${event.removed}</strong> completed task(s)`;
      case 'music_control':
        return `Music <strong>${escapeHtml(event.action)}</strong>${event.title ? ` — ${escapeHtml(event.title)}` : ''}`;
      case 'weather_lookup':
        return `Weather lookup: <strong>${escapeHtml(event.result?.location || event.request?.location || '')}</strong>`;
      case 'sensor_update':
        return `Sensor update: <strong>${escapeHtml(event.sensor?.name || event.sensor?.id || '')}</strong>`;
      case 'frdm_power_cycle':
        return `FRDM power cycle: <strong>${event.result?.ok ? 'done' : 'failed'}</strong>`;
      case 'frdm_reset':
        return `FRDM legacy reset event: <strong>${event.result?.ok ? 'done' : 'failed'}</strong>`;
      default:
        return escapeHtml(event.type || 'event');
    }
  }

  function renderEvents(events) {
    const log = $('#event-log');
    if (!events.length) {
      log.innerHTML = '<li class="empty">No events yet</li>';
      return;
    }
    log.innerHTML = events.slice(0, 20).map(event => `
      <li class="event-item">
        <span class="event-time">${fmtTime(event.at)}</span>
        <span class="event-text">${eventDescription(event)}</span>
      </li>
    `).join('');
  }

  function clearTimers() {
    clearInterval(state.statusTimer);
    clearInterval(state.cameraTimer);
    clearInterval(state.eventsTimer);
    clearInterval(state.aiTimer);
    state.statusTimer = null;
    state.cameraTimer = null;
    state.eventsTimer = null;
    state.aiTimer = null;
  }

  function startTimers() {
    clearTimers();
    refreshStatus();
    refreshCamera();
    refreshFocus();
    refreshEvents();
    refreshAiTrace();
    state.statusTimer = setInterval(refreshStatus, STATUS_REFRESH_MS);
    state.cameraTimer = setInterval(refreshCamera, CAMERA_REFRESH_MS);
    state.eventsTimer = setInterval(refreshEvents, EVENTS_REFRESH_MS);
    state.aiTimer = setInterval(() => {
      if (state.activeTab === 'ai') refreshAiTrace();
    }, AI_REFRESH_MS);
  }

  function init() {
    bindTabs();
    bindCamera();
    bindTodoForm();
    bindMusicControls();
    bindMaintenance();
    bindFocusRange();
    bindAiTrace();
    startTimers();

    document.addEventListener('visibilitychange', () => {
      if (document.hidden) clearTimers();
      else startTimers();
    });
  }

  function bindMaintenance() {
    const button = $('#frdm-reset-btn');
    if (!button) return;
    button.addEventListener('click', async () => {
      const ok = window.confirm('Power-cycle the FRDM panel now? Jetson will cut USB power and reconnect it. USB camera/audio on the same controller may briefly disconnect.');
      if (!ok) return;
      button.disabled = true;
      button.textContent = 'Powering...';
      const res = await api('/api/frdm/power-cycle', {
        method: 'POST',
        body: JSON.stringify({ source: 'dashboard' }),
      });
      button.disabled = false;
      button.textContent = 'Power Cycle';
      if (!res.ok) {
        toast(`FRDM power cycle failed: ${res.error}`, true);
        refreshEvents();
        return;
      }
      toast('FRDM power cycle done');
      refreshEvents();
      refreshStatus();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
