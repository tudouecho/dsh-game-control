/**
 * dsh-game-control — Host 半（Cordis 插件）。
 *
 * 职责（细节见 SPEC.md 第 3 节）：
 *  1. 注册 `/api/game-control/*` 同源 HTTP 路由，供浏览器侧栏面板调用；
 *  2. 注册 agent 工具 `game_control`，与面板共用同一套动作；
 *  3. 解析本机 Python，把动作转交给常驻 worker（`game_control.py --serve`）执行，
 *     负责超时、进程退出、错误归一化与清理。
 *
 * 设计约束：
 *  - 只使用 Node 内置模块（本地 link 安装时解析不到宿主内的 @deepseek-ai/* 包）；
 *  - 不联网；所有输入动作只由显式请求触发；插件卸载时杀掉 Python 子进程；
 *  - 工具的 JSON Schema 里每个 object 节点都显式写 additionalProperties。
 */

import { spawn, spawnSync } from 'node:child_process';
import { createInterface } from 'node:readline';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/** Stable Loader identity：必须与 package.json 的 name 一致。 */
export const name = '@local/dsh-game-control';

/**
 * 依赖服务。`webServer` 故意不放进 inject：没有 Web 的 profile 也应能注册工具；
 * 它用 ctx.get('webServer') 按可选服务处理。
 */
export const inject = ['tools'];

const PLUGIN_ROOT = path.dirname(fileURLToPath(import.meta.url));
const PY_DIR = path.join(PLUGIN_ROOT, 'python');
const BACKEND_SCRIPT = path.join(PY_DIR, 'game_control.py');
const ROUTE_PREFIX = '/api/game-control';
const BODY_LIMIT = 256 * 1024;
const STDOUT_LIMIT = 8 * 1024;

const VERSION = (() => {
  try {
    const manifest = JSON.parse(fs.readFileSync(path.join(PLUGIN_ROOT, 'package.json'), 'utf8'));
    return typeof manifest.version === 'string' && manifest.version !== '' ? manifest.version : '0.0.0';
  } catch {
    return '0.0.0-unversioned';
  }
})();

/** 控制动作名与 SPEC 2.3 / 8.2 完全一致，前端、工具、Python 三处共用。 */
const CONTROL_ACTIONS = [
  'status',
  'list_displays',
  'list_windows',
  'capture_window',
  'activate_window',
  'mouse_move',
  'mouse_move_rel',
  'mouse_click',
  'mouse_down',
  'mouse_up',
  'mouse_drag',
  'mouse_scroll',
  'mouse_circle',
  'mouse_position',
  'key_press',
  'key_down',
  'key_up',
  'hotkey',
  'type_text',
  'wait',
  'find_image',
  'wait_image',
  'screenshot',
];

/** 技能库动作（SPEC 8.3.2 / 10.4）：不与鼠标键盘动作混在同一工具里，避免歧义。 */
const SKILL_ACTIONS = [
  'skill_list',
  'skill_get',
  'skill_save',
  'skill_versions',
  'skill_restore',
  'skill_delete',
  'skill_run',
  'skill_progress',
];

/** Python 桥校验用并集；两个 agent 工具各自只暴露自己的子集。 */
const ALL_ACTIONS = [...CONTROL_ACTIONS, ...SKILL_ACTIONS];

/** Host 级循环动作（不经过 Python worker）：每轮独立调用一次 skill_run。 */
const LOOP_ACTIONS = ['loop_start', 'loop_status', 'loop_stop'];

/** `game_skill` 工具暴露的技能 + 循环动作。 */
const SKILL_TOOL_ACTIONS = [...SKILL_ACTIONS, ...LOOP_ACTIONS];

const ACTION_LABELS = {
  status: '读取后端/屏幕状态',
  list_displays: '列出显示器',
  list_windows: '列出本机可见窗口（标题/句柄/进程/尺寸/是否前台）',
  capture_window: '后台抓取指定窗口画面（可被遮挡，WGC 优先）',
  activate_window: '把目标窗口恢复并切到前台',
  mouse_move: '鼠标移动到绝对坐标 x,y',
  mouse_move_rel: '鼠标相对移动 dx,dy',
  mouse_click: '鼠标点击（可给 x,y 先移动）',
  mouse_down: '按下鼠标键',
  mouse_up: '抬起鼠标键',
  mouse_drag: '从 x1,y1 拖拽到 x2,y2',
  mouse_scroll: '滚轮（amount 正=上/左）',
  mouse_circle: '窗口内连续圆形鼠标轨迹（可中止）',
  mouse_position: '读取当前鼠标位置',
  key_press: '按一次键',
  key_down: '按住键',
  key_up: '松开键',
  hotkey: '组合键，如 keys:["ctrl","shift","s"]',
  type_text: '输入文本（支持中文/Unicode）',
  wait: '同步等待指定毫秒（不注入输入）',
  find_image: '屏幕/窗口内模板找图，返回匹配中心',
  wait_image: '轮询等待模板出现或消失',
  screenshot: '截图到文件，可返回 base64',
  skill_list: '列出技能库',
  skill_get: '读取一个技能的完整内容',
  skill_save: '新建/更新技能（自动备份旧版本）',
  skill_versions: '列出技能的历史备份',
  skill_restore: '从历史备份恢复技能',
  skill_delete: '删除技能（默认软删到回收目录）',
  skill_run: '执行技能一轮（底层单轮原语；多轮用 loop_start.rounds）',
  skill_progress: '读取当前技能运行的进度',
  loop_start: '启动长循环（每轮独立调用 skill_run）',
  loop_status: '查询长循环进度与每轮结果',
  loop_stop: '停止长循环（可立即终止当前轮）',
};

/** 动作超时预算：文本输入、长等待、圆形轨迹、技能执行按参数放宽，避免被误杀。 */
function timeoutFor(action, params) {
  switch (action) {
    case 'screenshot':
      return 30_000;
    case 'capture_window': {
      const timeout = Number.isFinite(params?.timeoutMs) ? Math.max(0, params.timeoutMs) : 3_000;
      return Math.min(45_000, 30_000 + timeout);
    }
    case 'type_text': {
      const length = typeof params?.text === 'string' ? params.text.length : 0;
      const interval = Number.isFinite(params?.intervalMs) ? Math.max(0, params.intervalMs) : 12;
      return Math.min(180_000, 15_000 + Math.ceil(length * interval) + 3_000);
    }
    case 'mouse_drag': {
      const duration = Number.isFinite(params?.durationMs) ? Math.max(0, params.durationMs) : 300;
      return Math.min(90_000, 15_000 + duration);
    }
    case 'activate_window': {
      const timeout = Number.isFinite(params?.timeoutMs) ? Math.max(0, params.timeoutMs) : 2_000;
      return Math.min(10_000, timeout + 5_000);
    }
    case 'mouse_circle': {
      const leadIn = Number.isFinite(params?.leadInMs) ? Math.max(0, params.leadInMs) : 300;
      const duration = Number.isFinite(params?.durationMs) ? Math.max(0, params.durationMs) : 30_000;
      return Math.min(180_000, 15_000 + leadIn + duration + 5_000);
    }
    case 'wait': {
      const duration = Number.isFinite(params?.durationMs) ? Math.max(0, params.durationMs) : 0;
      return Math.min(620_000, duration + 5_000);
    }
    case 'find_image':
      return 30_000;
    case 'wait_image': {
      const timeout = Number.isFinite(params?.timeoutMs) ? Math.max(0, params.timeoutMs) : 15_000;
      return Math.min(620_000, timeout + 5_000);
    }
    case 'skill_run': {
      const timeout = Number.isFinite(params?.timeoutMs) ? Math.max(0, params.timeoutMs) : 300_000;
      // v0.6：给 Python 内部总 deadline 与单步收尾留出足够余量，避免 Host 先于 Python 强杀 worker。
      return Math.min(620_000, timeout + 120_000);
    }
    case 'loop_start':
    case 'loop_status':
      return 15_000;
    case 'loop_stop':
      return 30_000;
    case 'skill_list':
    case 'skill_get':
    case 'skill_save':
    case 'skill_versions':
    case 'skill_restore':
    case 'skill_delete':
      return 15_000;
    default:
      return 15_000;
  }
}

/** 单行 JSON 安全化：拒绝 undefined / 函数 / BigInt 混进工具返回值。 */
function jsonSafe(value) {
  return JSON.parse(JSON.stringify(value ?? null));
}

function errorEnvelope(action, code, message, hint) {
  return {
    ok: false,
    action,
    durationMs: 0,
    error: { code, message, ...(hint === undefined ? {} : { hint }) },
  };
}

/** 数据目录与 Python 后端保持一致（GAME_CONTROL_HOME 优先，其次 DSH_HOME）。 */
function gameControlHome() {
  const explicit = process.env.GAME_CONTROL_HOME;
  if (typeof explicit === 'string' && explicit.trim() !== '') return explicit.trim();
  const dshHome = process.env.DSH_HOME || path.join(os.homedir(), '.dsh');
  return path.join(dshHome, 'game-control');
}

/* ------------------------------------------------------------------ */
/* Python 解析                                                         */
/* ------------------------------------------------------------------ */

function listDshRuntimePythons() {
  const home = process.env.DSH_HOME || path.join(os.homedir(), '.dsh');
  const runtimes = path.join(home, 'dsh-runtimes');
  const found = [];
  try {
    for (const entry of fs.readdirSync(runtimes, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      if (process.platform === 'win32') {
        found.push(path.join(runtimes, entry.name, 'dependencies', 'python', 'python.exe'));
      } else {
        found.push(path.join(runtimes, entry.name, 'dependencies', 'python', 'bin', 'python3'));
      }
    }
  } catch {
    /* 没有 DSH 运行时目录是正常情况 */
  }
  return found;
}

/** 候选 Python（按优先级）。返回 {cmd, args} 列表。 */
function pythonCandidates() {
  const candidates = [];
  const push = (cmd, args = []) => {
    if (typeof cmd === 'string' && cmd.trim() !== '') candidates.push({ cmd, args });
  };

  push(process.env.DSH_GAME_CONTROL_PYTHON);
  push(process.env.GAME_CONTROL_PYTHON);

  const venv =
    process.platform === 'win32'
      ? path.join(PY_DIR, '.venv', 'Scripts', 'python.exe')
      : path.join(PY_DIR, '.venv', 'bin', 'python3');
  push(venv);

  for (const runtimePython of listDshRuntimePythons()) push(runtimePython);

  if (process.platform === 'win32') {
    push('python');
    push('python3');
    push('py', ['-3']);
  } else {
    push('python3');
    push('python');
  }
  return candidates;
}

function probePython(candidate) {
  try {
    const result = spawnSync(candidate.cmd, [...candidate.args, '--version'], {
      timeout: 5000,
      windowsHide: true,
      encoding: 'utf8',
    });
    if (result.status !== 0) return false;
    const text = `${result.stdout || ''}${result.stderr || ''}`;
    return /python\s+\d+/i.test(text);
  } catch {
    return false;
  }
}

/* ------------------------------------------------------------------ */
/* Python 常驻 worker 桥                                               */
/* ------------------------------------------------------------------ */

class PythonBridge {
  constructor() {
    /** @type {import('node:child_process').ChildProcessWithoutNullStreams | null} */
    this.child = null;
    this.ready = false;
    this.starting = null;
    this.python = null;
    this.pythonResolved = false;
    this.lastError = '';
    this.stderrTail = '';
    this.seq = 0;
    /** @type {Map<number, {resolve: (v: unknown) => void, timer: NodeJS.Timeout}>} */
    this.pending = new Map();
    this.disposed = false;
  }

  info() {
    return {
      version: VERSION,
      ready: this.ready,
      running: this.child !== null && this.child.exitCode === null,
      pythonPath: this.python ? this.python.cmd : null,
      backendScript: BACKEND_SCRIPT,
      lastError: this.lastError || null,
      stderrTail: this.stderrTail || null,
      home: gameControlHome(),
    };
  }

  /** 逐个候选探测，返回第一个真正可用的 Python。 */
  resolvePython() {
    if (this.pythonResolved) return this.python;
    this.pythonResolved = true;
    for (const candidate of pythonCandidates()) {
      if (probePython(candidate)) {
        this.python = candidate;
        return candidate;
      }
    }
    this.python = null;
    return null;
  }

  async ensureStarted(startTimeoutMs = 25_000) {
    if (this.disposed) throw new Error('game-control: bridge disposed');
    if (this.ready && this.child !== null && this.child.exitCode === null) return;
    if (this.starting !== null) return this.starting;
    this.starting = this.#start(startTimeoutMs).finally(() => {
      this.starting = null;
    });
    return this.starting;
  }

  async #start(startTimeoutMs) {
    const python = this.resolvePython();
    if (python === null) {
      this.lastError = 'PYTHON_NOT_FOUND';
      throw Object.assign(
        new Error('找不到可用的 Python。请设置环境变量 DSH_GAME_CONTROL_PYTHON 指向 python.exe，或安装 Python 3.10+。'),
        { code: 'PYTHON_NOT_FOUND' },
      );
    }

    this.lastError = '';
    this.stderrTail = '';
    const child = spawn(python.cmd, [...python.args, BACKEND_SCRIPT, '--serve'], {
      cwd: PY_DIR,
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' },
    });
    this.child = child;

    const readyPromise = new Promise((resolve, reject) => {
      let settled = false;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        reject(Object.assign(new Error(`Python 后端启动超时（${startTimeoutMs}ms）`), { code: 'BACKEND_UNAVAILABLE' }));
      }, startTimeoutMs);

      const settleOk = (info) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        this.ready = true;
        this.lastError = '';
        resolve(info);
      };
      const settleErr = (error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        reject(error);
      };

      const lines = createInterface({ input: child.stdout });
      lines.on('line', (line) => {
        const text = String(line).trim();
        if (text === '') return;
        let message;
        try {
          message = JSON.parse(text);
        } catch {
          this.#appendStderr(`非法 JSON 行：${text.slice(0, 200)}`);
          return;
        }
        if (message && message.event === 'ready') {
          settleOk(message);
          return;
        }
        this.#onResponse(message);
      });

      child.stderr.on('data', (chunk) => this.#appendStderr(String(chunk)));
      child.on('error', (error) => {
        this.ready = false;
        this.lastError = `spawn 失败：${error.message}`;
        settleErr(Object.assign(new Error(this.lastError), { code: 'BACKEND_UNAVAILABLE' }));
      });
      child.on('exit', (code, signal) => {
        const wasReady = this.ready;
        this.ready = false;
        this.child = null;
        this.lastError = `Python 后端退出（code=${code ?? 'null'}, signal=${signal ?? 'null'}）${this.stderrTail ? `：${this.stderrTail.slice(-400)}` : ''}`;
        markSkillProgressStale(this.lastError);
        if (!wasReady) {
          settleErr(Object.assign(new Error(this.lastError), { code: 'BACKEND_UNAVAILABLE' }));
        }
        this.#rejectAll('BACKEND_UNAVAILABLE', this.lastError);
      });
    });

    try {
      await readyPromise;
    } catch (error) {
      this.#kill();
      throw error;
    }
  }

  #appendStderr(text) {
    this.stderrTail = (this.stderrTail + text).slice(-STDOUT_LIMIT);
  }

  #onResponse(message) {
    if (!message || typeof message !== 'object') return;
    const id = message.id;
    if (!Number.isInteger(id)) return;
    const record = this.pending.get(id);
    if (record === undefined) return;
    this.pending.delete(id);
    clearTimeout(record.timer);
    record.resolve(message);
  }

  #rejectAll(code, message) {
    for (const [id, record] of this.pending) {
      this.pending.delete(id);
      clearTimeout(record.timer);
      record.resolve(errorEnvelope('unknown', code, message));
    }
  }

  #kill() {
    const child = this.child;
    this.child = null;
    this.ready = false;
    if (child === null) return;
    try {
      child.stdin.end();
    } catch {
      /* ignore */
    }
    try {
      child.kill();
    } catch {
      /* ignore */
    }
  }

  /** 执行一个动作，始终返回信封（不抛异常，路由与工具共用）。 */
  async execute(action, params, timeoutOverride) {
    if (!ALL_ACTIONS.includes(action)) {
      return errorEnvelope(action, 'UNKNOWN_ACTION', `未知动作：${String(action)}`);
    }
    if (this.disposed) {
      return errorEnvelope(action, 'BACKEND_UNAVAILABLE', '插件已卸载');
    }
    try {
      await this.ensureStarted();
    } catch (error) {
      const code = error && error.code ? error.code : 'BACKEND_UNAVAILABLE';
      return errorEnvelope(action, code, String(error && error.message ? error.message : error), pythonHint());
    }

    const id = ++this.seq;
    const timeoutMs = timeoutOverride ?? timeoutFor(action, params);
    const payload = JSON.stringify({ id, action, params: params ?? {} }) + '\n';

    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        this.lastError = `动作 ${action} 超时（${timeoutMs}ms）`;
        this.#kill();
        resolve(
          errorEnvelope(action, 'TIMEOUT', `动作 ${action} 超时（${timeoutMs}ms）`, '后端已重启，请重试；长文本可降低 intervalMs'),
        );
      }, timeoutMs);

      this.pending.set(id, { resolve, timer });
      const child = this.child;
      if (child === null || !child.stdin.writable) {
        this.pending.delete(id);
        clearTimeout(timer);
        resolve(errorEnvelope(action, 'BACKEND_UNAVAILABLE', 'Python 后端未运行', pythonHint()));
        return;
      }
      child.stdin.write(payload, (error) => {
        if (error === undefined || error === null) return;
        this.pending.delete(id);
        clearTimeout(timer);
        this.lastError = `写入 Python 失败：${error.message}`;
        this.#kill();
        resolve(errorEnvelope(action, 'BACKEND_UNAVAILABLE', this.lastError, pythonHint()));
      });
    });
  }

  /** 立即终止当前 worker（loop_stop immediate / 外部强制停止用）。 */
  abortActive(reason = '用户立即停止') {
    this.lastError = String(reason);
    this.#rejectAll('BACKEND_UNAVAILABLE', this.lastError);
    this.#kill();
    markSkillProgressStale(this.lastError);
  }

  dispose() {
    this.disposed = true;
    this.#rejectAll('BACKEND_UNAVAILABLE', '插件已卸载');
    this.#kill();
  }
}

function pythonHint() {
  return [
    '设置 DSH_GAME_CONTROL_PYTHON=<python.exe 绝对路径>，',
    '或安装 Python 3.10+ 并在插件 python 目录执行 pip install -r requirements.txt。',
  ].join('');
}

/* ------------------------------------------------------------------ */
/* HTTP 工具函数                                                       */
/* ------------------------------------------------------------------ */

function sendJson(res, status, value) {
  const body = `${JSON.stringify(value)}\n`;
  try {
    res.writeHead(status, {
      'content-type': 'application/json; charset=utf-8',
      'content-length': Buffer.byteLength(body),
      'cache-control': 'no-store',
    });
  } catch {
    /* headers 可能已发送 */
  }
  res.end(body);
}

function readBody(req, limit = BODY_LIMIT) {
  return new Promise((resolve, reject) => {
    let size = 0;
    /** @type {Buffer[]} */
    const chunks = [];
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > limit) {
        reject(Object.assign(new Error(`请求体超过 ${limit} 字节`), { code: 'INVALID_ARGUMENT' }));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

function tailLog(lines) {
  const logFile = path.join(gameControlHome(), 'logs', 'game-control.log');
  try {
    const stat = fs.statSync(logFile);
    const start = Math.max(0, stat.size - 64 * 1024);
    const buffer = Buffer.alloc(stat.size - start);
    const fd = fs.openSync(logFile, 'r');
    try {
      fs.readSync(fd, buffer, 0, buffer.length, start);
    } finally {
      fs.closeSync(fd);
    }
    return buffer
      .toString('utf8')
      .split(/\r?\n/)
      .filter((line) => line.trim() !== '')
      .slice(-Math.min(Math.max(lines, 1), 500))
      .join('\n');
  } catch {
    return '';
  }
}

/** 技能运行进度文件（Python worker 写，Host 直接读，运行期间 worker 忙也能响应）。 */
function skillProgressFile() {
  return path.join(gameControlHome(), 'logs', 'skill-progress.json');
}

function ensureGameControlLogs() {
  try {
    fs.mkdirSync(path.join(gameControlHome(), 'logs'), { recursive: true });
  } catch {
    /* 只读/权限问题由调用方的返回消化 */
  }
}

/** 原子写 JSON 文件（tmp + rename）；失败返回 false，不抛。 */
function writeJsonAtomic(file, value) {
  ensureGameControlLogs();
  try {
    const tmp = `${file}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(value));
    fs.renameSync(tmp, file);
    return true;
  } catch {
    return false;
  }
}

function readSkillProgress() {
  try {
    const value = JSON.parse(fs.readFileSync(skillProgressFile(), 'utf8'));
    return value !== null && typeof value === 'object' ? value : null;
  } catch {
    return null;
  }
}

function writeSkillProgress(value) {
  return writeJsonAtomic(skillProgressFile(), value);
}

/** worker 退出/被终止时把进度标记为 stale，避免面板永久显示“运行中”。 */
function markSkillProgressStale(reason) {
  const current = readSkillProgress();
  if (current === null || current.running !== true) return current;
  const next = {
    ...current,
    running: false,
    stale: true,
    abortReason: current.abortReason ?? 'backend-stopped',
    finishedAt: new Date().toISOString(),
    lastError: String(reason ?? 'Python 后端已停止'),
  };
  writeSkillProgress(next);
  return next;
}

/* ------------------------------------------------------------------ */
/* 长循环控制器（Host 侧；每轮独立调用一次 skill_run）                 */
/* ------------------------------------------------------------------ */

function loopStateFile() {
  return path.join(gameControlHome(), 'logs', 'loop-state.json');
}

function loopJournalFile() {
  return path.join(gameControlHome(), 'logs', 'loop.jsonl');
}

function appendLoopJournal(entry) {
  ensureGameControlLogs();
  try {
    fs.appendFileSync(loopJournalFile(), `${JSON.stringify(entry)}\n`);
  } catch {
    /* ignore */
  }
}

function codedError(code, message, hint) {
  const error = new Error(message);
  error.code = code;
  if (hint !== undefined) error.hint = hint;
  return error;
}

function loopNumber(value, name, minimum, maximum, fallback) {
  if (value === undefined || value === null) return fallback;
  const number = Number(value);
  if (!Number.isFinite(number)) throw codedError('INVALID_ARGUMENT', `参数 ${name} 必须是数字`);
  const int = Math.round(number);
  if (int < minimum || int > maximum) {
    throw codedError('INVALID_ARGUMENT', `参数 ${name} 必须在 ${minimum}-${maximum}（收到 ${int}）`);
  }
  return int;
}

class LoopRunner {
  constructor(bridge) {
    this.bridge = bridge;
    /** @type {Record<string, unknown> | null} */
    this.state = null;
    this.sleep = null;
    this.seq = 0;
    this.disposed = false;
  }

  status() {
    if (this.state === null) return { running: false };
    const state = jsonSafe(this.state);
    // 运行中把 Python 进度里的“当前步骤”合并进 loop_status，面板可显示当前步骤。
    if (state.running === true) {
      const progress = readSkillProgress();
      if (progress !== null && progress.running === true && progress.skillId === state.skillId) {
        const step = progress.step !== null && typeof progress.step === 'object' ? progress.step : null;
        if (step !== null) {
          state.currentStep = {
            index: step.index ?? null,
            total: step.total ?? null,
            action: step.action ?? null,
            elapsedMs: step.elapsedMs ?? null,
          };
        }
      }
    }
    return state;
  }

  async handle(action, params) {
    if (action === 'loop_start') return this.start(params);
    if (action === 'loop_status') return this.status();
    if (action === 'loop_stop') return this.stop(params);
    throw codedError('UNKNOWN_ACTION', `未知循环动作：${action}`);
  }

  _persist() {
    if (this.state !== null) writeJsonAtomic(loopStateFile(), this.state);
  }

  async start(rawParams) {
    if (this.disposed) throw codedError('BACKEND_UNAVAILABLE', '插件已卸载');
    if (this.state !== null && this.state.running === true) {
      throw codedError('LOOP_ALREADY_RUNNING', `已有循环在运行（jobId=${String(this.state.jobId)}），请先 loop_stop`);
    }
    const params = rawParams ?? {};
    const skillId = typeof params.skillId === 'string' ? params.skillId.trim() : '';
    if (skillId === '') throw codedError('INVALID_ARGUMENT', 'loop_start 需要 skillId');
    const rounds = loopNumber(params.rounds, 'rounds', 1, 10000, undefined);
    if (rounds === undefined) throw codedError('INVALID_ARGUMENT', 'loop_start 需要 rounds（1-10000）');
    const intervalMs = loopNumber(params.intervalMs, 'intervalMs', 0, 60_000, 500);
    const stopOnError = params.stopOnError === undefined ? true : Boolean(params.stopOnError);
    const abortKey =
      typeof params.abortKey === 'string' && params.abortKey.trim() !== '' ? params.abortKey.trim() : 'esc';

    // 先 dryRun 校验技能与耗时估算（失败直接报错，不启动循环）。
    const dry = await this.bridge.execute('skill_run', { id: skillId, dryRun: true });
    if (dry.ok !== true) {
      const error = dry.error ?? {};
      throw codedError(
        error.code ?? 'BACKEND_UNAVAILABLE',
        `循环前置校验失败：${error.message ?? 'skill_run dryRun 未通过'}`,
        error.hint,
      );
    }
    const estimated = Number(dry.result?.estimatedDurationMs) || 60_000;
    const perRoundTimeoutMs = loopNumber(
      params.roundTimeoutMs,
      'roundTimeoutMs',
      1_000,
      600_000,
      Math.min(600_000, Math.max(1_000, estimated + 30_000)),
    );

    this.seq += 1;
    const state = {
      running: true,
      jobId: `loop-${Date.now()}-${this.seq}`,
      skillId,
      rounds,
      intervalMs,
      stopOnError,
      abortKey,
      perRoundTimeoutMs,
      estimatedDurationMs: estimated,
      completedRounds: 0,
      failedRounds: 0,
      currentRound: 0,
      stopRequested: false,
      startedAt: new Date().toISOString(),
      results: [],
    };
    this.state = state;
    this._persist();
    this._runLoop().catch((error) => {
      if (this.state === null) return;
      this.state.running = false;
      this.state.finishedAt = new Date().toISOString();
      this.state.lastError = String(error?.message ?? error);
      this._persist();
    });
    return this.status();
  }

  async _runLoop() {
    const state = this.state;
    if (state === null) return;
    const hostPerRoundTimeout = Math.min(620_000, Number(state.perRoundTimeoutMs) + 120_000);
    while (this.state !== null && this.state.running === true && this.state.stopRequested !== true) {
      if (this.state.currentRound >= this.state.rounds) break;
      this.state.currentRound += 1;
      const round = this.state.currentRound;
      const startedAt = Date.now();
      this._persist();
      let envelope;
      try {
        envelope = await this.bridge.execute(
          'skill_run',
          {
            id: this.state.skillId,
            repeat: 1,
            timeoutMs: this.state.perRoundTimeoutMs,
            abortKey: this.state.abortKey,
            stopOnError: this.state.stopOnError,
          },
          hostPerRoundTimeout,
        );
      } catch (error) {
        envelope = errorEnvelope('skill_run', 'INTERNAL', String(error?.message ?? error));
      }
      const durationMs = Date.now() - startedAt;
      /** @type {Record<string, unknown>} */
      let entry;
      if (envelope.ok === true) {
        const result = envelope.result ?? {};
        const success = result.success === true && result.aborted !== true;
        entry = {
          round,
          ok: true,
          success,
          aborted: Boolean(result.aborted),
          abortReason: result.abortReason ?? null,
          durationMs: typeof result.durationMs === 'number' ? result.durationMs : durationMs,
        };
        if (result.failedStep !== undefined && result.failedStep !== null) {
          entry.failedStep = result.failedStep;
        }
        if (success) {
          this.state.completedRounds = Number(this.state.completedRounds) + 1;
        } else {
          this.state.failedRounds = Number(this.state.failedRounds) + 1;
          this.state.lastError =
            result.failedStep !== undefined && result.failedStep !== null
              ? `第 ${Number(result.failedStep.index) + 1} 步 ${result.failedStep.action}: [${result.failedStep.code}] ${result.failedStep.message}${result.failedStep.screenshotPath ? `（失败截图：${result.failedStep.screenshotPath}）` : ''}`
              : result.abortReason
                ? `中止：${result.abortReason}`
                : '执行失败';
        }
        if (result.aborted === true && this.state.stopOnError !== false) this.state.stopRequested = true;
        if (!success && this.state.stopOnError !== false) this.state.stopRequested = true;
      } else {
        const error = envelope.error ?? {};
        entry = {
          round,
          ok: false,
          success: false,
          aborted: false,
          durationMs,
          errorCode: error.code ?? 'BACKEND_ERROR',
          message: error.message ?? '执行失败',
        };
        this.state.failedRounds = Number(this.state.failedRounds) + 1;
        this.state.lastError = `[${String(entry.errorCode)}] ${String(entry.message)}`;
        if (this.state.stopOnError !== false) this.state.stopRequested = true;
      }
      const results = Array.isArray(this.state.results) ? this.state.results : [];
      results.push(entry);
      this.state.results = results.slice(-20);
      appendLoopJournal({ ts: new Date().toISOString(), jobId: this.state.jobId, ...entry });
      this._persist();
      if (this.state.stopRequested === true || this.state.currentRound >= this.state.rounds) break;
      if (Number(this.state.intervalMs) > 0) await this._sleep(Number(this.state.intervalMs));
    }
    if (this.state !== null) {
      this.state.running = false;
      this.state.finishedAt = new Date().toISOString();
      this._persist();
    }
  }

  _sleep(ms) {
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.sleep = null;
        resolve();
      }, ms);
      this.sleep = { timer, resolve };
    });
  }

  async stop(rawParams) {
    if (this.state === null || this.state.running !== true) {
      throw codedError('INVALID_ARGUMENT', '当前没有运行中的循环');
    }
    const params = rawParams ?? {};
    this.state.stopRequested = true;
    this.state.stopRequestedAt = new Date().toISOString();
    const immediate = Boolean(params.immediate);
    if (immediate) {
      this.state.immediateStopped = true;
      this.bridge.abortActive('循环被用户立即停止');
    }
    this._persist();
    return this.status();
  }

  dispose() {
    this.disposed = true;
    if (this.sleep !== null) {
      clearTimeout(this.sleep.timer);
      this.sleep.resolve();
      this.sleep = null;
    }
    if (this.state !== null && this.state.running === true) {
      this.state.running = false;
      this.state.stopRequested = true;
      this.state.finishedAt = new Date().toISOString();
      this.state.lastError = '插件已卸载，循环停止';
      this._persist();
    }
  }
}

/* ------------------------------------------------------------------ */
/* agent 工具                                                          */
/* ------------------------------------------------------------------ */

function toolDefinition(bridge) {
  return {
    name: 'game_control',
    description: [
      '控制本机鼠标/键盘、等待、模板找图并截图（游戏辅助）。所有动作都由本次显式调用触发，只影响当前聚焦窗口。',
      `action 可选：${CONTROL_ACTIONS.join(', ')}。`,
      '鼠标坐标是物理像素（主屏左上为原点，多屏可为负），可先 action=status 读取屏幕几何；list_windows 返回每个窗口的 rect/clientRect。',
      '按键名：a-z、0-9、enter/esc/tab/space/backspace/delete/home/end/pageup/pagedown/方向键、f1-f24、',
      'ctrl/alt/shift/win 等；hotkey 用 keys 数组（如 ["ctrl","shift","s"]）。',
      '截图默认保存到本机截图目录并返回绝对路径；需要直接看图时先截图再用 read_image 工具读取该路径。',
      '后台看游戏窗口：list_windows（titleContains 过滤）拿 hwnd → capture_window（WGC 可在非前台/被遮挡时抓）；',
      '要让键鼠真正进游戏前必须先 activate_window（把游戏切前台），否则输入会打到别的窗口。',
      '【游戏自动化推荐流程】list_windows → activate_window → find_image(或 wait_image) 定位按钮 → mouse_click 其 center →',
      'wait(durationMs) → mouse_circle(窗口内画圆，hwnd 传游戏句柄) → wait_image(等待完成面板) → 再次 find_image/mouse_click 进入下一轮。',
      'mouse_circle 会阻塞 durationMs（默认 30s），执行中按 Esc 可立即中止（button 会自动释放）；',
      'wait_image 超时返回 ok:true,found:false,timedOut:true，不是错误；find_image/wait_image 支持 templates 多模板（任一出现即命中），等待阶段也可按 Esc 中止。',
      '动作失败会抛错，错误消息以 [错误码] 开头（如 [INVALID_KEY]、[WINDOW_NOT_FOUND]、[WINDOW_ACTIVATION_FAILED]、[TIMEOUT]）。',
    ].join('\n'),
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        action: { type: 'string', enum: CONTROL_ACTIONS, description: '动作名，见工具描述。' },
        x: { type: 'number', description: '绝对 X（物理像素）。' },
        y: { type: 'number', description: '绝对 Y（物理像素）。' },
        x1: { type: 'number', description: '拖拽起点 X。' },
        y1: { type: 'number', description: '拖拽起点 Y。' },
        x2: { type: 'number', description: '拖拽终点 X。' },
        y2: { type: 'number', description: '拖拽终点 Y。' },
        dx: { type: 'number', description: '相对移动 X 像素。' },
        dy: { type: 'number', description: '相对移动 Y 像素。' },
        button: {
          type: 'string',
          enum: ['left', 'right', 'middle', 'x1', 'x2', 'none'],
          description: '鼠标键。点击/拖拽默认 left；mouse_circle 默认 none（画圆期间可按住）。',
        },
        clicks: { type: 'number', description: '点击次数 1-10，默认 1。' },
        amount: { type: 'number', description: '滚轮格数 -1000..1000，正=上/左。' },
        horizontal: { type: 'boolean', description: '滚轮是否水平。' },
        key: { type: 'string', description: '单个键名。' },
        keys: {
          type: 'array',
          items: { type: 'string' },
          description: '组合键键名数组，1-4 个。',
        },
        presses: { type: 'number', description: '连按次数 1-20，默认 1。' },
        text: { type: 'string', description: '要输入的文本（≤10000 字符）。' },
        intervalMs: { type: 'number', description: '重复/逐字/轮询间隔毫秒（type_text/wait_image 等）。' },
        durationMs: {
          type: 'number',
          description: 'mouse_circle 为总时长（默认 30000）；wait 为等待毫秒；mouse_move/mouse_drag 为动画时长。',
        },
        steps: { type: 'number', description: 'mouse_drag 插值步数 2-200。' },
        region: {
          type: 'object',
          additionalProperties: false,
          description: '区域截图范围（物理像素，虚拟桌面坐标）。',
          properties: {
            x: { type: 'number' },
            y: { type: 'number' },
            width: { type: 'number' },
            height: { type: 'number' },
          },
          required: ['x', 'y', 'width', 'height'],
        },
        monitor: { type: 'number', description: '显示器序号（从 1 开始，与 list_displays 返回的 index 一致），可选。' },
        allScreens: { type: 'boolean', description: '截取全部屏幕组成的虚拟桌面。' },
        titleContains: { type: 'string', description: 'list_windows：按标题子串过滤（大小写不敏感）。' },
        includeInvisible: { type: 'boolean', description: 'list_windows：是否包含不可见窗口，默认 false。' },
        window: { type: 'string', description: 'capture_window / activate_window / find_image / wait_image 的目标窗口标题子串；与 hwnd 二选一。' },
        hwnd: { type: 'number', description: '窗口句柄（list_windows 返回）。capture_window/activate_window 与 window 二选一；mouse_circle 传它做前台校验。' },
        restore: { type: 'boolean', description: 'activate_window：最小化时先恢复窗口，默认 true。' },
        engine: {
          type: 'string',
          enum: ['auto', 'wgc', 'printwindow', 'screen'],
          description: 'capture_window 抓取方式：auto（默认，WGC→PrintWindow→screen 回退）/wgc（后台遮挡可抓）/printwindow/screen。',
        },
        includeCursor: { type: 'boolean', description: 'capture_window：是否包含鼠标指针，默认 false。' },
        centerX: { type: 'number', description: 'mouse_circle 圆心 X（物理像素）。' },
        centerY: { type: 'number', description: 'mouse_circle 圆心 Y（物理像素）。' },
        radius: { type: 'number', description: 'mouse_circle 半径（像素，1-5000）。' },
        periodMs: { type: 'number', description: 'mouse_circle 一圈耗时毫秒（300-20000，默认 2500）。' },
        direction: { type: 'string', enum: ['cw', 'ccw'], description: 'mouse_circle 方向：cw 顺时针（默认）/ccw 逆时针。' },
        startAngleDeg: { type: 'number', description: 'mouse_circle 起始角，默认 -90（正上方）；0=右，90=下。' },
        samplesPerSecond: { type: 'number', description: 'mouse_circle 每秒注入次数（30-240，默认 120）。' },
        leadInMs: { type: 'number', description: 'mouse_circle 开始画圆前从当前指针平滑到起点的时长（0-2000，默认 300）。' },
        abortKey: { type: 'string', description: '中止键名（默认 esc），mouse_circle/find_image/wait_image/skill_run 执行中按下即停止；"none" 禁用。' },
        template: { type: 'string', description: 'find_image / wait_image 模板路径：绝对路径或相对 GAME_CONTROL_HOME/templates/。与 templates 二选一。' },
        templates: {
          type: 'array',
          items: { type: 'string' },
          description: 'find_image / wait_image 多模板（1-8 个）：任一出现即命中；兼容不同样式的按钮。与 template 二选一。',
        },
        threshold: { type: 'number', description: 'find_image / wait_image 匹配阈值 0-1，默认 0.85。' },
        scales: {
          type: 'array',
          items: { type: 'number' },
          description: 'find_image 多尺度比例数组（1-5 个，默认 [1.0]）。',
        },
        grayscale: { type: 'boolean', description: 'find_image 是否灰度匹配，默认 true。' },
        maxResults: { type: 'number', description: 'find_image 最多返回匹配数（1-20，默认 3）。' },
        debugPath: { type: 'string', description: 'find_image 可选：把搜索区域和匹配框画出来另存，便于排障。' },
        mode: { type: 'string', enum: ['appear', 'disappear'], description: 'wait_image：等模板出现（默认）或消失。' },
        dryRun: { type: 'boolean', description: 'mouse_circle：只计算轨迹与参数，不注入任何输入（测试用）。' },
        timeoutMs: { type: 'number', description: '超时毫秒：capture_window 等首帧 500-10000；activate_window 100-5000；wait_image 100-600000。' },
        path: { type: 'string', description: '保存路径（绝对，或相对截图目录）；默认自动命名。' },
        format: { type: 'string', enum: ['png', 'jpeg'], description: '截图格式，默认 png。' },
        quality: { type: 'number', description: 'JPEG 质量 1-100。' },
        base64: { type: 'boolean', description: '是否在返回里附带 base64（面板预览用）。' },
      },
      required: ['action'],
    },
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    async execute(args) {
      const action = typeof args?.action === 'string' ? args.action : '';
      /** @type {Record<string, unknown>} */
      const params = {};
      for (const [key, value] of Object.entries(args ?? {})) {
        if (key === 'action' || value === undefined || value === null) continue;
        params[key] = value;
      }
      // 工具结果会进入模型上下文：截图/窗口抓取不附带 base64（面板预览才需要），
      // 模型应拿着 absolutePath 调用 read_image 看图。
      if (action === 'screenshot' || action === 'capture_window') delete params.base64;
      const envelope = await bridge.execute(action, params);
      if (envelope.ok !== true) {
        const error = envelope.error ?? {};
        const message = `[${error.code ?? 'BACKEND_ERROR'}] ${error.message ?? '执行失败'}${error.hint ? `（${error.hint}）` : ''}`;
        throw new Error(message);
      }
      return jsonSafe({
        ok: true,
        action,
        result: envelope.result ?? null,
        durationMs: envelope.durationMs ?? null,
      });
    },
  };
}

/** `game_skill`：插件内技能库（查看/编辑/备份/恢复/复用执行）。 */
function skillToolDefinition(bridge, loopRunner) {
  return {
    name: 'game_skill',
    description: [
      '管理并执行插件「技能库」里已适配好的游戏流程（skill）。技能 = 可读的 Markdown 说明 + 步骤数组（步骤是 game_control 动作）。',
      '技能文件是 JSONC（支持 // 中文注释）：保存时插件会补全每步完整参数（waitMs/action/params 显式化）',
      '并按步骤生成中文注释；手工注释会被重新生成，自定义备注请写在步骤的 note 字段里。',
      'skill_get 返回 {skill, content, stepsView}：content 是完整文件文本（含注释），可直接修改后用 skill_save 的 content 参数保存。',
      '保存/恢复/删除都会自动备份旧版本到 skills/<id>/backups/，可随时回滚；默认删除是软删除（移到 skills/.trash/）。',
      `action 可选：${SKILL_ACTIONS.join(', ')}。`,
      '典型用法：skill_list 看有哪些技能 → skill_get 读完整内容（含 content 全文）→ 修改 content 后用 skill_save 保存 → skill_run 传 id 执行（dryRun:true 只校验不执行）。',
      '【统一轮数】只有「轮数 rounds」一个概念：rounds=1 即单次运行，rounds>1 即循环；多轮统一用 loop_start（每轮独立调用一次 skill_run），',
      'skill_run 仅保留为单轮底层原语（repeat 参数已 deprecated，仅 CLI/内部兼容，多轮请用 loop_start.rounds）。',
      'skill_run 的 timeoutMs 为单轮运行上限（会先做执行时长估算校验）；运行中按 abortKey（默认 Esc）中止。',
      'find_image / wait_image 的等待阶段也支持 Esc 中止；模板可用 templates 数组匹配多种样式。',
      '步骤里 `$window.*` 引用最近一次 activate_window/list_windows 的窗口（含 clientRect/clientCenterX/quarterWidth），',
      '`$saveAs.*` 引用前面步骤保存的返回结果（如 `$start.center.x`）；执行结果按步骤返回成功/失败/错误码。',
      '长循环：loop_start {skillId, rounds(1-10000), intervalMs?, stopOnError?, abortKey?, roundTimeoutMs?} 会在 Host 后台**每轮独立调用一次 skill_run**，',
      '避免单次长请求超时；用 loop_status 查进度与每轮结果，loop_stop 停止（immediate:true 立即终止当前轮）。',
    ].join('\n'),
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        action: { type: 'string', enum: SKILL_TOOL_ACTIONS, description: '技能库动作（skill_*）或长循环动作（loop_*）。' },
        id: { type: 'string', description: '技能 id（skill_get/versions/restore/delete/run 必填；skill_save 传它表示更新）。' },
        title: { type: 'string', description: 'skill_save：标题（1-200 字符）。' },
        description: { type: 'string', description: 'skill_save：说明（≤2000 字符）。' },
        game: { type: 'string', description: 'skill_save：适用游戏名（≤100 字符）。' },
        tags: {
          type: 'array',
          items: { type: 'string' },
          description: 'skill_save：标签数组（≤20 个）。',
        },
        markdown: { type: 'string', description: 'skill_save：给人看的 Markdown 说明（≤200000 字符）。' },
        content: { type: 'string', description: 'skill_save：整份技能文件 JSONC 文本（含 // 中文注释，≤1000000 字符）。传了它就以它为准，忽略 title/description/game/tags/markdown/steps 字段。' },
        steps: {
          type: 'array',
          description: 'skill_save：步骤数组。每步 {action, params, waitMs?, continueOnError?, saveAs?, note?}。',
          items: { type: 'object', additionalProperties: true },
        },
        backup: { type: 'string', description: 'skill_restore：备份文件名，或 "latest" 表示最近一份。' },
        hard: { type: 'boolean', description: 'skill_delete：true 时彻底删除（默认 false，软删到 .trash/）。' },
        dryRun: { type: 'boolean', description: 'skill_run：只做静态校验与变量规划，不执行任何动作、不等待。' },
        timeoutMs: { type: 'number', description: 'skill_run：整次运行上限毫秒（1000-600000，默认 300000）。' },
        abortKey: { type: 'string', description: 'skill_run：中止键名（默认 esc），运行中按下即停止；"none" 禁用。' },
        stopOnError: { type: 'boolean', description: 'skill_run / loop_start：某步/某轮失败是否停止（默认 true）。' },
        skillId: { type: 'string', description: 'loop_start：要循环执行的技能 id。' },
        rounds: { type: 'number', description: 'loop_start：总执行轮数 1-10000（1=单次运行，>1=循环；每轮独立调用一次 skill_run）。' },
        intervalMs: { type: 'number', description: 'loop_start：轮与轮之间的间隔毫秒（0-60000，默认 500）。' },
        roundTimeoutMs: { type: 'number', description: 'loop_start：每轮 skill_run 的超时上限（1000-600000；省略按 dryRun 估算 +30s）。' },
        immediate: { type: 'boolean', description: 'loop_stop：true 时立即终止当前轮（会终止 Python worker），默认 false 等当前轮结束。' },
      },
      required: ['action'],
    },
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    async execute(args) {
      const action = typeof args?.action === 'string' ? args.action : '';
      /** @type {Record<string, unknown>} */
      const params = {};
      for (const [key, value] of Object.entries(args ?? {})) {
        if (key === 'action' || value === undefined || value === null) continue;
        params[key] = value;
      }
      // Host 级循环动作：交给 LoopRunner（每轮独立调用 skill_run）。
      if (LOOP_ACTIONS.includes(action)) {
        try {
          const startedAt = Date.now();
          const result = await loopRunner.handle(action, params);
          return jsonSafe({ ok: true, action, result, durationMs: Date.now() - startedAt });
        } catch (error) {
          const message = `[${error?.code ?? 'INTERNAL'}] ${String(error?.message ?? error)}${error?.hint ? `（${error.hint}）` : ''}`;
          throw new Error(message);
        }
      }
      const envelope = await bridge.execute(action, params);
      if (envelope.ok !== true) {
        const error = envelope.error ?? {};
        const message = `[${error.code ?? 'BACKEND_ERROR'}] ${error.message ?? '执行失败'}${error.hint ? `（${error.hint}）` : ''}`;
        throw new Error(message);
      }
      return jsonSafe({
        ok: true,
        action,
        result: envelope.result ?? null,
        durationMs: envelope.durationMs ?? null,
      });
    },
  };
}

/* ------------------------------------------------------------------ */
/* 插件入口                                                            */
/* ------------------------------------------------------------------ */

function registerRoutes(ctx, bridge, loopRunner) {
  const webServer =
    (typeof ctx.get === 'function' ? ctx.get('webServer') : null) ||
    (ctx.webServer ?? null);

  if (webServer === null || typeof webServer.register !== 'function') {
    ctx.logger?.warn?.('game-control: webServer 不可用，侧栏面板将拿不到数据（agent 工具不受影响）');
    return;
  }

  const handler = async (req, res) => {
    let url;
    try {
      url = new URL(req.url ?? '/', 'http://127.0.0.1');
    } catch {
      sendJson(res, 400, errorEnvelope('route', 'INVALID_ARGUMENT', '非法请求 URL'));
      return;
    }
    const sub = url.pathname.slice(ROUTE_PREFIX.length) || '/';
    try {
      if (req.method === 'GET' && sub === '/health') {
        sendJson(res, 200, { ok: true, version: VERSION, bridge: bridge.info() });
        return;
      }
      if (req.method === 'GET' && sub === '/status') {
        const backend = await bridge.execute('status', {});
        sendJson(res, 200, { ok: true, version: VERSION, bridge: bridge.info(), backend });
        return;
      }
      if (req.method === 'GET' && sub === '/logs') {
        const lines = Number.parseInt(url.searchParams.get('lines') ?? '80', 10);
        sendJson(res, 200, { ok: true, logTail: tailLog(Number.isFinite(lines) ? lines : 80) });
        return;
      }
      if (req.method === 'GET' && sub === '/skill-progress') {
        const info = bridge.info();
        let progress = readSkillProgress();
        // worker 已退出但进度仍写着 running → 标记 stale，避免面板永久“运行中”。
        if (progress !== null && progress.running === true && info.running !== true) {
          progress = markSkillProgressStale(info.lastError ?? 'Python 后端已停止');
        }
        sendJson(res, 200, {
          ok: true,
          progress,
          bridge: { running: info.running, lastError: info.lastError ?? null },
        });
        return;
      }
      if (req.method === 'POST' && sub === '/exec') {
        let body;
        try {
          const text = await readBody(req);
          body = text.trim() === '' ? {} : JSON.parse(text);
        } catch (error) {
          sendJson(res, 400, errorEnvelope('route', error?.code ?? 'INVALID_ARGUMENT', `请求体解析失败：${String(error?.message ?? error)}`));
          return;
        }
        const action = typeof body?.action === 'string' ? body.action : '';
        const params = body?.params !== null && typeof body?.params === 'object' && !Array.isArray(body.params) ? body.params : {};
        if (action === '') {
          sendJson(res, 400, errorEnvelope('route', 'INVALID_ARGUMENT', '缺少 action 字符串'));
          return;
        }
        // Host 级循环动作：不经过 Python worker。
        if (LOOP_ACTIONS.includes(action)) {
          const startedAt = Date.now();
          try {
            const result = await loopRunner.handle(action, params);
            sendJson(res, 200, { ok: true, action, result, durationMs: Date.now() - startedAt });
          } catch (error) {
            sendJson(
              res,
              200,
              errorEnvelope(action, error?.code ?? 'INTERNAL', String(error?.message ?? error), error?.hint),
            );
          }
          return;
        }
        const envelope = await bridge.execute(action, params);
        sendJson(res, 200, envelope);
        return;
      }
      if (sub === '/exec' || sub === '/status' || sub === '/health' || sub === '/logs' || sub === '/skill-progress') {
        sendJson(res, 405, errorEnvelope('route', 'INVALID_ARGUMENT', `不支持的方法：${req.method} ${sub}`));
        return;
      }
      sendJson(res, 404, errorEnvelope('route', 'UNKNOWN_ACTION', `未知接口：${req.method} ${sub}`));
    } catch (error) {
      ctx.logger?.error?.('game-control: route failed', error);
      sendJson(res, 500, errorEnvelope('route', 'INTERNAL', String(error?.message ?? error)));
    }
  };

  ctx.effect(
    () => webServer.register({ kind: 'prefix', path: ROUTE_PREFIX, handler }),
    'game-control: /api/game-control 路由',
  );
  ctx.logger?.info?.(`game-control: 路由已注册 ${ROUTE_PREFIX}/*`);
}

function registerTool(ctx, bridge, loopRunner) {
  const registrations = [
    ['game_control', toolDefinition(bridge)],
    ['game_skill', skillToolDefinition(bridge, loopRunner)],
  ];
  for (const [toolName, definition] of registrations) {
    try {
      ctx.effect(() => ctx.tools.register(definition), `game-control: ${toolName} 工具`);
      ctx.logger?.info?.(`game-control: 工具 ${toolName} 已注册`);
    } catch (error) {
      // 一个工具被宿主拒绝不应拖垮另一个工具与路由。
      ctx.logger?.error?.(`game-control: 工具 ${toolName} 注册失败：${String(error?.message ?? error)}`);
    }
  }
}

export function apply(ctx) {
  const bridge = new PythonBridge();
  const loopRunner = new LoopRunner(bridge);
  ctx.effect(() => () => {
    loopRunner.dispose();
    bridge.dispose();
  }, 'game-control: Python 桥/循环清理');

  registerRoutes(ctx, bridge, loopRunner);
  registerTool(ctx, bridge, loopRunner);

  // Python 路径在第一次显式动作时再探测：apply 期间不 spawn 任何进程，
  // 避免拖慢 profile 启动，也避免 Windows 商店版 python 别名弹窗等副作用。
  const explicitPython = process.env.DSH_GAME_CONTROL_PYTHON || process.env.GAME_CONTROL_PYTHON || 'auto';
  ctx.logger?.info?.(`game-control: ready (version=${VERSION}, python=${explicitPython}, backend=${BACKEND_SCRIPT})`);
}
