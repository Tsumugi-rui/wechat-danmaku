/* ==========================================================================
   微信弹幕 · 前端逻辑
   1. 通过 SSE 长连接接收后台推来的消息
   2. 弹幕模式：自动分配轨道，避免互相追赶重叠
   3. 弹窗模式：右下 / 右上 / 左下 / 左上 卡片堆叠
   4. 横幅模式：顶部大字通知
   ========================================================================== */

(() => {
  'use strict';

  // ------------------------------------------------------------------ 元素
  const $ = (id) => document.getElementById(id);
  const stage = $('stage');
  const danmakuLayer = $('danmaku-layer');
  const popupLayer = $('popup-layer');
  const bannerLayer = $('banner-layer');
  const emptyState = $('empty-state');
  const toolbar = $('toolbar');
  const settings = $('settings');
  const connDot = $('conn-dot');
  const connText = $('conn-text');
  const sourceStatus = $('source-status');

  // ------------------------------------------------------------------ 状态
  const STORAGE_KEY = 'wechat-danmaku-settings-v1';

  const state = {
    modes: ['danmaku', 'popup'],
    speed: 170,          // 弹幕速度，像素 / 秒
    fontSize: 26,
    veil: 55,
    popupLife: 8,        // 弹窗停留秒数
    pos: 'br',
    bg: 'dim',
    sound: false,
    showSender: true,
    showChat: true,
    paused: false,
    filters: [],
  };

  function save() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch (_) {}
  }

  function load() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) Object.assign(state, JSON.parse(raw));
    } catch (_) {}
  }

  // ------------------------------------------------------------------ 工具

  const PALETTE = ['#7dd3fc', '#a5b4fc', '#f0abfc', '#fda4af', '#fdba74',
                   '#fcd34d', '#86efac', '#67e8f9', '#c4b5fd', '#f9a8d4',
                   '#5eead4', '#fca5a5'];

  function colorFor(name) {
    let hash = 0;
    const text = name || '?';
    for (let i = 0; i < text.length; i++) hash = (hash * 31 + text.charCodeAt(i)) >>> 0;
    return PALETTE[hash % PALETTE.length];
  }

  function escapeHtml(text) {
    return String(text ?? '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  let emptyHidden = false;
  function hideEmpty() {
    if (!emptyHidden) { emptyState.classList.add('hidden'); emptyHidden = true; }
  }

  // ------------------------------------------------------------------ 提示音

  let audioCtx = null;
  function beep() {
    if (!state.sound) return;
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      const t = audioCtx.currentTime;
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(1046, t);
      osc.frequency.exponentialRampToValueAtTime(1568, t + 0.08);
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(0.12, t + 0.012);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.24);
      osc.connect(gain); gain.connect(audioCtx.destination);
      osc.start(t); osc.stop(t + 0.26);
    } catch (_) { /* 提示音失败不影响显示 */ }
  }

  // ---------- 弹幕 ----------

  const BOTTOM_MARGIN = 18;
  const LANE_GAP = 10;

  let TOP_MARGIN = 92;        // 顶部留给控制条，按实际高度动态计算

  let lanes = [];             // 每条轨道的「可再次使用时间」(ms)
  let laneHeight = 60;

  /** 量出控制条的真实高度（窗口变窄时会换行变高），据此调整弹幕起始位置。 */
  function applyTopOffset() {
    const clean = document.body.classList.contains('clean');
    const barHeight = clean ? 0 : (toolbar.offsetHeight || 66);
    TOP_MARGIN = Math.max(24, Math.round(barHeight + 26));
    document.documentElement.style.setProperty('--top-offset', TOP_MARGIN + 'px');
    recalcLanes();
  }

  function recalcLanes() {
    laneHeight = Math.round(state.fontSize * 1.85) + LANE_GAP;
    const usable = Math.max(laneHeight, window.innerHeight - TOP_MARGIN - BOTTOM_MARGIN);
    const count = Math.max(1, Math.floor(usable / laneHeight));
    const next = new Array(count).fill(0);
    for (let i = 0; i < Math.min(count, lanes.length); i++) next[i] = lanes[i];
    lanes = next;
  }

  /** 选一条可以出发的轨道；全都占满时给出最早可用的那条与需要等待的时间。 */
  function pickLane(now) {
    let free = -1, freeRelease = Infinity;
    let busy = 0, busyRelease = Infinity;
    for (let i = 0; i < lanes.length; i++) {
      if (lanes[i] <= now) {
        if (lanes[i] < freeRelease) { free = i; freeRelease = lanes[i]; }
      } else if (lanes[i] < busyRelease) {
        busy = i; busyRelease = lanes[i];
      }
    }
    if (free >= 0) return { lane: free, delay: 0 };
    return { lane: busy, delay: Math.min(busyRelease - now, 1500) };
  }

  function danmakuNode(msg) {
    const el = document.createElement('div');
    el.className = 'danmaku';

    const color = colorFor(msg.sender || msg.chat);
    const parts = [];

    const senderName = msg.sender || msg.chat || '未知';
    if (state.showChat && msg.chat && msg.chat !== senderName) {
      parts.push(`<span class="dm-chat">${escapeHtml(msg.chat)}</span>`);
    }
    parts.push(`<span class="dm-name" style="color:${color}">${escapeHtml(senderName)}：</span>`);
    parts.push(`<span class="dm-text">${escapeHtml(msg.content)}</span>`);

    el.innerHTML = parts.join('');
    return el;
  }

  function spawnDanmaku(msg) {
    const now = performance.now();
    const { lane, delay } = pickLane(now);

    const fire = () => {
      const el = danmakuNode(msg);
      el.style.animation = 'none';                 // 先不播放，便于量宽度
      el.style.top = (TOP_MARGIN + lane * laneHeight) + 'px';
      danmakuLayer.appendChild(el);

      const width = el.offsetWidth;                // 触发布局，拿到真实宽度
      const travel = window.innerWidth + width + 24;
      const duration = travel / Math.max(20, state.speed);

      el.style.animation = `dm-run ${duration.toFixed(2)}s linear forwards`;
      // 调试用：负延迟可让弹幕「从半途开始」，便于截图与排版预览
      if (window.__DM_OFFSET) el.style.animationDelay = `-${window.__DM_OFFSET}s`;

      // 该轨道在这个时刻之前不能再来新弹幕（否则会追尾）
      lanes[lane] = performance.now() + (width + 40) / Math.max(20, state.speed) * 1000;

      const remove = () => el.remove();
      el.addEventListener('animationend', remove, { once: true });
      setTimeout(remove, duration * 1000 + 2000);  // 兜底清理
    };

    if (delay > 30) setTimeout(fire, delay); else fire();
  }

  // ---------- 弹窗 ----------

  const MAX_POPUPS = 6;

  function popupNode(msg) {
    const el = document.createElement('div');
    el.className = 'popup';
    el.style.setProperty('--accent', colorFor(msg.sender || msg.chat));

    const senderName = msg.sender || '未知';
    const head = [
      `<span class="pu-name">${escapeHtml(senderName)}</span>`,
      (state.showChat && msg.chat && msg.chat !== senderName)
        ? `<span class="pu-chat">${escapeHtml(msg.chat)}</span>` : '',
      `<span class="pu-time">${escapeHtml(msg.time || '')}</span>`,
    ].join('');

    el.innerHTML = `<div class="pu-head">${head}</div>
                    <div class="pu-text">${escapeHtml(msg.content)}</div>`;
    return el;
  }

  function spawnPopup(msg) {
    const el = popupNode(msg);
    const fromTop = state.pos === 'tr' || state.pos === 'tl';
    if (fromTop) popupLayer.prepend(el); else popupLayer.appendChild(el);

    // 超出上限时先移除最旧的
    while (popupLayer.children.length > MAX_POPUPS) {
      const victim = fromTop ? popupLayer.lastElementChild : popupLayer.firstElementChild;
      if (victim === el) break;
      victim.remove();
    }

    const life = Math.max(1, state.popupLife) * 1000;
    const timer = setTimeout(() => {
      el.classList.add('leaving');
      el.addEventListener('animationend', () => el.remove(), { once: true });
      setTimeout(() => el.remove(), 600);
    }, life);
    el.addEventListener('click', () => { clearTimeout(timer); el.remove(); });
  }

  // ---------- 顶部横幅 ----------

  const MAX_BANNERS = 3;

  function spawnBanner(msg) {
    const el = document.createElement('div');
    el.className = 'banner';
    const accent = colorFor(msg.sender || msg.chat);
    el.innerHTML = `<span class="bn-badge" style="background:${accent}">${escapeHtml(
                      msg.sender || '消息')}</span>
                    <span class="bn-text">${escapeHtml(msg.content)}</span>`;
    bannerLayer.appendChild(el);

    while (bannerLayer.children.length > MAX_BANNERS) {
      bannerLayer.firstElementChild.remove();
    }

    const life = Math.max(1, state.popupLife) * 1000;
    setTimeout(() => {
      el.classList.add('leaving');
      el.addEventListener('animationend', () => el.remove(), { once: true });
      setTimeout(() => el.remove(), 600);
    }, life);
  }

  // ---------- 消息入口 ----------

  function matchesFilter(msg) {
    if (!state.filters.length) return true;
    const haystack = `${msg.chat || ''} ${msg.sender || ''} ${msg.content || ''}`.toLowerCase();
    return state.filters.some((word) => haystack.includes(word));
  }

  function handleMessage(msg) {
    if (!msg || state.paused) return;
    if (!matchesFilter(msg)) return;

    hideEmpty();
    if (state.modes.includes('danmaku')) spawnDanmaku(msg);
    if (state.modes.includes('popup')) spawnPopup(msg);
    if (state.modes.includes('banner')) spawnBanner(msg);
    beep();
  }

  const SAMPLE_POOL = [
    ['绫地宁宁', '在吗？帮我看看这个 Bug'],
    ['项目冲刺群', '老王', '今晚 11 点发版，大家注意'],
    ['丛雨', '主人，晚饭记得吃哦'],
    ['前端·小张', 'PR 我改好了，麻烦 review 一下'],
    ['全家人的群', '妈妈', '天气转凉了，记得加衣服'],
    ['设计·阿May', '首页视觉稿更新好了，看下群文件'],
    ['产品经理·老王', '这个需求明天能上线吗？'],
    ['大学同学会', '阿哲', '周末有局吗，人齐了'],
  ];

  /** 生成一条本地示例消息（不经过后台服务），用于预览效果。 */
  function pushSample() {
    const pick = SAMPLE_POOL[Math.floor(Math.random() * SAMPLE_POOL.length)];
    const msg = pick.length === 3
      ? { chat: pick[0], sender: pick[1], content: pick[2] }
      : { chat: pick[0], sender: pick[0], content: pick[1] };
    msg.time = new Date().toTimeString().slice(0, 8);
    handleMessage(msg);
  }

  // ---------- SSE 连接 ----------

  let eventSource = null;
  let retryDelay = 1000;

  function setConn(status, text) {
    connDot.classList.remove('on', 'off');
    if (status === 'on') connDot.classList.add('on');
    if (status === 'off') connDot.classList.add('off');
    connText.textContent = text;
  }

  function connect() {
    try { if (eventSource) eventSource.close(); } catch (_) {}
    eventSource = new EventSource('/events');

    eventSource.addEventListener('open', () => {
      retryDelay = 1000;
      setConn('on', '已连接');
      $('empty-hint').textContent = '服务已连接，等待消息推送…';
    });

    eventSource.addEventListener('hello', (evt) => {
      setConn('on', '已连接');
      try {
        const data = JSON.parse(evt.data);
        if (data && data.clients) connText.textContent = `已连接 · ${data.clients} 端`;
      } catch (_) {}
    });

    eventSource.addEventListener('message', (evt) => {
      try { handleMessage(JSON.parse(evt.data)); } catch (_) {}
    });

    eventSource.addEventListener('error', () => {
      setConn('off', '连接已断开，重连中…');
      $('empty-hint').textContent = '后台服务似乎没在运行，请重新启动 start.bat';
      try { eventSource.close(); } catch (_) {}
      setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 1.6, 10000);
    });
  }

  // ---------- 控制条交互 ----------

  function applyVisuals() {
    document.documentElement.style.setProperty('--veil', (state.veil / 100).toFixed(2));
    document.documentElement.style.setProperty('--danmaku-size', state.fontSize + 'px');
    document.body.dataset.bg = state.bg;
    popupLayer.className = 'pos-' + state.pos;
  }

  function applyUi() {
    document.querySelectorAll('.mode-btn').forEach((btn) => {
      btn.classList.toggle('active', state.modes.includes(btn.dataset.mode));
    });
    document.querySelectorAll('.s-bg').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.bg === state.bg);
    });
    document.querySelectorAll('.s-pos').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.pos === state.pos);
    });
    $('speed').value = state.speed;
    $('fontsize').value = state.fontSize;
    $('veil').value = state.veil;
    $('veil-val').textContent = state.veil + '%';
    $('popup-life').value = state.popupLife;
    $('popup-life-val').textContent = state.popupLife + 's';
    $('sound').checked = state.sound;
    $('show-sender').checked = state.showSender;
    $('show-chat').checked = state.showChat;
    $('btn-pause').textContent = state.paused ? '继续' : '暂停';
    $('btn-pause').classList.toggle('active', state.paused);
    applyVisuals();
  }

  function bindUi() {
    // 展示方式切换（可多选）
    document.querySelectorAll('.mode-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const mode = btn.dataset.mode;
        const idx = state.modes.indexOf(mode);
        if (idx >= 0) {
          if (state.modes.length === 1) return;   // 至少保留一种
          state.modes.splice(idx, 1);
        } else {
          state.modes.push(mode);
        }
        applyUi(); save();
      });
    });

    $('speed').addEventListener('input', (e) => { state.speed = +e.target.value; save(); });
    $('fontsize').addEventListener('input', (e) => {
      state.fontSize = +e.target.value;
      recalcLanes();
      document.documentElement.style.setProperty('--danmaku-size', state.fontSize + 'px');
      save();
    });
    $('veil').addEventListener('input', (e) => {
      state.veil = +e.target.value;
      document.documentElement.style.setProperty('--veil', (state.veil / 100).toFixed(2));
      $('veil-val').textContent = state.veil + '%';
      save();
    });
    $('popup-life').addEventListener('input', (e) => {
      state.popupLife = +e.target.value;
      $('popup-life-val').textContent = state.popupLife + 's';
      save();
    });

    document.querySelectorAll('.s-bg').forEach((btn) => {
      btn.addEventListener('click', () => { state.bg = btn.dataset.bg; applyUi(); save(); });
    });
    document.querySelectorAll('.s-pos').forEach((btn) => {
      btn.addEventListener('click', () => { state.pos = btn.dataset.pos; applyUi(); save(); });
    });

    $('sound').addEventListener('change', (e) => { state.sound = e.target.checked; save(); if (state.sound) beep(); });
    $('show-sender').addEventListener('change', (e) => { state.showSender = e.target.checked; save(); });
    $('show-chat').addEventListener('change', (e) => { state.showChat = e.target.checked; save(); });

    $('filter-input').addEventListener('input', (e) => {
      state.filters = e.target.value.split(/[,，\s]+/).map((s) => s.trim().toLowerCase()).filter(Boolean);
      save();
    });

    $('btn-pause').addEventListener('click', () => {
      state.paused = !state.paused;
      applyUi();
    });

    $('btn-clear').addEventListener('click', () => {
      danmakuLayer.innerHTML = '';
      popupLayer.innerHTML = '';
      bannerLayer.innerHTML = '';
      lanes = lanes.map(() => 0);
      fetch('/api/clear', { method: 'POST' }).catch(() => {});
    });

    $('btn-test').addEventListener('click', pushSample);

    $('btn-settings').addEventListener('click', () => settings.classList.toggle('open'));

    $('btn-fullscreen').addEventListener('click', () => {
      if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen().catch(() => {});
      } else {
        document.exitFullscreen().catch(() => {});
      }
    });

    document.addEventListener('fullscreenchange', () => {
      setTimeout(applyTopOffset, 120);
    });

    // 鼠标静止后自动隐藏控制条，保持画面干净
    let idleTimer = null;
    const poke = () => {
      toolbar.classList.remove('idle');
      clearTimeout(idleTimer);
      idleTimer = setTimeout(() => {
        if (toolbar.matches(':hover')) return;
        if (settings.classList.contains('open')) return;
        toolbar.classList.add('idle');
      }, 3200);
    };
    document.addEventListener('mousemove', poke);
    document.addEventListener('keydown', poke);
    poke();

    // 快捷键：空格暂停、C 清屏、F 全屏、H 隐藏控件
    document.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT') return;
      const key = e.key.toLowerCase();
      if (key === ' ') { e.preventDefault(); $('btn-pause').click(); }
      if (key === 'c') $('btn-clear').click();
      if (key === 'f') $('btn-fullscreen').click();
      if (key === 'h') { document.body.classList.toggle('clean'); applyTopOffset(); }
      if (key === 's') $('btn-settings').click();
    });
  }

  // ---------- 状态轮询 ----------

  const STATUS_COLORS = {
    running: '#22c55e', connecting: '#f59e0b', starting: '#f59e0b',
    error: '#ef4444', stopped: '#64748b', idle: '#64748b',
  };

  async function pollStatus() {
    try {
      const res = await fetch('/api/status', { cache: 'no-store' });
      const data = await res.json();
      const items = Object.values(data.sources || {});
      if (!items.length) { sourceStatus.textContent = '暂无消息源'; return; }
      sourceStatus.innerHTML = items.map((src) => `
        <div class="src-item" title="${escapeHtml(src.detail || '')}">
          <span class="src-dot" style="background:${STATUS_COLORS[src.status] || '#64748b'}"></span>
          <span class="src-name">${escapeHtml(src.display_name || src.name)}</span>
          <span class="src-detail">${escapeHtml(src.detail || src.status || '')}</span>
        </div>`).join('');
    } catch (_) {
      sourceStatus.textContent = '无法获取状态';
    }
  }

  // ---------- 启动 ----------

  function init() {
    load();
    applyUi();
    bindUi();

    $('filter-input').value = state.filters.join(', ');

    // 控制条高度依赖字体与换行，等首次布局完成后再量
    requestAnimationFrame(applyTopOffset);
    window.addEventListener('load', applyTopOffset);

    window.addEventListener('resize', applyTopOffset);

    // 调试用 URL 参数：
    //   ?demo=6        载入后自动播 6 条示例消息（无需后台服务即可预览排版）
    //   ?clean=1       隐藏控制条（录屏 / 展示用）
    //   ?offset=4      弹幕从第 4 秒的进度开始（截图 / 排版检查用）
    //   ?offline=1     不连接后台服务，纯本地预览
    //   ?mode=danmaku,banner   指定展示方式
    //   ?bg=dim|clear|light    指定背景
    //   ?pos=br|tr|bl|tl       指定弹窗位置
    const params = new URLSearchParams(location.search);
    if (params.get('clean') === '1') document.body.classList.add('clean');
    const offset = parseFloat(params.get('offset'));
    if (offset > 0) window.__DM_OFFSET = offset;

    const modesParam = (params.get('mode') || '')
      .split(',').map((s) => s.trim()).filter((m) => ['danmaku', 'popup', 'banner'].includes(m));
    if (modesParam.length) state.modes = modesParam;
    if (['dim', 'clear', 'light'].includes(params.get('bg'))) state.bg = params.get('bg');
    if (['br', 'tr', 'bl', 'tl'].includes(params.get('pos'))) state.pos = params.get('pos');
    applyUi();
    applyTopOffset();

    if (params.get('offline') !== '1') {
      connect();
      pollStatus();
      setInterval(pollStatus, 6000);
    } else {
      setConn('on', '本地预览');
      connDot.classList.add('on');
    }

    if (params.has('demo')) {
      const count = Math.max(1, Math.min(30, parseInt(params.get('demo'), 10) || 5));
      for (let i = 0; i < count; i++) setTimeout(pushSample, 120 + i * 240);
    } else if (params.get('offline') !== '1') {
      // 首次进入时给一个短暂的提示
      setTimeout(pokeHint, 1200);
    }
  }

  function pokeHint() {
    if (!emptyHidden) emptyState.classList.remove('hidden');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
