let runConfig = null;
let currentState = null;
let lastSequence = 0;
let allLogs = [];
let seenLogSequences = new Set();
let taskRecords = new Map();
let queueFilter = 'all';
let pollTimer = null;
let toastTimer = null;
let viewedRunId = new URLSearchParams(location.search).get('run') || '';
let historyRuns = [];

const ACTIVE = new Set(['starting', 'running', 'pausing', 'paused', 'cancelling', 'finalizing']);
const LABELS = { idle: 'IDLE', interrupted: 'INTERRUPTED', starting: 'STARTING', running: 'RUNNING', pausing: 'PAUSING', paused: 'PAUSED', cancelling: 'STOPPING', finalizing: 'REPORTING', completed: 'COMPLETE', failed: 'FAILED', cancelled: 'CANCELLED' };

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[char]));
}

function api(path, options = {}) {
  return fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } }).then(async (response) => {
    const payload = (response.headers.get('content-type') || '').includes('application/json') ? await response.json() : null;
    if (!response.ok) throw new Error(payload?.error || `Runner API ${response.status}`);
    return payload;
  });
}

function formatClock(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return `${hours ? `${String(hours).padStart(2, '0')}:` : ''}${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

function compactNumber(value) {
  const number = Number(value) || 0;
  if (number >= 1_000_000) return `${(number / 1_000_000).toFixed(2)}m`;
  if (number >= 10_000) return `${(number / 1_000).toFixed(1)}k`;
  return number.toLocaleString();
}

function toast(message, error = false) {
  const node = document.querySelector('#opsToast');
  clearTimeout(toastTimer);
  node.textContent = message;
  node.className = `ops-toast show${error ? ' error' : ''}`;
  toastTimer = setTimeout(() => { node.className = 'ops-toast'; }, 3300);
}

function setConnection(online) {
  const wire = document.querySelector('.ops-wire');
  wire.classList.toggle('offline', !online);
  document.querySelector('#connectionState').textContent = online ? 'ONLINE' : 'OFFLINE';
}

function parseLog(item) {
  const line = item.line || '';
  const started = line.match(/\[run (\d+)\/(\d+)\]\s+\S+\s+::\s+(\S+)/);
  if (started) {
    const [, index, , taskId] = started;
    const meta = runConfig?.tasks?.find((task) => task.id === taskId) || {};
    taskRecords.set(taskId, { ...(taskRecords.get(taskId) || {}), index: Number(index), taskId, title: meta.title || taskId, category: meta.category || 'unknown', status: 'running', result: null });
  }
  const ended = line.match(/\[end (\d+)\/(\d+)\]\s+\S+\s+::\s+(\S+)\s+(PASS|FAIL)\s+steps=(\d+)\s+tokens=(\d+)\s+t=([\d.]+)s\s*(.*)/);
  if (ended) {
    const [, index, , taskId, result, steps, tokens, elapsed, failure] = ended;
    const meta = runConfig?.tasks?.find((task) => task.id === taskId) || {};
    taskRecords.set(taskId, { index: Number(index), taskId, title: meta.title || taskId, category: meta.category || 'unknown', status: 'completed', result, steps: Number(steps), tokens: Number(tokens), elapsedSeconds: Number(elapsed), failure: failure || null });
  }
}

function mergeTaskRuns(state) {
  for (const item of state.taskRuns || []) taskRecords.set(item.taskId, { ...(taskRecords.get(item.taskId) || {}), ...item });
  for (const item of state.logs || []) parseLog(item);
}

function relevantTasks(state) {
  const tasks = runConfig?.tasks || [];
  if (!state || !state.scope || state.scope === 'all') return tasks;
  if (runConfig?.categories?.[state.scope]) return tasks.filter((task) => task.category === state.scope);
  // 看板发車的 scope 是一串逗号题号（"a,b,c"）：必须按集合处理。以前这里当成
  // 单个题号去比，于是看板发出的一轮在控制台里逐题表和实时栏位全是空的。
  const wanted = new Set(String(state.scope).split(',').map((item) => item.trim()).filter(Boolean));
  return tasks.filter((task) => wanted.has(task.id));
}

function computedRecords(state) {
  const active = new Set(state.currentTasks || []);
  return relevantTasks(state).map((task, index) => {
    const existing = taskRecords.get(task.id);
    if (existing) return { ...task, ...existing };
    return { ...task, index: index + 1, taskId: task.id, status: active.has(task.id) ? 'running' : 'pending', result: null };
  }).sort((a, b) => (a.index || 9999) - (b.index || 9999));
}

// ---------- 实时思考流：一题一栏 ----------
// 数据来自 /api/runs/current/events 的游标式拉取（引擎每 60 字左右合并一条打出来）。
// 这里只做三件事：把增量并进每题的块列表、按"在跑 + 最近结束"决定有哪几栏、只重画
// 变过的栏——一道题能吐几千个增量，整屏重排会把浏览器拖到跑不动。
const STREAM_MAX_ENTRIES = 300;   // 每栏留下的块数（文本块已经合并过）
const STREAM_ENTRY_CHARS = 4000;  // 单个文本块的上限
let streamCursor = 0;
let streamRunId = null;
let streamEvents = new Map();     // taskId -> [{kind, text, name, ...}]
let streamSteps = new Map();      // taskId -> {step, maxSteps}
let streamTruncated = false;
let streamPending = 0;
let streamClearedUntil = 0;
let streamDirty = new Set();
let eventTimer = null;
let eventsBusy = false;

function resetStream() {
  streamCursor = 0;
  streamEvents = new Map();
  streamSteps = new Map();
  streamSeq.clear();
  streamOpenGroup.clear();
  streamPendingCall.clear();
  streamEcho.clear();
  streamOpenProse.clear();
  streamDirty = new Set();
  streamTruncated = false;
  streamPending = 0;
  streamClearedUntil = 0;
  const grid = document.querySelector('#streamGrid');
  if (grid) grid.innerHTML = '<div class="empty-signal">等待任务进入 worker…</div>';
}

function proseBlock(task, kind) {
  let slots = streamOpenProse.get(task);
  if (!slots) { slots = { reasoning: null, text: null }; streamOpenProse.set(task, slots); }
  return slots;
}

// 同一轮里的思考/发言必须并成一块往下续写；上游发碎片的、发累计快照的、把思考再当发言发一遍的都有，
// 这里统一收进同一个块，只在 step_end（一轮结束）之后换新块。散成一排条就没法读了。
function appendProse(list, task, kind, raw) {
  const delta = String(raw || '');
  if (!delta.trim()) return;
  const slots = proseBlock(task, kind);
  const block = slots[kind];
  if (block && list.includes(block)) {
    const body = String(block.text || '');
    if (body.trim() === delta || body.includes(delta)) {      // 重复帧 / 已包含
      block.seq = Math.max(block.seq, 0);
      return;
    }
    if (delta.startsWith(body.trim())) {                        // 累计快照：整段覆盖
      block.text = delta.length > STREAM_ENTRY_CHARS ? clip(delta, STREAM_ENTRY_CHARS) : delta;
      return;
    }
    const merged = `${body}${delta}`;   // 正常增量：原样相接（空格由上游带），别 trim 掉
    block.text = merged.length > STREAM_ENTRY_CHARS ? clip(merged, STREAM_ENTRY_CHARS) : merged;
    return;
  }
  const item = { kind, text: delta, seq: 0 };
  slots[kind] = item;
  list.push(item);
}

function consumeEvents(batch) {
  for (const event of batch.events || []) {
    if (event.seq <= streamClearedUntil || !event.task) continue;
    if (!streamEvents.has(event.task)) streamEvents.set(event.task, []);
    const list = streamEvents.get(event.task);
    if (event.kind === 'step') {
      streamSteps.set(event.task, { step: event.idx, maxSteps: event.max_steps });
      streamOpenProse.delete(event.task);        // 新一轮，思考/发言各起一块
      list.push({ ...event });
      streamDirty.add(event.task);
      continue;
    }
    if (event.kind === 'reasoning' || event.kind === 'text') {
      const delta = String(event.text || '');
      if (!delta.trim()) continue;
      if (event.kind === 'reasoning') {
        streamEcho.set(event.task, `${streamEcho.get(event.task) || ''}${delta}`.trim());
        appendProse(list, event.task, 'reasoning', delta);
      } else if ((streamEcho.get(event.task) || '').includes(delta.trim())) {
        continue;                                 // 上游把这段既当思考又当发言发过，只留一份
      } else {
        appendProse(list, event.task, 'text', delta);
      }
    } else {
      if (event.kind === 'step_end') streamOpenProse.delete(event.task);   // 一轮收尾，下轮新块
      list.push({ ...event });
    }
    if (list.length > STREAM_MAX_ENTRIES) list.splice(0, list.length - STREAM_MAX_ENTRIES);
    streamDirty.add(event.task);
  }
}

// ---- 折叠式时间线：一行一件事；参数、输出点开才看，不把 JSON 括号摊在脸上 ----
const STREAM_MAX_GROUPS = 26;          // 每栏最多留多少个 STEP 组，超了从头顶删
const streamSeq = new Map();           // taskId -> 已渲染到的事件 seq（增量追加的游标）
const streamOpenGroup = new Map();     // taskId -> 当前 STEP 组的 .tl-rows 容器
const streamPendingCall = new Map();   // taskId -> 等结果回填的那一行工具
const streamEcho = new Map();          // taskId -> 已被当成思考吞掉的那段文本
const streamOpenProse = new Map();     // taskId -> {reasoning: item|null, text: item|null} 本栏正在累积的块
const streamDensity = localStorage.getItem('tracebench-stream-density') || 'compact';

function clip(value, max) {
  const text = String(value ?? '').replace(/\s+/g, ' ').trim();
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

function sizeLabel(chars) {
  const n = Number(chars) || 0;
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${n} B`;
}

// 引擎给的 arguments/observation 多是 JSON；被单引号包起来的 Python repr 也常见。
// 没有双引号时把单引号当 JSON 引号试一次，有双引号就不动它，免得改坏内容里的撇号。
function parseLoose(raw) {
  const text = String(raw ?? '').trim();
  if (!text || !'([{'.includes(text[0])) return null;
  try { return JSON.parse(text); } catch (_) { /* 继续 */ }
  if (!text.includes('"')) {
    try { return JSON.parse(text.replace(/'/g, '"')); } catch (_) { return null; }
  }
  return null;
}

const ARG_PRIMARY_KEYS = ['command', 'cmd', 'path', 'file_path', 'filename', 'file', 'query',
                          'prompt', 'question', 'instruction', 'url', 'pattern', 'symbol',
                          'term', 'key', 'id', 'code', 'script'];
// 没参数、或者参数只有路径这种没什么信息量时，用工具名给一句人话。
const TOOL_LABELS = { list_files: '列出文件', read_file: '读文件', write_file: '写文件',
                      edit_file: '改文件', run_python: '跑 Python', run_bash: '跑命令',
                      run_shell: '跑命令', run_tests: '跑测试', memory_search: '检索记忆',
                      memory_write: '写记忆', done: '完成交卷' };
const LONG_TEXT_KEYS = new Set(['content', 'body', 'new_text', 'old_text', 'data', 'input',
                                'payload', 'text', 'edits', 'operations', 'messages']);

function prettyValue(value, limit = STREAM_ENTRY_CHARS) {
  let text;
  if (typeof value === 'string') text = value;
  else {
    try { text = JSON.stringify(value, null, 2); } catch (_) { text = String(value); }
  }
  return text.length > limit ? `${text.slice(0, limit)}\n…（共 ${sizeLabel(text.length)}，已截断）` : text;
}

// 严格 JSON.parse 失败时的兜底：按 "key": value 扫一遍，能认多少算多少。
// 引擎那边会把过长的 arguments/observation 截断（1200 字），截过的 JSON 是解析不出来的；
// python repr 的单引号写法也常见。这两种情况都要有一行摘要可看，不能把括号糊满屏。
const SCALAR_PAIR_RE = /(['"])([A-Za-z_][\w.]{0,40})\1\s*:\s*(?:"((?:[^"\\]|\\.){0,220})"|'((?:[^'\\]|\\.){0,220})'|(-?\d+(?:\.\d+)?)|(true|false|null))/g;

function loosePairs(text) {
  const out = [];
  const re = new RegExp(SCALAR_PAIR_RE.source, 'g');
  let match;
  while ((match = re.exec(String(text ?? ''))) !== null) {
    const key = match[2];
    const value = match[3] ?? match[4] ?? match[5] ?? match[6];
    if (value === undefined || value === null) continue;
    out.push([key, String(value).replace(/\\r?\\n/g, '\n').replace(/\\t/g, '\t')]);
  }
  return out;
}

function pickPrimary(entries) {
  for (const key of ARG_PRIMARY_KEYS) {
    const hit = entries.find(([name]) => name === key);
    if (hit && (typeof hit[1] === 'string' || typeof hit[1] === 'number')) return clip(hit[1], 190);
  }
  const firstString = entries.find(([, value]) => typeof value === 'string' && String(value).trim());
  return firstString ? clip(firstString[1], 190) : '';
}

// 参数摘要：一行看得懂的话 + 小尾巴；长内容和嵌套结构进展开区。
// 参数串常被服务端截断成半个 JSON；这时直接按字段名在原文里找值，别退化成「工具返回」。
function extractKey(raw, key) {
  // 值允许没有收尾引号：服务端截断过的参数串经常断在半路。
  const re = '"' + key + '"\\s*:\\s*"([^"]{0,240})(?:"|$)';
  const m = String(raw || '').match(new RegExp(re));
  if (!m) return '';
  return m[1];
}

function summarizeArgs(raw) {
  const parsed = parseLoose(raw);
  if (parsed === null || typeof parsed !== 'object' || parsed === undefined) {
    const pairs = loosePairs(raw);
    if (!pairs.length) {
      for (const key of ARG_PRIMARY_KEYS) {
        const value = extractKey(raw, key);
        if (value) return { primary: clip(String(value), 190), chips: [], sections: [] };
      }
      return { primary: clip(raw, 150), chips: [], sections: [] };
    }
    const chips = [];
    const sections = [];
    const primary = pickPrimary(pairs);
    for (const [key, value] of pairs) {
      if (primary && String(value) === primary) continue;   // 已经摆在行首了，别再来一遍
      if (value.length > 90) {
        sections.push({ label: `参数 · ${key}`, text: prettyValue(value) });
        chips.push(`${key} ${sizeLabel(value.length)}`);
      } else {
        chips.push(`${key}=${clip(value, 42)}`);
      }
    }
    return { primary: primary || '(无参数)', chips: chips.slice(0, 5), sections };
  }
  const entries = Array.isArray(parsed)
    ? parsed.map((item, index) => [`[${index}]`, item])
    : Object.entries(parsed);
  let primary = '';
  const chips = [];
  const sections = [];
  for (const key of ARG_PRIMARY_KEYS) {
    const hit = entries.find(([name]) => name === key);
    if (hit && (typeof hit[1] === 'string' || typeof hit[1] === 'number')) {
      primary = clip(hit[1], 190);
      break;
    }
  }
  const used = new Set();
  for (const key of ARG_PRIMARY_KEYS) {
    const hit = entries.find(([name]) => name === key);
    if (hit && typeof hit[1] === 'string' && clip(hit[1], 190) === primary) used.add(key);
  }
  for (const [key, value] of entries) {
    if (!primary && typeof value === 'string') primary = clip(value, 190);
    if (used.has(key)) continue;
    if (LONG_TEXT_KEYS.has(key)) {
      const text = prettyValue(value);
      sections.push({ label: `参数 · ${key}`, text });
      chips.push(`${key} ${sizeLabel(String(text).length)}`);
    } else if (value !== null && typeof value === 'object') {
      const count = Array.isArray(value) ? `${value.length} 项` : `${Object.keys(value).length} 字段`;
      sections.push({ label: `参数 · ${key}`, text: prettyValue(value, 2400) });
      chips.push(`${key} ${count}`);
    } else if (typeof value === 'string') {
      chips.push(`${key}=${clip(value, 42)}`);
    } else if (value !== undefined && value !== null) {
      chips.push(`${key}=${value}`);
    }
  }
  return { primary: primary || '(无参数)', chips: chips.slice(0, 5), sections };
}

function lineCountOf(text) {
  return String(text).split('\n').filter(Boolean).length;
}

// 工具结果摘要：成败/退出码放最前，正文按 stdout / stderr / content 分块。
function summarizeObservation(raw, ok) {
  const parsed = parseLoose(raw);
  const chips = [];
  const sections = [];
  if (parsed === null || typeof parsed !== 'object' || parsed === undefined) {
    // 拆不开（截断过的 JSON、python repr）：至少把退出码和输出摘成一行，正文收进展开区。
    const pairs = Object.fromEntries(loosePairs(raw));
    if (ok === false) chips.push('失败');
    const code = pairs.exit_code ?? pairs.returncode ?? pairs.exit_status;
    if (code !== undefined) chips.push(`exit ${code}`);
    for (const [key, label] of [['stdout', '输出'], ['output', '输出'], ['stderr', '错误'], ['content', '内容']]) {
      if (!pairs[key]) continue;
      const text = String(pairs[key]);
      sections.push({ label, text: prettyValue(text), lines: lineCountOf(text) });
      chips.push(`${label} ${lineCountOf(text)} 行`);
    }
    if (!sections.length && !chips.length) {
      const fallback = String(raw ?? '').replace(/\\r?\\n/g, '\n').replace(/\\t/g, '\t');
      return { chips: [], sections: [{ label: '返回', text: prettyValue(fallback) }] };
    }
    return { chips: chips.slice(0, 5), sections };
  }
  // 引擎把结果包成 {name, ok, result}；先把这层壳剥掉，再按内容形状分块。
  let source = parsed;
  const wrapped = ('result' in parsed) && ('ok' in parsed || 'name' in parsed);
  if (wrapped) source = parsed.result;
  if (typeof source === 'string') {
    return { chips: [], sections: [{ label: '返回', text: prettyValue(source), lines: lineCountOf(source) }] };
  }
  if (Array.isArray(source)) {
    const items = source.map((item) => (typeof item === 'string' ? item : JSON.stringify(item)));
    return {
      chips: [`文件 ${items.length} 个`],
      sections: [{ label: '返回', text: prettyValue(items.join('\n')), lines: items.length }],
    };
  }
  if (source === null || source === undefined) {
    return { chips: ['空返回'], sections: [] };
  }
  if (ok === false) chips.push('失败');
  const codeKeys = ['exit_code', 'returncode', 'exit_status', 'code'].filter((key) =>
    source[key] !== undefined && source[key] !== null);
  if (codeKeys.length) chips.push(`exit ${source[codeKeys[0]]}`);
  const lineCount = lineCountOf;
  for (const [key, value] of Object.entries(source)) {
    if (value === null || value === undefined || key === 'ok') continue;
    if (codeKeys.includes(key)) continue;                       // 退出码已经做成 chip，别再来一遍
    if (typeof value === 'string' && !value.trim()) continue;   // 空 stdout/stderr 不值得占一行
    if (typeof value === 'string') {
      const label = ({ stdout: '输出', output: '输出', stderr: '错误', error: '错误',
                       content: '内容', message: '说明', detail: '说明', summary: '摘要',
                       written: '写入', edited: '已改', path: '路径', matches: '命中' })[key] || key;
      sections.push({ label, text: prettyValue(value), lines: lineCount(value) });
      if (['stdout', 'output', 'stderr', 'content'].includes(key)) {
        chips.push(`${label} ${lineCount(value)} 行`);
      }
    } else if (Array.isArray(value)) {
      const text = value.map((item) => (typeof item === 'string' ? item : prettyValue(item))).join('\n');
      sections.push({ label: key, text: prettyValue(text), lines: value.length });
      chips.push(`${key} ${value.length} 项`);
    } else if (typeof value === 'number' || typeof value === 'boolean') {
      const label = ({ passed: '通过', failed: '失败', errors: '报错', total: '总计',
                       remaining_occurrences: '剩余匹配', hits: '命中', recorded: '已记录' })[key] || key;
      chips.push(`${label}=${value}`);
    }
  }
  return { chips: chips.slice(0, 5), sections };
}

// 展开密度：紧凑=短思考自动开、长的和工具细节收起；详细=短的自动开；全开=全开。
// 紧凑模式下思考/发言默认收起成一条；正文跟预览重复摊开反而更挤。极短的（一句话）才直接展开。
function densityOpen(length) {
  if (streamDensity !== 'compact') return true;
  return length <= 40;
}

const toolAutoOpen = () => streamDensity === 'raw';

const TL_TONE = { 输出: 'out', 错误: 'err' };

function tlSectionsHtml(sections) {
  return sections.map((section) => `
    <section class="tl-part${TL_TONE[section.label] ? ` tl-${TL_TONE[section.label]}` : ''}">
      <span>${esc(section.label)}${section.lines ? ` · ${section.lines} 行` : ''}</span>
      <pre>${esc(section.text)}</pre>
    </section>`).join('');
}

// 「思考 / 发言」一行：折叠时只看第一句，点开读全段。
function streamProseNode(entry, kind) {
  const text = String(entry.text || '');
  if (!text.trim()) return '';
  const label = kind === 'reasoning' ? '思考' : '发言';
  const open = densityOpen(text.length);
  return `<details class="tl-item tl-${kind}" data-seq="${entry.seq}"${open ? ' open' : ''}>
    <summary><span class="tl-kind">${label}</span><span class="tl-preview">${esc(clip(text, 170))}</span></summary>
    <div class="tl-body"><p>${esc(text)}</p></div>
  </details>`;
}

// 工具调用：一行「工具名 + 干了什么」，参数和返回都在展开区里。
function streamToolCallNode(entry) {
  const args = summarizeArgs(entry.arguments);
  const sections = tlSectionsHtml(args.sections);
  return `<details class="tl-item tl-tool pending" data-seq="${entry.seq}">
    <summary>
      <b class="tl-name">${esc(entry.name || '?')}</b>
      <code class="tl-oneline">${esc(args.primary || TOOL_LABELS[entry.name] || '')}</code>
      <em class="tl-chips"><i></i>运行中</em>
    </summary>
    <div class="tl-detail">${sections || '<div class="tl-note">这个调用没有带参数。</div>'}</div>
  </details>`;
}

// 结果回来时回填到同一行；找不到对应调用就单独成一行。
function streamResultFill(row, entry) {
  const obs = summarizeObservation(entry.observation, entry.ok);
  row.classList.remove('pending');
  row.classList.add(entry.ok === false ? 'bad' : 'ok');
  if (toolAutoOpen()) row.setAttribute('open', '');
  // 摘要尾巴只留一颗：先摘掉"运行中"那颗占位的，再挂真实结果。
  row.querySelectorAll('summary > .tl-chips').forEach((node) => node.remove());
  const chips = document.createElement('em');
  chips.className = 'tl-chips';
  chips.textContent = obs.chips.length ? obs.chips.join(' · ') : (entry.ok === false ? '失败' : '完成');
  row.querySelector('summary')?.appendChild(chips);
  const detail = row.querySelector('.tl-detail');
  if (detail) detail.innerHTML += tlSectionsHtml(obs.sections);
  // 参数被截断时行首只剩「工具返回」这种占位；用参数里还能找到的字段把标题补回来。
  const line = row.querySelector('.tl-oneline');
  if (line && /^(工具返回|\(无参数\))$/.test(line.textContent.trim())) {
    for (const key of ARG_PRIMARY_KEYS) {
      const value = extractKey(entry.arguments, key);
      if (value) { line.textContent = clip(String(value), 190); break; }
    }
  }
}

function streamStandaloneResultNode(entry) {
  const obs = summarizeObservation(entry.observation, entry.ok);
  const total = obs.sections.reduce((sum, section) => sum + section.text.length, 0);
  return `<details class="tl-item tl-tool ${entry.ok === false ? 'bad' : 'ok'}" data-seq="${entry.seq}"${toolAutoOpen() ? ' open' : ''}>
    <summary>
      <b class="tl-name">${esc(entry.name || '结果')}</b>
      <code class="tl-oneline">工具返回</code>
      <em class="tl-chips">${esc(obs.chips.length ? obs.chips.join(' · ') : (entry.ok === false ? '失败' : '完成'))}</em>
    </summary>
    <div class="tl-detail">${tlSectionsHtml(obs.sections)}</div>
  </details>`;
}

function ensureStreamGroup(taskId, body) {
  let rows = streamOpenGroup.get(taskId);
  if (rows && body.contains(rows)) return rows;
  const section = document.createElement('section');
  section.className = 'tl-step open';
  section.innerHTML = '<header><span>STEP —</span><em></em></header><div class="tl-rows"></div>';
  body.appendChild(section);
  const created = section.querySelector('.tl-rows');
  streamOpenGroup.set(taskId, created);
  return created;
}

function capStreamGroups(body) {
  const steps = [...body.querySelectorAll('.tl-step')];
  if (steps.length <= STREAM_MAX_GROUPS) return;
  steps.slice(0, steps.length - STREAM_MAX_GROUPS).forEach((node) => node.remove());
}

// 增量渲染：只画 seq 比游标新的条目，已展开的折叠区不会被重画掉。
function appendStreamColumn(taskId, body, list, showReasoning, forceFull = false) {
  if (forceFull) {
    body.innerHTML = '';
    streamSeq.delete(taskId);
    streamOpenGroup.delete(taskId);
    streamPendingCall.delete(taskId);
  }
  const lastSeq = streamSeq.get(taskId) ?? -1;
  let appended = 0;
  for (const entry of list) {
    const seq = Number(entry.seq ?? -1);
    if (seq <= lastSeq) continue;
    const rows = ensureStreamGroup(taskId, body);
    if (entry.kind === 'step') {
      const section = document.createElement('section');
      section.className = 'tl-step';
      section.dataset.step = String(entry.idx ?? '?');
      section.innerHTML = `<header><span>STEP ${Number(entry.idx) || '?'}</span>`
        + `<em>${entry.max_steps ? `共 ${Number(entry.max_steps)} 步` : ''}</em></header><div class="tl-rows"></div>`;
      body.appendChild(section);
      streamOpenGroup.set(taskId, section.querySelector('.tl-rows'));
      streamPendingCall.delete(taskId);
    } else if (entry.kind === 'step_end') {
      const stepEl = [...body.querySelectorAll('.tl-step')].slice(-1)[0];
      const chip = stepEl?.querySelector('header > em');
      if (chip) {
        chip.textContent = [
          `${Number(entry.latency_ms) || 0}ms`,
          `${compactNumber(Number(entry.tokens) || 0)} tok`,
          entry.completion_tokens ? `输出 ${compactNumber(entry.completion_tokens)}` : '',
          entry.reasoning_tokens ? `推理 ${compactNumber(entry.reasoning_tokens)}` : '',
          `${Number(entry.tool_calls) || 0} 次调用`,
        ].filter(Boolean).join(' · ');
      }
    } else if (entry.kind === 'reasoning' && !showReasoning) {
      // 「显示模型思考」关掉时整段跳过，只看动作。
    } else if (entry.kind === 'reasoning' || entry.kind === 'text') {
      rows.insertAdjacentHTML('beforeend', streamProseNode(entry, entry.kind));
    } else if (entry.kind === 'tool_call') {
      rows.insertAdjacentHTML('beforeend', streamToolCallNode(entry));
      streamPendingCall.set(taskId, rows.lastElementChild);
    } else if (entry.kind === 'tool_result') {
      const row = streamPendingCall.get(taskId);
      if (row && row.classList.contains('pending')) {
        streamResultFill(row, entry);
        streamPendingCall.delete(taskId);
      } else {
        rows.insertAdjacentHTML('beforeend', streamStandaloneResultNode(entry));
      }
    }
    if (seq >= 0) streamSeq.set(taskId, seq);
    appended += 1;
  }
  capStreamGroups(body);
  return appended;
}

function renderStream(state, records) {
  const grid = document.querySelector('#streamGrid');
  if (!grid) return;
  const byId = new Map(records.map((item) => [item.taskId, item]));
  const runningIds = records.filter((item) => item.status === 'running').map((item) => item.taskId);
  const lastSeq = (id) => ((streamEvents.get(id) || []).slice(-1)[0] || {}).seq || 0;
  const finishedIds = [...streamEvents.keys()].filter((id) => !runningIds.includes(id))
    .sort((a, b) => lastSeq(b) - lastSeq(a));
  // 栏位 = 正在跑的题 + 最近结束的几道（留一栏让人看最后发生了什么），最多 6 栏。
  // 按题目序号排，不按"谁刚结束"排：一道题跑完就换位置的话，盯着看的人会突然找不到它。
  const ids = [...runningIds, ...finishedIds.slice(0, Math.max(0, 6 - runningIds.length))]
    .sort((a, b) => ((byId.get(a) || {}).index || 999) - ((byId.get(b) || {}).index || 999));
  const totalEvents = [...streamEvents.values()].reduce((sum, list) => sum + list.length, 0);
  document.querySelector('#activeCount').textContent = `${runningIds.length} ACTIVE`;
  const tabCount = document.querySelector('#tabLiveCount');
  if (tabCount) tabCount.textContent = `${ids.length} 栏`;
  document.querySelector('#streamSummary').textContent = totalEvents
    ? `${ids.length} 栏 · ${totalEvents} 条事件${streamPending ? ` · 待取 ${streamPending}` : ''}${streamTruncated ? ' · 早期内容已被缓冲截断' : ''}`
    : '等待事件…';
  const foot = document.querySelector('#streamFoot');
  if (foot) {
    foot.textContent = streamTruncated
      ? '早期内容已被缓冲截断（每轮最多留 8000 条事件）；完整过程在轨迹文件里。'
      : '一行一件事：思考和发言默认只看第一句，工具行默认只显示干了什么，点开才看参数和返回。';
  }

  if (!ids.length) {
    const label = ACTIVE.has(state.status) ? '任务还没开跑，worker 正在取题…' : '当前没有运行中的任务';
    grid.innerHTML = `<div class="empty-signal">${esc(label)}</div>`;
    streamDirty.clear();
    return;
  }
  const showReasoning = document.querySelector('#streamReasoning')?.checked !== false;
  const follow = document.querySelector('#streamFollow')?.checked !== false;
  if (grid.querySelector('.empty-signal')) grid.innerHTML = '';
  for (const id of [...grid.children].map((node) => node.dataset.task).filter((task) => task && !ids.includes(task))) {
    grid.querySelector(`[data-task="${CSS.escape(id)}"]`)?.remove();
  }
  ids.forEach((id, slot) => {
    const record = byId.get(id) || { taskId: id };
    const list = streamEvents.get(id) || [];
    const step = streamSteps.get(id) || {};
    let column = grid.querySelector(`[data-task="${CSS.escape(id)}"]`);
    const isNew = !column;
    if (isNew) {
      column = document.createElement('article');
      column.className = 'stream-column';
      column.dataset.task = id;
      column.innerHTML = '<header></header><div class="stream-body"></div>';
      grid.appendChild(column);
    }
    const statusName = record.result || record.status || 'running';
    const statusLabel = { running: 'RUNNING', completed: 'DONE', pending: 'WAITING', PASS: 'PASS', FAIL: 'FAIL' }[statusName] || String(statusName).toUpperCase();
    const tokens = record.tokens == null ? '—' : Number(record.tokens).toLocaleString();
    const elapsed = record.elapsedSeconds == null ? '' : ` · ${Number(record.elapsedSeconds).toFixed(1)}s`;
    column.querySelector('header').innerHTML = `
      <span>COL ${String(slot + 1).padStart(2, '0')} · TASK ${String(record.index || '—').padStart(2, '0')}</span>
      <b>${esc(record.taskId || id)}</b>
      <small>${esc(record.title || record.category || '')}</small>
      <div class="stream-meta">
        <em class="queue-state ${esc(statusName)}"><i></i>${esc(statusLabel)}</em>
        ${step.step ? `<span>STEP ${step.step}${step.maxSteps ? ` / ${step.maxSteps}` : ''}</span>` : ''}
        <span>${esc(tokens)} tok${esc(elapsed)}</span>
        <span>${list.length} 条</span>
      </div>`;
    // 只追加新事件；整栏重画会把刚展开的折叠区和滚动位置一起抹掉。
    const body = column.querySelector('.stream-body');
    if (isNew || streamDirty.has(id)) {
      const added = appendStreamColumn(id, body, list, showReasoning, isNew);
      if (!body.querySelector('.tl-step')) body.innerHTML = '<div class="stream-idle">等待第一个增量…</div>';
      if (added && (follow || body.scrollHeight - body.scrollTop - body.clientHeight < 40)) {
        body.scrollTop = body.scrollHeight;
      }
    }
  });
  streamDirty.clear();
}

async function pollEvents() {
  clearTimeout(eventTimer);
  eventTimer = null;
  if (eventsBusy) return;
  eventsBusy = true;
  try {
    if (!viewedRunId) {
      const batch = await api(`/api/runs/current/events?since=${streamCursor}&limit=400`);
      if (batch.runId && batch.runId !== streamRunId) {
        resetStream();
        streamRunId = batch.runId;
      }
      if (batch.events?.length) {
        consumeEvents(batch);
        renderStream(currentState || {}, computedRecords(currentState || {}));
      }
      streamCursor = batch.cursor ?? streamCursor;
      streamTruncated = Boolean(batch.truncated);
      streamPending = batch.pending || 0;
    }
  } catch (error) {
    /* 事件拉取失败不弹窗：主轮询会把连接状态说清楚，这里静默重试。 */
  } finally {
    eventsBusy = false;
  }
  eventTimer = !document.hidden && ACTIVE.has(currentState?.status) ? setTimeout(pollEvents, 350) : null;
}


function renderTasks(state, records) {
  const filtered = records.filter((task) => {
    if (queueFilter === 'all') return true;
    if (queueFilter === 'pending' || queueFilter === 'running') return task.status === queueFilter;
    return task.result === queueFilter;
  });
  const body = document.querySelector('#taskRows');
  if (!filtered.length) {
    body.innerHTML = '<tr><td colspan="9" class="empty-cell">这个筛选条件下没有任务</td></tr>';
    return;
  }
  body.innerHTML = filtered.map((task) => {
    const stateName = task.result || task.status || 'pending';
    const stateLabel = { running: 'RUNNING', pending: 'WAITING', completed: 'DONE', PASS: 'PASS', FAIL: 'FAIL' }[stateName] || stateName.toUpperCase();
    const canRerun = Boolean(state.rerunnable || ['completed', 'interrupted'].includes(state.status)) && task.status === 'completed' && !ACTIVE.has(state.status);
    const action = canRerun ? `<button class="task-rerun" data-rerun-task="${esc(task.taskId)}" type="button">↻ 重跑</button>` : '<span class="task-action-empty">—</span>';
    return `<tr><td class="mono">${String(task.index || '—').padStart(2, '0')}</td><td><div class="task-id">${esc(task.taskId)}</div><div class="task-title">${esc(task.title || '')}</div></td><td class="mono">${esc(task.category || '—')}</td><td><span class="queue-state ${esc(stateName)}"><i></i>${esc(stateLabel)}</span></td><td class="mono">${task.steps ?? '—'}</td><td class="mono">${task.tokens == null ? '—' : Number(task.tokens).toLocaleString()}</td><td class="mono">${task.elapsedSeconds == null ? '—' : `${Number(task.elapsedSeconds).toFixed(1)}s`}</td><td><span class="evidence" title="${esc(task.failure || '')}">${esc(task.failure || '—')}</span></td><td>${action}</td></tr>`;
  }).join('');
}

// "[ev] {json}" 这类转发行：给出人话摘要，细节收进"详情"里点开看。
function eventLogLine(item) {
  const seq = String(item.seq || '').padStart(4, '0');
  const raw = (item.line || '').replace(/^\[ev\]\s*/, '');
  const data = parseLoose(raw);
  if (!data) return `<div class="terminal-line ev"><span>${seq}</span><code>${esc(raw)}</code></div>`;
  const task = clip(data.task || '', 26);
  let text = '';
  let tone = '';
  let more = '';
  switch (data.kind) {
    case 'step':
      tone = 'ev-step';
      text = `STEP ${data.idx}${data.max_steps ? ` / ${data.max_steps}` : ''} · ${task}`;
      break;
    case 'step_end':
      tone = 'ev-metric';
      text = [`${Number(data.latency_ms) || 0}ms`, `${compactNumber(Number(data.tokens) || 0)} tok`,
              `${Number(data.tool_calls) || 0} 次调用`].join(' · ') + ` · STEP ${data.idx}`;
      break;
    case 'reasoning':
      tone = 'ev-think';
      text = `思考 · ${clip(data.text, 150)}`;
      more = String(data.text || '').slice(0, 2400);
      break;
    case 'text':
      tone = 'ev-say';
      text = `发言 · ${clip(data.text, 150)}`;
      more = String(data.text || '').slice(0, 2400);
      break;
    case 'tool_call': {
      const args = summarizeArgs(data.arguments);
      tone = 'ev-call';
      text = `${data.name || '?'} · ${args.primary}${args.chips.length ? ` · ${args.chips.join(' · ')}` : ''}`;
      more = prettyValue(parseLoose(data.arguments) ?? data.arguments, 2400);
      break;
    }
    case 'tool_result': {
      const obs = summarizeObservation(data.observation, data.ok);
      tone = data.ok === false ? 'ev-bad' : 'ev-ok';
      text = `${data.name || '结果'} ${obs.chips.length ? obs.chips.join(' · ') : (data.ok === false ? '失败' : '完成')}`;
      more = obs.sections.map((s) => `${s.label}${s.lines ? `（${s.lines} 行）` : ''}\n${s.text}`).join('\n\n').slice(0, 3000);
      break;
    }
    default:
      return `<div class="terminal-line ev"><span>${seq}</span><code>${esc(raw)}</code></div>`;
  }
  const toggle = more
    ? `<button type="button" class="ev-more-button">详情</button><pre class="ev-more" hidden>${esc(more)}</pre>`
    : '';
  return `<div class="terminal-line ${tone}"><span>${seq}</span><div class="ev-cell"><code>${esc(text)}</code>${toggle}</div></div>`;
}

function appendLogs(logs) {
  if (!logs?.length) return;
  const fresh = logs.filter((item) => !seenLogSequences.has(item.seq));
  if (!fresh.length) return;
  for (const item of fresh) seenLogSequences.add(item.seq);
  allLogs.push(...fresh);
  if (allLogs.length > 2500) allLogs = allLogs.slice(-2200);
  const terminal = document.querySelector('#terminal');
  if (terminal.querySelector('.terminal-empty')) terminal.innerHTML = '';
  for (const item of fresh) {
    const line = item.line || '';
    let type = '';
    if (line.startsWith('[ev] ')) {
      // 引擎把流式事件也转发进日志；整段 JSON 摊在终端里最难读，换成一行摘要 + 可展开原文。
      terminal.insertAdjacentHTML('beforeend', eventLogLine(item));
      continue;
    }
    if (line.startsWith('[run ')) type = 'run-start';
    else if (line.startsWith('[end ')) type = `run-end ${line.includes(' PASS ') ? 'pass' : 'fail'}`;
    else if (line.startsWith('[control]') || line.includes('[control]')) type = 'control';
    else if (line.startsWith('[web]')) type = 'control';
    else if (item.stream === 'error' || line.includes('[web:error]')) type = 'error';
    terminal.insertAdjacentHTML('beforeend', `<div class="terminal-line ${type}"><span>${String(item.seq || '').padStart(4, '0')}</span><code>${esc(line)}</code></div>`);
  }
  while (terminal.children.length > 2200) terminal.firstElementChild.remove();
  if (document.querySelector('#autoFollow').checked) terminal.scrollTop = terminal.scrollHeight;
}

// 终端里的"详情"：就地展开/收起，不改行序。
document.querySelector('#terminal')?.addEventListener('click', (event) => {
  const button = event.target.closest('.ev-more-button');
  if (!button) return;
  const box = button.parentElement?.querySelector('.ev-more');
  if (!box) return;
  box.hidden = !box.hidden;
  button.textContent = box.hidden ? '详情' : '收起';
});

function render(state) {
  currentState = state;
  mergeTaskRuns(state);
  const records = computedRecords(state);
  const completed = records.filter((item) => item.result === 'PASS' || item.result === 'FAIL');
  const totalTokens = state.totalTokens ?? completed.reduce((sum, item) => sum + (item.tokens || 0), 0);
  const totalSteps = state.totalSteps ?? completed.reduce((sum, item) => sum + (item.steps || 0), 0);
  const taskSeconds = state.totalTaskSeconds ?? completed.reduce((sum, item) => sum + (item.elapsedSeconds || 0), 0);
  const progress = state.total ? Math.min(100, ((state.completed || completed.length) / state.total) * 100) : 0;
  const status = state.status || 'idle';
  const statusNode = document.querySelector('#runStatus');
  statusNode.className = `ops-status ${status}`;
  statusNode.querySelector('span').textContent = LABELS[status] || status.toUpperCase();
  document.querySelector('#runId').textContent = state.id || 'NO ACTIVE RUN';
  document.querySelector('#modelName').textContent = state.model || '没有正在运行的评测';
  document.querySelector('#apiModel').textContent = `API ${state.apiModel || '—'}`;
  document.querySelector('#runProfile').textContent = `PROFILE ${state.profile || '—'}`;
  document.querySelector('#runPid').textContent = `PID ${state.pid || '—'}`;
  document.querySelector('#elapsedClock').textContent = formatClock(state.elapsedSeconds);
  document.querySelector('#progressText').textContent = `${state.completed || completed.length} / ${state.total || records.length}`;
  document.querySelector('#progressPercent').textContent = `${progress.toFixed(1)}%`;
  document.querySelector('#progressBar').style.width = `${progress}%`;
  document.querySelector('#progressMarker').style.left = `${progress}%`;
  document.querySelector('#passedCount').textContent = state.passed ?? completed.filter((item) => item.result === 'PASS').length;
  document.querySelector('#failedCount').textContent = state.failed ?? completed.filter((item) => item.result === 'FAIL').length;
  const completeCount = state.completed || completed.length;
  document.querySelector('#passRate').textContent = `${completeCount ? ((state.passed || 0) / completeCount * 100).toFixed(1) : '0.0'}% pass rate`;
  document.querySelector('#failRate').textContent = `${completeCount ? ((state.failed || 0) / completeCount * 100).toFixed(1) : '0.0'}% fail rate`;
  document.querySelector('#tokenCount').textContent = compactNumber(totalTokens);
  document.querySelector('#avgTokens').textContent = `${completeCount ? Math.round(totalTokens / completeCount).toLocaleString() : 0} avg / task`;
  document.querySelector('#avgTime').textContent = `${completeCount ? (taskSeconds / completeCount).toFixed(1) : 0}s`;
  document.querySelector('#tokenSpeed').textContent = `${taskSeconds ? (totalTokens / taskSeconds).toFixed(1) : 0} tok/s accounted`;
  document.querySelector('#workerCount').textContent = state.jobs || '—';
  document.querySelector('#workerState').textContent = `${(state.currentTasks || []).length} active · ${totalSteps} steps done`;
  document.querySelector('#outputLimit').textContent = state.maxOutputTokens ? compactNumber(state.maxOutputTokens) : '—';
  document.querySelector('#requestTimeout').textContent = state.requestTimeoutSeconds ? `${state.requestTimeoutSeconds}s request timeout` : '— request timeout';

  const profile = runConfig?.models?.find((item) => item.name === state.profile);
  document.querySelector('#endpointValue').textContent = profile?.endpoint || '—';
  document.querySelector('#apiModelValue').textContent = state.apiModel || profile?.apiModel || '—';
  document.querySelector('#scopeValue').textContent = state.scope || '—';
  document.querySelector('#jobsValue').textContent = state.jobs ? `${state.jobs} workers` : '—';
  document.querySelector('#maxOutputValue').textContent = state.maxOutputTokens ? Number(state.maxOutputTokens).toLocaleString() : '—';
  document.querySelector('#timeoutValue').textContent = state.requestTimeoutSeconds ? `${state.requestTimeoutSeconds}s` : '—';
  document.querySelector('#outputValue').textContent = state.outputDir || '—';
  const judgeBadge = document.querySelector('#judgeState');
  if (judgeBadge) {
    // 把尺子名字贴在 badge 上：同一个控制台里看到的分数要能说清楚是哪把尺判的。
    judgeBadge.textContent = state.judgeProfile ? `JUDGE ${state.judgeProfile}`
      : (state.judge ? 'JUDGE ON' : 'JUDGE OFF');
    judgeBadge.dataset.on = state.judge ? 'true' : 'false';
  }

  const controlsSupported = Boolean(runConfig?.controls?.pauseResume);
  const controlReady = Boolean(state.controlReady);
  const oldActiveRun = ACTIVE.has(status) && !controlsSupported;
  document.querySelector('#compatBanner').hidden = !oldActiveRun;
  const pauseButton = document.querySelector('#pauseButton');
  const resumeButton = document.querySelector('#resumeButton');
  pauseButton.hidden = status === 'paused';
  resumeButton.hidden = status !== 'paused';
  pauseButton.disabled = status !== 'running' || !controlsSupported || !controlReady;
  resumeButton.disabled = status !== 'paused' || !controlsSupported || !controlReady;
  document.querySelector('#stopButton').disabled = !['starting', 'running', 'pausing', 'paused'].includes(status);
  const continueButton = document.querySelector('#continueRunButton');
  const missing = state.missingTasks || [];
  continueButton.hidden = !(status === 'interrupted' && missing.length);
  continueButton.disabled = !(status === 'interrupted' && missing.length);
  document.querySelector('#continueRunHint').textContent = missing.length ? `只执行缺失的 ${missing.length} 道题` : '从中断点恢复';

  renderStream(state, records);
  renderTasks(state, records);
  appendLogs(state.logs || []);
  lastSequence = Math.max(lastSequence, state.lastLogSequence || 0);
  document.querySelector('#lastUpdate').textContent = `SYNC ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`;
  document.title = `${LABELS[status] || status.toUpperCase()} · ${state.model || 'Tracebench'} / Live Run`;
}

async function poll(initial = false) {
  clearTimeout(pollTimer);
  try {
    if (initial) {
      const [config, history] = await Promise.all([api('/api/config'), api('/api/history')]);
      runConfig = config;
      historyRuns = history.runs || [];
      populateRunSelector();
      currentState = await api(viewedRunId ? `/api/runs/${encodeURIComponent(viewedRunId)}` : '/api/runs/current?since=0');
      lastSequence = 0;
      allLogs = [];
      seenLogSequences.clear();
      taskRecords.clear();
    } else {
      const previousRunId = currentState?.id;
      currentState = await api(viewedRunId ? `/api/runs/${encodeURIComponent(viewedRunId)}` : `/api/runs/current?since=${lastSequence}`);
      if (currentState.id !== previousRunId) {
        lastSequence = 0;
        allLogs = [];
        seenLogSequences.clear();
        taskRecords.clear();
        document.querySelector('#terminal').innerHTML = '<div class="terminal-empty">$ new run detected<span>_</span></div>';
        currentState = await api(viewedRunId ? `/api/runs/${encodeURIComponent(viewedRunId)}` : '/api/runs/current?since=0');
      }
    }
    setConnection(true);
    render(currentState);
    if (document.hidden) return;
    if (ACTIVE.has(currentState.status) && !eventTimer && !eventsBusy) pollEvents();
    // 没有 run 在跑就不要每 3 秒重画整页：关掉硬件加速之后那是白烧一核。
    pollTimer = setTimeout(() => poll(false), ACTIVE.has(currentState.status) ? 1000 : 10000);
  } catch (error) {
    setConnection(false);
    clearTimeout(eventTimer);
    eventTimer = null;
    document.querySelector('#lastUpdate').textContent = error.message;
    if (!document.hidden) pollTimer = setTimeout(() => poll(initial), 8000);
  }
}

function populateRunSelector() {
  const selector = document.querySelector('#runSelector');
  selector.innerHTML = '<option value="">当前 Runner</option>' + historyRuns.map((run) => `<option value="${esc(run.runId)}">${esc(run.runId)} · ${esc(run.model)} · ${run.total}/${run.expected || run.total}</option>`).join('');
  selector.value = viewedRunId;
}

async function continueInterruptedRun() {
  if (!currentState?.id || !currentState.missingTasks?.length) return;
  const count = currentState.missingTasks.length;
  if (!window.confirm(`继续 ${currentState.id}？\n\n只执行缺失的 ${count} 道题，已有结果保持不变。`)) return;
  try {
    currentState = await api(`/api/runs/${encodeURIComponent(currentState.id)}/resume`, { method: 'POST', body: '{}' });
    viewedRunId = currentState.id;
    history.replaceState(null, '', `console.html?run=${encodeURIComponent(viewedRunId)}`);
    lastSequence = 0;
    allLogs = [];
    seenLogSequences.clear();
    taskRecords.clear();
    render(currentState);
    toast(`已继续 ${count} 道缺失任务`);
  } catch (error) {
    toast(`续跑失败：${error.message}`, true);
  }
}

async function rerunTask(taskId) {
  if (!currentState?.id) return;
  if (!window.confirm(`重跑 ${taskId}？\n\n旧尝试会归档到该 run 的 .attempts/，其他任务结果不变。`)) return;
  try {
    currentState = await api(`/api/runs/${encodeURIComponent(currentState.id)}/tasks/${encodeURIComponent(taskId)}/rerun`, { method: 'POST', body: '{}' });
    viewedRunId = currentState.id;
    history.replaceState(null, '', `console.html?run=${encodeURIComponent(viewedRunId)}`);
    lastSequence = 0;
    allLogs = [];
    seenLogSequences.clear();
    taskRecords.clear();
    render(currentState);
    toast(`${taskId} 已进入重跑队列`);
  } catch (error) {
    toast(`重跑失败：${error.message}`, true);
  }
}

async function control(action) {
  const labels = { pause: '暂停', resume: '继续', cancel: '停止' };
  try {
    const state = await api(`/api/runs/current/${action}`, { method: 'POST', body: '{}' });
    render(state);
    toast(`${labels[action]}命令已发送`);
  } catch (error) {
    toast(`${labels[action]}失败：${error.message}`, true);
  }
}

document.querySelector('#pauseButton').addEventListener('click', () => control('pause'));
document.querySelector('#resumeButton').addEventListener('click', () => control('resume'));
document.querySelector('#stopButton').addEventListener('click', () => {
  if (window.confirm('确定停止当前评测？已完成任务的结果会保留，未完成任务将中断。')) control('cancel');
});
document.querySelector('#continueRunButton').addEventListener('click', continueInterruptedRun);
document.querySelector('#taskRows').addEventListener('click', (event) => {
  const button = event.target.closest('[data-rerun-task]');
  if (button) rerunTask(button.dataset.rerunTask);
});
document.querySelector('#openSelectedRun').addEventListener('click', () => {
  const runId = document.querySelector('#runSelector').value;
  location.href = runId ? `console.html?run=${encodeURIComponent(runId)}` : 'console.html';
});
document.querySelector('#queueFilter').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-filter]');
  if (!button) return;
  queueFilter = button.dataset.filter;
  document.querySelectorAll('#queueFilter button').forEach((item) => item.classList.toggle('active', item === button));
  if (currentState) renderTasks(currentState, computedRecords(currentState));
});
document.querySelector('#clearTerminal').addEventListener('click', () => {
  document.querySelector('#terminal').innerHTML = '<div class="terminal-empty">$ display cleared; stream still connected<span>_</span></div>';
});

// ---------- 分页：实时输出 / 任务看板 ----------
// 看板用 iframe 嵌 index.html 的 embed 模式：拖拽、发车对话框、抽屉都在那边，重写一遍
// 等于把同一套状态做成两份，早晚对不上。iframe 只在第一次切过去时才加载。
function setTab(name) {
  document.querySelectorAll('#opsTabs button').forEach((button) => {
    const active = button.dataset.tab === name;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
  });
  document.querySelectorAll('.ops-page').forEach((page) => { page.hidden = page.dataset.page !== name; });
  if (name === 'board') {
    const frame = document.querySelector('#boardFrame');
    if (frame && (!frame.src || frame.src === 'about:blank')) frame.src = frame.dataset.src;
  }
}

document.querySelector('#opsTabs').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-tab]');
  if (button) setTab(button.dataset.tab);
});
document.querySelector('#boardReload').addEventListener('click', () => {
  const frame = document.querySelector('#boardFrame');
  if (frame) frame.src = frame.dataset.src;
});
// 展开密度：紧凑=一行摘要；详细/全开=新内容自动展开，排查长任务时少点几次。
document.querySelectorAll('[data-density]').forEach((button) => {
  button.classList.toggle('active', button.dataset.density === streamDensity);
});
document.querySelector('.stream-actions')?.addEventListener('click', (event) => {
  const button = event.target.closest('[data-density]');
  if (!button) return;
  localStorage.setItem('tracebench-stream-density', button.dataset.density);
  document.querySelectorAll('[data-density]').forEach((item) => {
    item.classList.toggle('active', item === button);
  });
  for (const [taskId, list] of streamEvents) {
    const body = document.querySelector(`[data-task="${CSS.escape(taskId)}"] .stream-body`);
    if (body) appendStreamColumn(taskId, body, list, true, true);
  }
  toast(`展开密度：${button.textContent.trim()}`);
});

document.querySelector('#clearStream').addEventListener('click', () => {
  const newest = [...streamEvents.values()].map((list) => (list.slice(-1)[0] || {}).seq || 0);
  streamClearedUntil = Math.max(streamClearedUntil, ...newest, 0);
  streamEvents = new Map();
  streamSteps = new Map();
  streamSeq.clear();
  streamOpenGroup.clear();
  streamPendingCall.clear();
  streamDirty = new Set();
  renderStream(currentState || {}, computedRecords(currentState || {}));
});
document.querySelector('#streamReasoning').addEventListener('change', () => {
  streamDirty = new Set(streamEvents.keys());
  renderStream(currentState || {}, computedRecords(currentState || {}));
});

// 页签切走就是没人在看：定时器全部收工，回到前台再立刻补一次同步。
document.addEventListener('visibilitychange', () => {
  clearTimeout(pollTimer);
  clearTimeout(eventTimer);
  pollTimer = null;
  eventTimer = null;
  if (!document.hidden) {
    poll(false);
    pollEvents();
  }
});

// 主题：与 index.html 共用 tracebench-theme；内嵌看板是同源 iframe，直接同步它的 body class，
// 切亮色时不必重载看板，iframe 里 index.html 自己的 #themeButton 也读同一份设置。
(function () {
  const themeButton = document.querySelector('#opsThemeButton');
  function applyTheme(light) {
    document.body.classList.toggle('light', light);
    try { document.querySelector('#boardFrame')?.contentDocument?.body?.classList.toggle('light', light); } catch (e) {}
  }
  if (localStorage.getItem('tracebench-theme') === 'light') applyTheme(true);
  themeButton?.addEventListener('click', () => {
    const light = !document.body.classList.contains('light');
    localStorage.setItem('tracebench-theme', light ? 'light' : 'dark');
    applyTheme(light);
  });
})();

poll(true);
pollEvents();
