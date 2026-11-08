#!/usr/bin/env node
/**
 * dsh-game-control 最小验收用例（严格按 SPEC.md §6 的 8 项验收标准 + v0.2 窗口截图 + v0.3 游戏自动化/技能库
 * + v0.4 技能文件 JSONC/中文注释 + v0.5 Esc 中止/多模板/运行进度/结构化编辑 + v0.6 长循环/连接中断修复
 * + v0.6.1 单一轮数语义（rounds=1 单次、>1 循环，repeat deprecated）
 * + v0.6.2 模板等待超时修复（onMissing / WAIT_IMAGE_TIMEOUT / failedStep.screenshotPath））。
 *
 * 特点：
 *  - 纯 Node（>=20），零第三方依赖，不需要联网、不需要安装插件、不碰 profile；
 *  - 只做**无副作用**检查：静态检查 + Python 只读动作（status/list_displays/list_windows/
 *    capture_window engine=screen/selftest/--version/--serve 握手 / mouse_circle dryRun /
 *    find_image / wait_image 短超时 / skill_* 干跑与 wait(0) 技能）+ 截图；
 *    **绝不点击 / 按键 / 拖拽 / 输入文本 / 激活窗口 / 真实画圆**；
 *  - WGC 真后台抓取路径**不在本脚本里测试**（DSH 沙箱内 WGC 可能不可用）：只测 engine=screen
 *    的落盘链路；WGC/printwindow/window-dc 回退链由 Lead 在未受限环境单独验收；
 *  - v0.3 的 mouse_circle 只测 dryRun（不注入、不等待），wait_image 只测「找不到 → timedOut」，
 *    skill_run 只测 dryRun 与「仅 wait 0ms」技能（零输入副作用）；
 *  - v0.4 在唯一临时 GAME_CONTROL_HOME 下验证技能文件是含中文注释的 JSONC（显式 waitMs/归一化参数）、
 *    content 全文编辑、note 保留、坏 content 报 INVALID_SKILL、旧纯 JSON 兼容与版本恢复；
 *  - v0.5 验证默认 abortKey=esc、templates 多模板参数校验、skill_run 前置时长校验与 estimatedDurationMs、
 *    skill_progress 动作与 logs/skill-progress.json 字段；
 *  - Python 解释器自动发现：DSH_GAME_CONTROL_PYTHON / GAME_CONTROL_PYTHON 环境变量 →
 *    ~/.dsh/dsh-runtimes/​*​/dependencies/python/python.exe → PATH 的 python3 / python；
 *    都找不到时（Windows 上常见）Python 相关项记 SKIP，不判失败；
 *  - 所有临时文件都在 test/.tmp/ 下，结束时自动清理（--keep-tmp 可保留排查）。
 *
 * 用法：
 *   node test/acceptance.mjs             # 逐条输出 PASS / FAIL / SKIP + 汇总
 *   node test/acceptance.mjs --verbose   # 额外打印每条 Python 命令的 stdout/stderr 摘要
 *   node test/acceptance.mjs --keep-tmp  # 保留 test/.tmp 产物
 *
 * 退出码：0 = 没有 FAIL（允许 SKIP）；1 = 存在 FAIL。
 */

import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/* ------------------------------------------------------------------ */
/* 路径与常量                                                          */
/* ------------------------------------------------------------------ */

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const PLUGIN = path.join(ROOT, 'dsh-game-control');
const PY_DIR = path.join(PLUGIN, 'python');
const TMP_DIR = path.join(HERE, '.tmp');
/**
 * Lead 约定：Python 后端的 GAME_CONTROL_HOME 固定放 test/.tmp，避开 DSH 沙箱只读的
 * ~/.dsh 默认目录。这里每次运行用独立的 gc-home-<pid>-<ts> 子目录，避免两次验收
 * （或与 py-engineer 的 Python 自测）并行时互相污染截图目录 / 日志 / 审计基线。
 */
const RUN_ID = `${process.pid}-${Date.now().toString(36)}`;
const HOME_DIR = path.join(TMP_DIR, `gc-home-${RUN_ID}`);
const SHOT_DIR = path.join(HOME_DIR, 'shots');
const LOG_DIR = path.join(HOME_DIR, 'logs');
const SPAWN_DIR = path.join(TMP_DIR, 'spawn');
/** v0.3 技能库/模板测试的临时工作目录（合成模板、损坏模板、坏技能等）。 */
const V03_DIR = path.join(TMP_DIR, 'v03');
const CLI = path.join(PY_DIR, 'game_control.py');

const VERBOSE = process.argv.includes('--verbose');
const KEEP_TMP = process.argv.includes('--keep-tmp');

const results = [];
const startedAt = Date.now();

/* ------------------------------------------------------------------ */
/* 输出与断言小工具                                                     */
/* ------------------------------------------------------------------ */

function oneLine(value, max = 200) {
  return String(value === undefined || value === null ? '' : value)
    .replace(/\r?\n/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, max);
}

function fmtBytes(size) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(2)} MB`;
}

function emit(status, id, name, detail) {
  results.push({ status, id, name, detail: oneLine(detail, 600) });
  const head = `${status.padEnd(4)}  ${String(id).padEnd(5)} ${name}`;
  if (status === 'PASS') {
    console.log(`${head}${VERBOSE ? `   · ${oneLine(detail, 160)}` : ''}`);
  } else {
    console.log(head);
    console.log(`      ↳ ${oneLine(detail, 1200) || '(无详情)'}`);
  }
}

function section(title) {
  console.log(`\n── ${title} ──`);
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function assertFile(file, label) {
  let stat;
  try {
    stat = fs.statSync(file);
  } catch {
    throw new Error(`缺少文件：${label}（${file}）`);
  }
  assert(stat.isFile(), `不是普通文件：${label}（${file}）`);
  assert(stat.size > 0, `文件为空：${label}（${file}）`);
  return stat;
}

function readText(file, label) {
  assertFile(file, label);
  return fs.readFileSync(file, 'utf8');
}

/**
 * 捕获子进程输出，但**不使用管道**：stdin/stdout/stderr 全部走 test/.tmp 里的普通文件。
 *
 * 原因：DSH 的 Windows 沙箱禁止子进程通过命名管道传 stdio（Node 默认的
 * child_process.spawn/spawnSync 管道会直接 EPERM）。改用文件描述符后，无论在
 * DSH 沙箱内还是普通终端里运行本脚本都能工作。
 */
let spawnSeq = 0;
function runCaptured(cmd, args, options = {}) {
  fs.mkdirSync(SPAWN_DIR, { recursive: true });
  const seq = ++spawnSeq;
  const outFile = path.join(SPAWN_DIR, `out-${seq}.txt`);
  const errFile = path.join(SPAWN_DIR, `err-${seq}.txt`);
  const inFile = path.join(SPAWN_DIR, `in-${seq}.jsonl`);
  const outFd = fs.openSync(outFile, 'w');
  const errFd = fs.openSync(errFile, 'w');
  let inFd = 'ignore';
  let result;
  try {
    if (options.input !== undefined) {
      fs.writeFileSync(inFile, String(options.input), 'utf8');
      inFd = fs.openSync(inFile, 'r');
    }
    result = spawnSync(cmd, args, {
      cwd: options.cwd ?? ROOT,
      env: options.env ?? { ...process.env },
      timeout: options.timeoutMs ?? 60_000,
      windowsHide: true,
      stdio: [inFd, outFd, errFd],
    });
  } finally {
    try {
      fs.closeSync(outFd);
    } catch {
      /* ignore */
    }
    try {
      fs.closeSync(errFd);
    } catch {
      /* ignore */
    }
    if (typeof inFd === 'number') {
      try {
        fs.closeSync(inFd);
      } catch {
        /* ignore */
      }
      try {
        fs.unlinkSync(inFile);
      } catch {
        /* ignore */
      }
    }
  }
  const stdout = fs.existsSync(outFile) ? fs.readFileSync(outFile, 'utf8') : '';
  const stderr = fs.existsSync(errFile) ? fs.readFileSync(errFile, 'utf8') : '';
  try {
    fs.unlinkSync(outFile);
  } catch {
    /* ignore */
  }
  try {
    fs.unlinkSync(errFile);
  } catch {
    /* ignore */
  }
  if (VERBOSE) {
    const shown = args.map((arg) => {
      const text = String(arg).replace(/\s+/g, ' ');
      return text.length > 140 ? `${text.slice(0, 140)}…` : text;
    });
    console.log(`      $ ${cmd} ${shown.join(' ')}`);
    if (stdout) console.log(`        stdout: ${oneLine(stdout, 500)}`);
    if (stderr) console.log(`        stderr: ${oneLine(stderr, 500)}`);
    if (result.error) console.log(`        spawn error: ${result.error.code ?? ''} ${result.error.message}`);
  }
  return {
    status: result.status,
    signal: result.signal,
    error: result.error ?? null,
    stdout,
    stderr,
  };
}

function check(id, name, fn) {
  try {
    const detail = fn();
    emit('PASS', id, name, detail === undefined ? '' : detail);
  } catch (error) {
    emit('FAIL', id, name, VERBOSE && error && error.stack ? error.stack : (error && error.message) || error);
  }
}

function skip(id, name, reason) {
  emit('SKIP', id, name, reason);
}

/* ------------------------------------------------------------------ */
/* 1. 交付文件齐全（SPEC §6.1 / §1）                                   */
/* ------------------------------------------------------------------ */

function checkFiles() {
  section('1. 交付文件齐全（SPEC §6.1 / §1）');

  const required = [
    ['dsh-game-control/package.json', path.join(PLUGIN, 'package.json')],
    ['dsh-game-control/index.js', path.join(PLUGIN, 'index.js')],
    ['dsh-game-control/client.js', path.join(PLUGIN, 'client.js')],
    ['dsh-game-control/cordis.patch.yml', path.join(PLUGIN, 'cordis.patch.yml')],
    ['dsh-game-control/icon.svg', path.join(PLUGIN, 'icon.svg')],
    ['dsh-game-control/locale/zh.json', path.join(PLUGIN, 'locale', 'zh.json')],
    ['dsh-game-control/locale/en.json', path.join(PLUGIN, 'locale', 'en.json')],
    ['dsh-game-control/python/game_control.py', path.join(PY_DIR, 'game_control.py')],
    ['dsh-game-control/python/requirements.txt', path.join(PY_DIR, 'requirements.txt')],
    ['README.md', path.join(ROOT, 'README.md')],
    ['requirements.txt', path.join(ROOT, 'requirements.txt')],
  ];

  required.forEach(([label, file], index) => {
    const id = `1.${index + 1}`;
    check(id, label, () => {
      const stat = assertFile(file, label);
      return fmtBytes(stat.size);
    });
  });

  check('1.12', 'python 后端模块（win_native / screenshot / input_backend）', () => {
    const modules = ['win_native.py', 'screenshot.py', 'input_backend.py'];
    for (const name of modules) assertFile(path.join(PY_DIR, name), `python/${name}`);
    return `${modules.length} 个模块齐全（SPEC §2.5）`;
  });

  check('1.13', 'python/test_backend.py（SPEC §2.7 Python 自测脚本）', () => {
    const stat = assertFile(path.join(PY_DIR, 'test_backend.py'), 'python/test_backend.py');
    return fmtBytes(stat.size);
  });

  check('1.14', 'README.md 内容覆盖（功能/安装/接口/错误码/安全/跨平台/验收/参考）', () => {
    const text = readText(path.join(ROOT, 'README.md'), 'README.md');
    const requiredSections = [
      '功能清单',
      '目录结构',
      '安装步骤',
      '依赖安装说明',
      '侧栏面板使用说明',
      '插件接口',
      '后台窗口截图',
      '游戏自动化',
      '技能库',
      '调用示例',
      '异常处理与日志',
      '安全边界',
      '跨平台支持矩阵与限制',
      '最小验收用例',
      '参考项目与致谢',
    ];
    const missing = requiredSections.filter((name) => !text.includes(name));
    assert(missing.length === 0, `README 缺少章节：${missing.join('、')}`);
    for (const url of [
      'https://github.com/LJL454/dsh-computer-use',
      'https://github.com/gychen-NJU/dsh-game-daily',
    ]) {
      assert(text.includes(url), `README 缺少参考项目链接：${url}`);
    }
    for (const token of ['plugin_manager', 'install_bundle', 'requirements.txt', 'GAME_CONTROL_HOME', '--stdin']) {
      assert(text.includes(token), `README 缺少关键内容：${token}`);
    }
    return `${requiredSections.length} 个必需章节 + 参考链接 + 安装/环境变量/CLI 关键字`;
  });

  check('1.15', 'requirements.txt 声明 Pillow 依赖（根级与 python/）', () => {
    for (const [label, file] of [
      ['requirements.txt', path.join(ROOT, 'requirements.txt')],
      ['dsh-game-control/python/requirements.txt', path.join(PY_DIR, 'requirements.txt')],
    ]) {
      const text = readText(file, label);
      assert(/Pillow/i.test(text), `${label} 未声明 Pillow 依赖`);
    }
    return '两处 requirements.txt 均声明 Pillow';
  });

  check('1.16', 'README「后台窗口截图（v0.2）」章节内容完整', () => {
    const text = readText(path.join(ROOT, 'README.md'), 'README.md');
    const sectionMatch = text.match(/##\s*后台窗口截图[^\n]*\n([\s\S]*?)(?=\n##\s)/);
    assert(sectionMatch, 'README 缺少「## 后台窗口截图」章节（v0.2）');
    const sectionText = `${sectionMatch[0]}`;
    const requiredTokens = [
      'WGC',
      'list_windows',
      'capture_window',
      'engine',
      'WINDOW_NOT_FOUND',
      'WINDOW_CAPTURE_FAILED',
      'windows_capture',
      'numpy',
      '1903',
      'draw_border',
      'verification-window-background.png',
      '遮挡',
    ];
    const missing = requiredTokens.filter((token) => !text.includes(token));
    assert(missing.length === 0, `README 缺少 v0.2 关键字：${missing.join('、')}`);
    assert(sectionText.includes('WGC'), '「后台窗口截图」章节必须说明 WGC 主路径');
    assert(sectionText.includes('printwindow') || sectionText.includes('PrintWindow'), '章节必须说明 printwindow 回退');
    assert(sectionText.includes('screen'), '章节必须说明 screen 回退/可见像素边界');
    return `章节存在，含 WGC/回退链/依赖/错误码/证据图等 ${requiredTokens.length} 个关键字`;
  });

  check('1.17', 'docs/verification-window-background.png 证据图存在且为 PNG', () => {
    const file = path.join(ROOT, 'docs', 'verification-window-background.png');
    assertFile(file, 'docs/verification-window-background.png（WGC 后台截图证据）');
    const head = fs.readFileSync(file).subarray(0, 8);
    const pngMagic = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
    assert(head.equals(pngMagic), '证据图文件头不是 PNG 魔数');
    return fmtBytes(fs.statSync(file).size);
  });

  check('1.18', 'python 窗口截图后端与随包 WGC 依赖（window_capture.py + vendor/windows_capture + LICENSE）', () => {
    assertFile(path.join(PY_DIR, 'window_capture.py'), 'python/window_capture.py');
    const vendorRoot = path.join(PY_DIR, 'vendor');
    let entries = [];
    try {
      entries = fs.readdirSync(vendorRoot, { withFileTypes: true });
    } catch {
      throw new Error('缺少随包依赖目录 python/vendor/（windows-capture 解包，SPEC §2.3.1）');
    }
    const pkgDir = path.join(vendorRoot, 'windows_capture');
    assert(entries.some((entry) => entry.isDirectory() && entry.name === 'windows_capture'), '缺少 python/vendor/windows_capture/');
    assertFile(path.join(pkgDir, '__init__.py'), 'python/vendor/windows_capture/__init__.py');
    const licenseFiles = [];
    const walk = (dir) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) walk(full);
        else if (/licen[cs]e|copying/i.test(entry.name)) licenseFiles.push(full);
      }
    };
    walk(vendorRoot);
    assert(licenseFiles.length > 0, 'python/vendor/ 下缺少 windows-capture 的 LICENSE 文件（MIT 许可要求随包分发）');
    return `window_capture.py + vendor/windows_capture + ${licenseFiles.length} 个 LICENSE`;
  });

  check('1.19', 'python v0.3 模块（circle_motion.py / template_match.py / skills.py）', () => {
    const modules = ['circle_motion.py', 'template_match.py', 'skills.py'];
    for (const name of modules) assertFile(path.join(PY_DIR, name), `python/${name}`);
    return `${modules.length} 个 v0.3 模块齐全（SPEC §8.2/§8.3）`;
  });

  check('1.20', 'README v0.3 内容（游戏自动化 / 技能库 / 新动作 / Esc / 模板 / 变量语法）', () => {
    const text = readText(path.join(ROOT, 'README.md'), 'README.md');
    const requiredTokens = [
      '游戏自动化',
      '技能库',
      'activate_window',
      'mouse_circle',
      'wait_image',
      'find_image',
      'Esc',
      'templates/',
      'skill.json',
      'skill_run',
      'dryRun',
      '$window',
      '$saveAs',
      '备份',
      '软删除',
      'clientRect',
    ];
    const missing = requiredTokens.filter((token) => !text.includes(token));
    assert(missing.length === 0, `README 缺少 v0.3 关键字：${missing.join('、')}`);
    for (const sectionName of ['游戏自动化', '技能库']) {
      assert(
        new RegExp(`##\\s*${sectionName}`).test(text),
        `README 缺少「## ${sectionName}」章节（v0.3）`,
      );
    }
    return `${requiredTokens.length} 个 v0.3 关键字 + 两个新章节`;
  });

  check('1.21', 'README「技能文件格式（JSONC + 中文注释）」章节（v0.4，SPEC §9.5）', () => {
    const text = readText(path.join(ROOT, 'README.md'), 'README.md');
    assert(/##\s*技能文件格式/.test(text), 'README 缺少「## 技能文件格式（JSONC + 中文注释）」章节');
    const requiredTokens = [
      'JSONC',
      '中文注释',
      'strip_jsonc',
      'normalize_skill',
      'render_skill_jsonc',
      'content',
      'stepsView',
      'note',
      'waitMs',
      'continueOnError',
      '归一化',
      '自动补全',
      '重新生成',
      '旧',
      '兼容',
      '完整配置文件',
    ];
    const missing = requiredTokens.filter((token) => !text.includes(token));
    assert(missing.length === 0, `README 缺少 v0.4 关键字：${missing.join('、')}`);
    assert(/\/\/\s*第\s*\d+\s*步/.test(text), 'README 缺少「// 第 N 步：…」中文注释示例');
    assert(/\/\/\s*第\s*1\s*步/.test(text), 'README 示例需包含「// 第 1 步：」');
    const sectionMatch = text.match(/##\s*技能文件格式[\s\S]*?(?=\n##\s)/);
    assert(sectionMatch, 'README 技能文件格式章节内容为空');
    const section = sectionMatch[0];
    for (const action of ['mouse_click', 'mouse_circle', 'mouse_drag']) {
      assert(section.includes(action), `技能文件格式章节缺少 ${action} 的完整参数示例`);
    }
    for (const token of ['radius', 'periodMs', 'samplesPerSecond', 'clicks', 'steps']) {
      assert(section.includes(token), `技能文件格式章节示例缺少参数 ${token}`);
    }
    return `JSONC 章节 + ${requiredTokens.length} 个关键字 + 第 N 步注释/参数示例`;
  });

  check('1.22', 'python/skills.py 含 JSONC 处理函数（strip_jsonc / normalize_skill / render_skill_jsonc，SPEC §9.1）', () => {
    const text = readText(path.join(PY_DIR, 'skills.py'), 'python/skills.py');
    for (const name of ['strip_jsonc', 'normalize_skill', 'render_skill_jsonc']) {
      assert(
        new RegExp(`def\\s+${name}\\s*\\(`).test(text),
        `python/skills.py 缺少函数 ${name}（SPEC §9.1）`,
      );
    }
    return 'strip_jsonc / normalize_skill / render_skill_jsonc 均已实现';
  });

  check('1.23', 'README v0.5 内容（Esc / templates 多模板 / 进度 / estimatedDurationMs / 结构化步骤）', () => {
    const text = readText(path.join(ROOT, 'README.md'), 'README.md');
    const requiredTokens = [
      'Esc',
      'abortKey',
      'templates',
      'skill-progress.json',
      '/api/game-control/skill-progress',
      'estimatedDurationMs',
      '结构化步骤',
      '第 N/M',
      '禁用',
      '0.6.2',
      'radius',
      '360',
      '不再',
    ];
    const missing = requiredTokens.filter((token) => !text.includes(token));
    assert(missing.length === 0, `README 缺少 v0.5 关键字：${missing.join('、')}`);
    assert(!/按\s*F12/.test(text), 'README 仍要求按 F12（v0.5 应全部改为 Esc）');
    assert(!/默认\s*F12/.test(text), 'README 仍写「默认 F12」（v0.5 默认 Esc）');
    assert(!/"f12"/i.test(text), 'README 仍含 "f12" 默认值（v0.5 默认 esc）');
    assert((text.match(/Esc/g) || []).length >= 15, 'README Esc 出现次数过少，F12→Esc 替换可能不完整');
    return `${requiredTokens.length} 个 v0.5 关键字 + 无残留 F12 默认值`;
  });

  check('1.24', 'README 运行进度/结构化表格/示例技能细节（SPEC §10.3/§10.4）', () => {
    const text = readText(path.join(ROOT, 'README.md'), 'README.md');
    assert(/运行进度与防卡死/.test(text), 'README 缺少「运行进度与防卡死」小节');
    assert(/结构化步骤表格/.test(text), 'README 缺少结构化步骤表格说明');
    const requiredTokens = [
      'skill-progress.json',
      'running',
      'skillId',
      '已等待',
      '上移',
      '下移',
      '新增步骤',
      '高级',
      '完整配置文件',
      'keep-on-mining-30s',
      'next_round',
      'start_mining',
      '30000',
      '2000',
      '排队',
    ];
    const missing = requiredTokens.filter((token) => !text.includes(token));
    assert(missing.length === 0, `README 缺少 v0.5 细节关键字：${missing.join('、')}`);
    assert(/estimatedDurationMs/.test(text) && /INVALID_ARGUMENT/.test(text), 'README 缺少前置时长校验说明');
    return `${requiredTokens.length} 个细节关键字 + 进度/表格/示例技能说明`;
  });

  check('1.25', 'Python abortKey 默认值为 esc（mouse_circle / find_image / wait_image / skill_run）', () => {
    const text = readText(path.join(PY_DIR, 'game_control.py'), 'python/game_control.py');
    const defaultEscCount = (text.match(/default\s*=\s*["']esc["']/g) || []).length;
    assert(
      defaultEscCount >= 4,
      `game_control.py 中 default="esc" 出现 ${defaultEscCount} 次，应至少 4 处（mouse_circle/find_image/wait_image/skill_run）`,
    );
    const abortKeyCount = (text.match(/abortKey[\s\S]{0,80}default\s*=\s*["']esc["']/g) || []).length;
    assert(abortKeyCount >= 2, 'game_control.py 中未发现 abortKey 默认 esc 的参数定义');
    return `default="esc" × ${defaultEscCount}（含 abortKey 默认 esc）`;
  });

  check('1.26', 'README「长循环与连接修复（v0.6/v0.6.1）」章节与关键字（SPEC §11.2–§11.5、§12）', () => {
    const text = readText(path.join(ROOT, 'README.md'), 'README.md');
    assert(/##\s*长循环与连接修复/.test(text), 'README 缺少「## 长循环与连接修复」章节');
    const requiredTokens = [
      'loop_start',
      'loop_status',
      'loop_stop',
      'loop-state.json',
      'loop.jsonl',
      'rounds',
      'intervalMs',
      'stopOnError',
      'roundTimeoutMs',
      'immediate',
      'LOOP_ALREADY_RUNNING',
      '每轮独立',
      'stale',
      'backend-stopped',
      'bridge',
      '重置状态',
      '后端连接中断',
      'currentStep',
      '0.6.2',
    ];
    const missing = requiredTokens.filter((token) => !text.includes(token));
    assert(missing.length === 0, `README 缺少 v0.6/v0.6.1 关键字：${missing.join('、')}`);
    assert(/1[–-]10000/.test(text), 'README 未写 rounds 1–10000');
    assert(/每轮.{0,30}skill_run|skill_run.{0,30}每轮/.test(text), 'README 未说明「每轮独立 skill_run」');
    return `${requiredTokens.length} 个 v0.6/v0.6.1 关键字 + 章节存在`;
  });

  check('1.27', 'README 长循环细节（不撞 310s / deadline / stale / 循环面板，SPEC §11.3–§11.6）', () => {
    const text = readText(path.join(ROOT, 'README.md'), 'README.md');
    assert(/310\s*s|310s/.test(text), 'README 未说明不再撞 Host 310s');
    assert(/120000|120000|120s|120000/.test(text) && /120/.test(text), 'README 未写 Host +120s 超时余量');
    assert(/deadline/i.test(text), 'README 未说明 Python 总 deadline 优雅中止');
    assert(/abortReason\s*[:”"]?\s*"?timeout/.test(text) || /abortReason:"timeout"/.test(text), 'README 未说明 abortReason=timeout');
    assert(/每轮一行|loop\.jsonl/.test(text), 'README 未说明 loop.jsonl 每轮一行');
    for (const token of ['backend-stopped', 'stale:true', 'Failed to fetch', '重置状态', '立即停止']) {
      assert(text.includes(token), `README 缺少 v0.6 细节关键字「${token}」`);
    }
    assert(/bridge:\{running,lastError\}/.test(text), 'README 未说明 /skill-progress 返回 bridge:{running,lastError}');
    assert(/第 N\/M 轮/.test(text), 'README 未写面板统一轮次进度文案（第 N/M 轮）');
    return '310s/deadline/stale/bridge/循环面板细节齐全';
  });

  check('1.28', 'README 长循环错误码与调用示例（LOOP_ALREADY_RUNNING / loop_start·loop_status·loop_stop / immediate）', () => {
    const text = readText(path.join(ROOT, 'README.md'), 'README.md');
    assert(
      /\|\s*`LOOP_ALREADY_RUNNING`\s*\|/.test(text),
      'README 错误码表缺少 LOOP_ALREADY_RUNNING 行',
    );
    assert(
      /"action":\s*"loop_start"/.test(text),
      'README 缺少 agent 工具 loop_start 调用示例',
    );
    assert(
      /"action":\s*"loop_status"/.test(text) || /action:'loop_status'|"action":"loop_status"/.test(text),
      'README 缺少 loop_status 调用示例',
    );
    assert(
      /immediate["': ]+true|"immediate":\s*true/.test(text),
      'README 缺少 loop_stop immediate:true 示例',
    );
    assert(/loop-state\.json/.test(text) && /loop\.jsonl/.test(text), 'README 缺少 loop 状态文件说明');
    return '错误码 LOOP_ALREADY_RUNNING + loop_start/status/stop(immediate) 示例 + 状态文件';
  });

  check('1.29', 'README v0.6.1 单一轮数语义（rounds=1 单次 / >1 循环 / repeat deprecated / 无重复次数双控制，SPEC §12.2）', () => {
    const text = readText(path.join(ROOT, 'README.md'), 'README.md');
    assert(/rounds\s*=\s*1/.test(text), 'README 未写 rounds=1 语义');
    assert(/rounds\s*=\s*1[\s\S]{0,40}单次运行|单次运行[\s\S]{0,40}rounds\s*=\s*1/.test(text), 'README 未说明 rounds=1 即单次运行');
    assert(/rounds\s*>\s*1/.test(text) && /循环/.test(text), 'README 未说明 rounds>1 即循环');
    assert(/loop_start/.test(text) && /统一\s*N\s*轮|统一轮数/.test(text), 'README 未写 loop_start 是统一 N 轮入口');
    assert(/repeat[\s\S]{0,40}deprecated|deprecated[\s\S]{0,40}repeat/i.test(text), 'README 未写 repeat 已 deprecated');
    assert(/CLI[\s\S]{0,20}兼容|兼容[\s\S]{0,20}CLI/.test(text), 'README 未说明 repeat 仅 CLI/内部兼容');
    assert(/currentStep/.test(text), 'README 未写 loop_status.currentStep');
    assert(/没有「重复次数」|不再有「重复次数」/.test(text), 'README 未说明删除「重复次数」双控制');
    assert(/运行一次|启动循环/.test(text), 'README 未说明删除「运行一次/启动循环」两个按钮');
    return 'rounds=1 单次 / >1 循环 / loop_start 统一入口 / repeat deprecated / currentStep / 无双控制';
  });

  check('1.30', 'README「模板等待超时修复（v0.6.2）」章节与关键字（SPEC §13.2）', () => {
    const text = readText(path.join(ROOT, 'README.md'), 'README.md');
    assert(/##\s*模板等待超时修复/.test(text), 'README 缺少「## 模板等待超时修复（v0.6.2）」章节');
    const requiredTokens = [
      'onMissing',
      'WAIT_IMAGE_TIMEOUT',
      'TEMPLATE_NOT_FOUND',
      'screenshotPath',
      'skill-fail-',
      'failedStep',
      '失败后继续',
      'stopOnError',
      '30000',
      '0.6.2',
    ];
    const missing = requiredTokens.filter((token) => !text.includes(token));
    assert(missing.length === 0, `README 缺少 v0.6.2 关键字：${missing.join('、')}`);
    assert(/NoneType/.test(text), 'README 未说明 $btn.match.center.x 的 NoneType 根因');
    assert(/onMissing["'：:\s]{0,40}fail|fail.{0,20}默认/.test(text), 'README 未写 onMissing 默认 fail');
    assert(/continue/.test(text), 'README 未写 onMissing="continue" 语义');
    return `${requiredTokens.length} 个 v0.6.2 关键字 + 章节存在`;
  });

  check('1.31', 'README v0.6.2 细节（根因/失败截图/变量 hint/失败后继续/示例技能 timeoutMs 30000，SPEC §13.2）', () => {
    const text = readText(path.join(ROOT, 'README.md'), 'README.md');
    assert(/\$btn\.match\.center\.x/.test(text), 'README 未提 $btn.match.center.x 报错示例');
    assert(/logs[\\/]skill-fail-|skill-fail-/.test(text), 'README 未写失败截图路径规则');
    assert(/failedStep\.screenshotPath/.test(text), 'README 未写 failedStep.screenshotPath');
    assert(/match.{0,20}为空|变量错误.{0,40}hint|hint.{0,40}模板未找到/i.test(text), 'README 未写变量错误 hint 增强');
    assert(/失败后继续/.test(text) && /stopOnError\s*[:=]\s*false/.test(text), 'README 未写「失败后继续」↔ stopOnError=false');
    assert(/results[\s\S]{0,80}failedStep|failedStep[\s\S]{0,80}results/.test(text), 'README 未写循环 results 带 failedStep');
    assert(
      /timeoutMs[\s"':=]{0,12}30000/.test(text) && /onMissing["'：:\s]{0,20}fail/.test(text),
      'README 示例技能应含 timeoutMs 30000 与显式 onMissing:"fail"',
    );
    assert(/技能文件格式/.test(text) && /单步允许字段[\s\S]{0,120}onMissing/.test(text), 'README 技能文件字段说明未加 onMissing');
    return '根因/失败截图/变量 hint/失败后继续/示例技能(onMissing+30000) 细节齐全';
  });

  check('1.32', 'python/skills.py 含 onMissing 归一化与注释放 NOT-FOUND 说明（SPEC §13.2.1）', () => {
    const text = readText(path.join(PY_DIR, 'skills.py'), 'python/skills.py');
    assert(text.includes('onMissing'), 'python/skills.py 缺少 onMissing 处理');
    assert(/["']fail["']/.test(text) && /["']continue["']/.test(text), 'python/skills.py 缺少 fail/continue 取值');
    assert(/找不到时/.test(text), 'python/skills.py 生成注释缺少「找不到时」说明');
    assert(/onMissing/.test(text), 'python/skills.py 归一化未写显式 onMissing');
    return 'skills.py: onMissing fail/continue + 注释「找不到时」';
  });
}

/* ------------------------------------------------------------------ */
/* 2. node --check                                                     */
/* ------------------------------------------------------------------ */

function checkNodeSyntax() {
  section('2. JavaScript 语法检查（node --check，SPEC §6.2）');

  const targets = [
    ['2.1', path.join(PLUGIN, 'index.js'), 'dsh-game-control/index.js'],
    ['2.2', path.join(PLUGIN, 'client.js'), 'dsh-game-control/client.js'],
  ];
  for (const [id, file, label] of targets) {
    check(id, `node --check ${label}`, () => {
      const result = runCaptured(process.execPath, ['--check', file], { timeoutMs: 60_000 });
      if (result.error) throw new Error(`启动 node 失败：${result.error.code ?? ''} ${result.error.message}`);
      assert(
        result.status === 0,
        `node --check 退出码 ${result.status}：${oneLine(result.stderr || result.stdout, 500)}`,
      );
      return `语法 OK（${process.version}）`;
    });
  }
}

/* ------------------------------------------------------------------ */
/* 3. package.json / cordis.patch.yml                                  */
/* ------------------------------------------------------------------ */

const manifestState = { manifest: null, patchFile: null };

function checkManifest() {
  section('3. package.json 与 bundle 补丁（SPEC §6.3）');

  check('3.1', 'package.json 字段（含 BOM 检查）', () => {
    const file = path.join(PLUGIN, 'package.json');
    assertFile(file, 'dsh-game-control/package.json');
    const bytes = fs.readFileSync(file);
    assert(
      !(bytes.length >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf),
      'package.json 带 UTF-8 BOM：DSH 用 JSON.parse 读 bundle 清单，会整插件加载失败',
    );
    const manifest = JSON.parse(bytes.toString('utf8'));
    manifestState.manifest = manifest;

    assert(
      manifest.name === '@local/dsh-game-control',
      `name 必须是 @local/dsh-game-control，实际：${JSON.stringify(manifest.name)}`,
    );
    assert(
      typeof manifest.version === 'string' && /^\d+\.\d+\.\d+/.test(manifest.version),
      `version 不合法：${JSON.stringify(manifest.version)}`,
    );
    assert(manifest.type === 'module', 'type 必须是 module（ESM）');

    const patch = manifest?.dsh?.bundle?.patch;
    assert(typeof patch === 'string' && patch.trim() !== '', '缺少 dsh.bundle.patch');
    const patchFile = path.resolve(PLUGIN, patch);
    assertFile(patchFile, `dsh.bundle.patch 指向的文件 ${patch}`);
    manifestState.patchFile = patchFile;

    assert(manifest?.dsh?.client?.platform === 'web', "dsh.client.platform 必须是 'web'");
    assert(
      manifest?.exports && typeof manifest.exports === 'object' && manifest.exports['./client'],
      "exports['./client'] 缺失",
    );
    assert(typeof manifest.main === 'string' && manifest.main !== '', '缺少 main 字段');
    assertFile(path.resolve(PLUGIN, manifest.main), 'main 指向的文件');

    return `name=${manifest.name} version=${manifest.version} patch=${patch}`;
  });

  check('3.2', 'cordis.patch.yml 内容（Loader 挂载行）', () => {
    const file = manifestState.patchFile ?? path.join(PLUGIN, 'cordis.patch.yml');
    const text = readText(file, 'cordis.patch.yml');
    for (const token of ['insert', 'game-control', '@local/dsh-game-control']) {
      assert(text.includes(token), `cordis.patch.yml 缺少 ${JSON.stringify(token)}`);
    }
    return '含 insert 行 + id=game-control + 包名';
  });

  check('3.3', 'package.json 版本为 0.6.2（SPEC §13.4）', () => {
    const manifest =
      manifestState.manifest ??
      JSON.parse(readText(path.join(PLUGIN, 'package.json'), 'dsh-game-control/package.json'));
    assert(
      manifest.version === '0.6.2',
      `版本应为 0.6.2（SPEC §13.4），实际 ${JSON.stringify(manifest.version)}`,
    );
    return `version=${manifest.version}`;
  });
}

/* ------------------------------------------------------------------ */
/* 4. locale / icon.svg                                                */
/* ------------------------------------------------------------------ */

function checkLocaleAndIcon() {
  section('4. locale 与图标（SPEC §6.4）');

  for (const [id, lang] of [
    ['4.1', 'zh'],
    ['4.2', 'en'],
  ]) {
    check(id, `locale/${lang}.json 可解析且 meta.title/meta.description 非空`, () => {
      const file = path.join(PLUGIN, 'locale', `${lang}.json`);
      const text = readText(file, `locale/${lang}.json`);
      let dict;
      try {
        dict = JSON.parse(text);
      } catch (error) {
        throw new Error(`JSON 解析失败：${error.message}`);
      }
      for (const key of ['meta.title', 'meta.description']) {
        assert(
          typeof dict[key] === 'string' && dict[key].trim() !== '',
          `缺少非空字符串字段 ${key}`,
        );
      }
      return `${dict['meta.title']} / ${dict['meta.description']}`;
    });
  }

  check('4.3', 'icon.svg 以 <svg 开头且含 viewBox', () => {
    const file = path.join(PLUGIN, 'icon.svg');
    const text = readText(file, 'icon.svg');
    assert(text.trimStart().startsWith('<svg'), 'icon.svg 必须以内联 <svg 开头（不是外链图片）');
    assert(text.includes('viewBox'), 'icon.svg 缺少 viewBox');
    return `${fmtBytes(fs.statSync(file).size)}`;
  });
}

/* ------------------------------------------------------------------ */
/* 5. Python 后端（SPEC §6.5）                                         */
/* ------------------------------------------------------------------ */

/** @type {{cmd: string, args: string[], version: string, source: string} | null} */
let python = null;
const pyEnv = {
  ...process.env,
  GAME_CONTROL_HOME: HOME_DIR,
  PYTHONIOENCODING: 'utf-8',
  PYTHONUNBUFFERED: '1',
  PYTHONDONTWRITEBYTECODE: '1',
};

/**
 * Python 段全部检查项（id / 名称）。没有 Python 解释器、或前置 status 失败时，
 * 按这一份清单批量记 SKIP，保证编号与逐条输出一致。
 */
const PYTHON_CHECKS = [
  ['5.1', '--version 版本 JSON'],
  ['5.2', '--selftest 只读自检'],
  ['5.3', 'Python 语法检查（compile 全部 .py，不写 pycache）'],
  ['5.4', '源码不得含联网 import（socket/urllib/http/requests/websocket）'],
  ['5.5', '--json 单次 CLI：status 合法几何'],
  ['5.6', '--stdin 流模式批量请求（SPEC §2.1 JSONL）'],
  ['5.7', '全屏截图：PNG 落盘 + 尺寸正确'],
  ['5.8', '区域截图 200×120：尺寸精确'],
  ['5.9', 'base64 截图：可解码为 PNG'],
  ['5.10', '部分越界区域：自动裁剪不报错'],
  ['5.11', '错误码：非法 action → UNKNOWN_ACTION'],
  ['5.12', '错误码：非法 key → INVALID_KEY'],
  ['5.13', '错误码：非法 region（width=0）→ INVALID_REGION'],
  ['5.14', '错误码：完全屏外区域 → OUT_OF_BOUNDS'],
  ['5.15', '错误码：未知参数 → UNKNOWN_PARAM'],
  ['5.16', '错误码：非法参数类型 → INVALID_ARGUMENT'],
  ['5.17', '无副作用审计：未执行任何鼠标/键盘输入动作'],
  ['5.18', '日志与审计文件（SPEC §2.6）'],
  ['5.19', '--serve 常驻 worker：ready 事件 + id 回显'],
  ['5.20', 'list_windows：返回 count>0 且字段合法（hwnd/title/rect/minimized/visible）'],
  ['5.21', 'capture_window 缺 window/hwnd → INVALID_ARGUMENT'],
  ['5.22', 'capture_window 不存在的窗口 → WINDOW_NOT_FOUND'],
  ['5.23', 'capture_window engine=screen：PNG 落盘 + 尺寸>0（含 engine/window 字段）'],
  ['5.24', 'capture_window 按标题子串（window 参数）选窗'],
  ['5.25', 'list_windows titleContains 过滤生效（大小写不敏感）'],
  ['5.26', 'capture_window 非法 engine → INVALID_ARGUMENT'],
  ['5.27', '窗口动作审计：无输入副作用'],
  ['5.28', 'Python ACTION_PARAMS/ACTION_HANDLERS 含 5 个新动作 + 7 个 skill_* 动作（SPEC §8.2/§8.3）'],
  ['5.29', 'mouse_circle dryRun:true 返回 samples/不含输入（SPEC §8.2.2）'],
  ['5.30', 'find_image 不存在的模板 → TEMPLATE_NOT_FOUND'],
  ['5.31', 'find_image 损坏模板 → INVALID_TEMPLATE'],
  ['5.32', 'wait_image 合成模板 + 短超时 → timedOut:true（只读截图）'],
  ['5.33', '技能库 skill_save → skill_list → skill_get 基线流程'],
  ['5.34', '技能库：更新自动备份 → skill_versions → skill_restore 回滚'],
  ['5.35', '技能库 skill_run：dryRun:true 与仅 wait(0) 技能真执行（零输入）'],
  ['5.36', '技能库软删除 + SKILL_NOT_FOUND / INVALID_SKILL'],
  ['5.37', '--selftest 含 v0.3 新能力检查（numpy / 模板目录 / 中止键）'],
  ['5.38', 'v0.4 结构化保存 → 技能文件为 JSONC（// 第 N 步 + 显式 waitMs + 归一化默认参数）'],
  ['5.39', 'v0.4 skill_get 返回 content/stepsView（content 与磁盘一致，comment 与注释同文）'],
  ['5.40', 'v0.4 content 模式保存：注释重新生成、note 保留、数据不变（返回 content 可刷新编辑器）'],
  ['5.41', 'v0.4 非法 content / 未知步骤字段 → INVALID_SKILL'],
  ['5.42', 'v0.4 旧纯 JSON 技能兼容 + JSONC 技能 skill_run dryRun 通过'],
  ['5.43', 'v0.4 版本恢复保留历史 JSONC 文本（恢复后 content 仍含中文注释）'],
  ['5.44', 'v0.5 mouse_circle dryRun 默认 abortKey=esc（不注入输入）'],
  ['5.45', 'v0.5 templates 多模板参数校验（template 同给 / 超过 8 个 / 空数组 → INVALID_ARGUMENT）'],
  ['5.46', 'v0.5 templates 多模板：缺失文件 → TEMPLATE_NOT_FOUND，1 个合法模板可正常查找'],
  ['5.47', 'v0.5 skill_run dryRun 返回 estimatedDurationMs + 时长前置校验（超 timeoutMs → INVALID_ARGUMENT）'],
  ['5.48', 'v0.5 skill_progress 动作 + logs/skill-progress.json 结构（运行结束 running=false）'],
  ['5.49', 'v0.6.1 CLI 兼容 skill_run.repeat（面板/工具 schema 已不暴露，Python CLI 仍可用）'],
];

function discoverPython() {
  /** @type {{cmd: string, args: string[], source: string}[]} */
  const candidates = [];
  for (const key of ['DSH_GAME_CONTROL_PYTHON', 'GAME_CONTROL_PYTHON']) {
    const value = process.env[key];
    if (typeof value === 'string' && value.trim() !== '') {
      candidates.push({ cmd: value.trim(), args: [], source: `环境变量 ${key}` });
    }
  }

  const runtimesDir = path.join(os.homedir(), '.dsh', 'dsh-runtimes');
  let entries = [];
  try {
    entries = fs.readdirSync(runtimesDir, { withFileTypes: true });
  } catch {
    /* 没有 DSH 运行时目录是正常情况 */
  }
  for (const entry of entries.sort((a, b) => a.name.localeCompare(b.name))) {
    if (!entry.isDirectory()) continue;
    const exe =
      process.platform === 'win32'
        ? path.join(runtimesDir, entry.name, 'dependencies', 'python', 'python.exe')
        : path.join(runtimesDir, entry.name, 'dependencies', 'python', 'bin', 'python3');
    if (fs.existsSync(exe)) {
      candidates.push({ cmd: exe, args: [], source: `DSH 内置 Python（dsh-runtimes/${entry.name}）` });
    }
  }

  for (const name of ['python3', 'python']) {
    candidates.push({ cmd: name, args: [], source: `PATH ${name}` });
  }
  if (process.platform === 'win32') {
    candidates.push({ cmd: 'py', args: ['-3'], source: 'PATH py -3' });
  }

  const tried = [];
  for (const candidate of candidates) {
    const result = runCaptured(candidate.cmd, [...candidate.args, '--version'], { timeoutMs: 8000 });
    const text = `${result.stdout || ''}${result.stderr || ''}`;
    const match = text.match(/python\s+(\d+\.\d+\.\d+)/i);
    if (result.error || result.status !== 0 || !match) {
      const reason = result.error ? `(${result.error.code ?? result.error.message})` : `(exit ${result.status})`;
      tried.push(`${candidate.source} ${reason}`);
      continue;
    }
    return { ...candidate, version: match[1], tried };
  }
  return { tried };
}

function runPython(args, options = {}) {
  return runCaptured(python.cmd, [...python.args, ...args], {
    cwd: options.cwd ?? ROOT,
    env: { ...pyEnv, ...(options.env ?? {}) },
    timeoutMs: options.timeoutMs ?? 60_000,
    input: options.input,
  });
}

function parseJsonLines(text, label) {
  const lines = String(text || '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line !== '');
  assert(lines.length > 0, `${label}：stdout 没有任何 JSON 行`);
  return lines.map((line, index) => {
    try {
      return JSON.parse(line);
    } catch (error) {
      throw new Error(`${label}：第 ${index + 1} 行不是合法 JSON：${oneLine(line, 300)}（${error.message}）`);
    }
  });
}

function parseSingleEnvelope(result, label) {
  if (result.error) throw new Error(`${label}：启动 Python 失败（${result.error.message}）`);
  const envelopes = parseJsonLines(result.stdout, label);
  assert(envelopes.length === 1, `${label}：期望恰好 1 行 JSON，实际 ${envelopes.length} 行`);
  return envelopes[0];
}

function checkGeometry(status, label) {
  assert(status && status.ok === true, `${label}：status 返回 ok!==true：${oneLine(JSON.stringify(status?.error), 300)}`);
  const result = status.result || {};
  for (const key of ['virtualScreen', 'primary']) {
    const rect = result[key];
    assert(rect && typeof rect === 'object', `${label}：缺少 ${key}`);
    for (const field of ['x', 'y', 'width', 'height']) {
      assert(Number.isFinite(rect[field]), `${label}：${key}.${field} 不是数字`);
    }
    assert(rect.width > 0 && rect.height > 0, `${label}：${key} 宽高必须 > 0（实际 ${rect.width}x${rect.height}）`);
  }
  assert(Array.isArray(result.displays) && result.displays.length >= 1, `${label}：displays 必须是非空数组`);
  assert(
    result.pointer && Number.isFinite(result.pointer.x) && Number.isFinite(result.pointer.y),
    `${label}：pointer 坐标不合法`,
  );
  for (const key of ['platform', 'pythonVersion', 'backend', 'shotDir', 'logDir']) {
    assert(typeof result[key] === 'string' && result[key] !== '', `${label}：${key} 必须是非空字符串`);
  }
  assert(typeof result.dpiAware === 'boolean', `${label}：dpiAware 必须是布尔`);
  return result;
}

const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

function assertPngFile(file, label) {
  assertFile(file, label);
  const head = fs.readFileSync(file).subarray(0, 8);
  assert(head.equals(PNG_MAGIC), `${label}：文件头不是 PNG 魔数`);
  return fs.statSync(file).size;
}

/** 从 list_windows 结果里挑一个适合做「窗口截图」的目标（可见、非最小化、有标题、有面积）。 */
function pickCaptureTarget(windows) {
  if (!Array.isArray(windows)) return null;
  const usable = windows.filter((item) => {
    if (!item || typeof item !== 'object') return false;
    if (item.visible !== true || item.minimized !== false) return false;
    if (typeof item.title !== 'string' || item.title.trim() === '') return false;
    const rect = item.rect;
    return (
      rect &&
      ['x', 'y', 'width', 'height'].every((key) => Number.isFinite(rect[key])) &&
      rect.width > 0 &&
      rect.height > 0
    );
  });
  usable.sort((a, b) => {
    const aForeground = a.foreground === true ? 1 : 0;
    const bForeground = b.foreground === true ? 1 : 0;
    if (aForeground !== bForeground) return bForeground - aForeground;
    return b.rect.width * b.rect.height - a.rect.width * a.rect.height;
  });
  return usable[0] ?? null;
}

/** 取窗口标题的一段安全子串（按码点切，避免截断代理对），用于 titleContains 用例。 */
function titleSubstring(title) {
  const chars = Array.from(String(title || '').trim());
  return chars.slice(0, Math.min(6, chars.length)).join('');
}

/** 用 Python + Pillow 生成一张纯色 PNG（v0.3：合成模板，用于 find_image / wait_image 测试）。 */
function createSyntheticPng(file, width, height, color) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const code = [
    'import sys',
    'from PIL import Image',
    'Image.new("RGB", (int(sys.argv[2]), int(sys.argv[3])), sys.argv[4]).save(sys.argv[1])',
  ].join('\n');
  const result = runCaptured(
    python.cmd,
    [...python.args, '-c', code, file, String(width), String(height), color],
    { env: pyEnv, timeoutMs: 30_000 },
  );
  assert(result.status === 0, `生成合成模板 PNG 失败（需要 Pillow）：${oneLine(result.stderr, 300)}`);
  assertFile(file, '合成模板 PNG');
  return file;
}

/**
 * v0.4：剥离 JSONC 的 `//` 行注释与 `/* *\/` 块注释（字符串内的 // 不能误伤）。
 * 只用于验收断言：把技能文件去掉注释后应能 JSON.parse。
 */
function stripJsoncComments(text) {
  let output = '';
  let index = 0;
  let inString = false;
  while (index < text.length) {
    const ch = text[index];
    const next = text[index + 1];
    if (inString) {
      output += ch;
      if (ch === '\\') {
        output += text[index + 1] ?? '';
        index += 2;
        continue;
      }
      if (ch === '"') inString = false;
      index += 1;
      continue;
    }
    if (ch === '"') {
      inString = true;
      output += ch;
      index += 1;
      continue;
    }
    if (ch === '/' && next === '/') {
      while (index < text.length && text[index] !== '\n') index += 1;
      continue;
    }
    if (ch === '/' && next === '*') {
      index += 2;
      while (index < text.length && !(text[index] === '*' && text[index + 1] === '/')) index += 1;
      index += 2;
      continue;
    }
    output += ch;
    index += 1;
  }
  return output;
}

function networkImportScan() {
  const banned = new Set(['socket', 'urllib', 'http', 'requests', 'websocket']);
  const pattern = /^\s*(?:import|from)\s+([A-Za-z_][\w.]*)/gm;
  const offenders = [];
  let files = [];
  try {
    files = fs.readdirSync(PY_DIR).filter((name) => name.endsWith('.py'));
  } catch {
    return { files, offenders };
  }
  for (const name of files.sort()) {
    const text = fs.readFileSync(path.join(PY_DIR, name), 'utf8');
    for (const match of text.matchAll(pattern)) {
      if (banned.has(match[1].split('.')[0])) offenders.push(`${name}: ${match[1]}`);
    }
  }
  return { files, offenders };
}

function runServeProbe() {
  // 用文件描述符跑 --serve：stdin 给一条 status 请求，stdout/stderr 落文件后校验。
  // 不依赖管道流式读取，因此在 DSH 沙箱里也能验证 ready 事件、id 回显与 stdout 纯 JSON。
  const result = runCaptured(python.cmd, [...python.args, CLI, '--serve'], {
    env: pyEnv,
    timeoutMs: 30_000,
    input: `${JSON.stringify({ id: 7, action: 'status' })}\n`,
  });
  if (result.error) throw new Error(`spawn Python --serve 失败：${result.error.code ?? ''} ${result.error.message}`);
  assert(result.status === 0, `--serve 退出码 ${result.status}：${oneLine(result.stderr, 400)}`);
  const envelopes = parseJsonLines(result.stdout, '--serve');
  assert(envelopes.length >= 2, `--serve 期望 ready + status 至少 2 行 JSON，实际 ${envelopes.length} 行`);
  const ready = envelopes[0];
  assert(ready && ready.ok === true && ready.event === 'ready', `--serve 第一行必须是 {ok:true,event:'ready'}：${oneLine(JSON.stringify(ready), 300)}`);
  assert(typeof ready.version === 'string' && ready.version !== '', 'ready 事件缺少 version 字段');
  const response = envelopes.find((item) => item.id === 7);
  assert(response, '--serve 未回显请求 id=7');
  assert(response.ok === true, `--serve status 返回 ok=false：${oneLine(JSON.stringify(response.error), 300)}`);
  return `ready 事件 + id=7 回显 + stdout 共 ${envelopes.length} 行纯 JSON`;
}

async function checkPython() {
  section('5. Python 后端（SPEC §6.5 / §2）');

  // 5.0 解释器发现
  const discovered = discoverPython();
  if (!discovered.cmd) {
    skip(
      '5.0',
      'Python 解释器发现',
      `未找到可用 Python（已尝试：${(discovered.tried || []).join('；') || '无候选'}）。Windows 可安装 Python 3.10+ 后重跑`,
    );
    const skipped = PYTHON_CHECKS;
    for (const [id, name] of skipped) skip(id, name, '前置条件不足：未找到 Python 解释器');
  } else {
    python = { cmd: discovered.cmd, args: discovered.args, version: discovered.version, source: discovered.source };
    const hasPillow = (() => {
      const result = runCaptured(python.cmd, [...python.args, '-c', 'import PIL; print(PIL.__version__)'], {
        env: pyEnv,
        timeoutMs: 15_000,
      });
      return { ok: result.status === 0 && !result.error, version: oneLine(result.stdout, 40) };
    })();
    const glob = path.join(ROOT, 'test', '.tmp') + path.sep;

    check('5.0', 'Python 解释器发现', () => {
      assert(python !== null, '内部错误：python 未赋值');
      assertFile(CLI, 'python/game_control.py');
      return `${python.cmd} · Python ${python.version} · ${python.source}${hasPillow.ok ? ` · Pillow ${hasPillow.version}` : ' · 无 Pillow'}`;
    });

    // 5.1 --version
    check('5.1', '--version 版本 JSON', () => {
      const result = runPython([CLI, '--version'], { timeoutMs: 20_000 });
      assert(result.status === 0, `--version 退出码 ${result.status}：${oneLine(result.stderr, 300)}`);
      const payload = parseSingleEnvelope(result, '--version');
      assert(payload.ok === true, `--version ok!==true：${oneLine(JSON.stringify(payload), 300)}`);
      assert(typeof payload.version === 'string' && payload.version !== '', '--version 缺 version 字段');
      assert(typeof payload.pythonVersion === 'string' && payload.pythonVersion !== '', '--version 缺 pythonVersion 字段');
      return `version=${payload.version} python=${payload.pythonVersion}`;
    });

    // 5.2 --selftest
    check('5.2', '--selftest 只读自检', () => {
      const result = runPython([CLI, '--selftest'], { timeoutMs: 90_000 });
      assert(result.status === 0, `--selftest 退出码 ${result.status}：${oneLine(result.stderr, 400)}`);
      const payload = parseSingleEnvelope(result, '--selftest');
      assert(payload.ok === true, `--selftest ok=false，failures=${JSON.stringify(payload.failures)}`);
      assert(Array.isArray(payload.checks) && payload.checks.length >= 5, '--selftest checks 条目过少');
      assert(
        Array.isArray(payload.failures) && payload.failures.length === 0,
        `--selftest 存在失败项：${JSON.stringify(payload.failures)}`,
      );
      return `${payload.checks.length} 项自检全部通过`;
    });

    // 5.3 语法检查（compile()，不写 __pycache__）
    check('5.3', 'Python 语法检查（compile 全部 .py，不写 pycache）', () => {
      const files = fs.readdirSync(PY_DIR).filter((name) => name.endsWith('.py'));
      assert(files.length >= 4, `python/ 下 .py 文件过少：${files.length}`);
      const code = [
        'import json, sys',
        'errors = []',
        'for name in sys.argv[1:]:',
        '    try:',
        "        compile(open(name, encoding='utf-8').read(), name, 'exec')",
        '    except SyntaxError as exc:',
        "        errors.append('%s:%s:%s' % (name, exc.lineno, exc.msg))",
        "print(json.dumps({'ok': not errors, 'errors': errors, 'count': len(sys.argv) - 1}))",
      ].join('\n');
      const result = runCaptured(
        python.cmd,
        [...python.args, '-c', code, ...files.map((name) => path.join(PY_DIR, name))],
        { env: pyEnv, timeoutMs: 30_000 },
      );
      assert(result.status === 0, `语法检查进程退出码 ${result.status}：${oneLine(result.stderr, 300)}`);
      const payload = JSON.parse(oneLine(result.stdout, 4000));
      assert(payload.ok === true, `语法错误：${JSON.stringify(payload.errors)}`);
      return `${payload.count} 个文件语法 OK`;
    });

    // 5.4 源码无联网 import（静态检查，不依赖上面的解释器）
    check('5.4', '源码不得含联网 import（socket/urllib/http/requests/websocket）', () => {
      const { files, offenders } = networkImportScan();
      assert(files.length >= 4, `python/ 下 .py 文件过少：${files.length}`);
      assert(offenders.length === 0, `发现联网 import：${offenders.join('; ')}`);
      return `扫描 ${files.length} 个 .py：无 socket/urllib/http/requests/websocket`;
    });

    // 5.5 --json 单次 CLI
    /** @type {any} */
    let statusResult = null;
    check('5.5', '--json 单次 CLI：status 合法几何', () => {
      const result = runPython([CLI, '--json', JSON.stringify({ action: 'status' })], { timeoutMs: 30_000 });
      assert(result.status === 0, `status 退出码 ${result.status}：${oneLine(result.stderr, 300)}`);
      const envelope = parseSingleEnvelope(result, '--json status');
      statusResult = checkGeometry(envelope, '--json status');
      assert(
        path.resolve(statusResult.shotDir).startsWith(path.resolve(glob)),
        `shotDir 未生效 GAME_CONTROL_HOME：${statusResult.shotDir}`,
      );
      return `virtualScreen=${statusResult.virtualScreen.width}x${statusResult.virtualScreen.height} primary=${statusResult.primary.width}x${statusResult.primary.height} (${statusResult.platform}/${statusResult.backend})`;
    });

    if (!statusResult) {
      for (const [id, name] of PYTHON_CHECKS) {
        skip(id, name, '前置 5.5（--json status）失败，无法读取屏幕几何');
      }
      return;
    }

    // 准备 test/.tmp，记录审计基线（HOME 可能残留上次运行的日志）
    fs.mkdirSync(SHOT_DIR, { recursive: true });
    fs.mkdirSync(LOG_DIR, { recursive: true });
    const auditFile = path.join(LOG_DIR, 'actions.jsonl');
    const auditBaseline = (() => {
      try {
        return fs.readFileSync(auditFile, 'utf8').split('\n').filter((line) => line.trim() !== '').length;
      } catch {
        return 0;
      }
    })();

    // 5.6 --stdin 批量会话（一次进程跑完，减少 Python 冷启动）
    /** @type {any} */
    let session = null;
    check('5.6', '--stdin 流模式批量请求（SPEC §2.1 JSONL）', () => {
      const primary = statusResult.primary;
      const virtualScreen = statusResult.virtualScreen;
      const clipRegion = {
        x: virtualScreen.x + virtualScreen.width - 50,
        y: virtualScreen.y,
        width: 200,
        height: 120,
      };
      const requests = [
        { id: 1, action: 'list_displays' },
        { id: 2, action: 'screenshot', params: { path: 'accept-full.png' } },
        { id: 3, action: 'screenshot', params: { region: { x: primary.x, y: primary.y, width: 200, height: 120 }, path: 'accept-region.png' } },
        { id: 4, action: 'screenshot', params: { region: { x: primary.x, y: primary.y, width: 320, height: 200 }, base64: true } },
        { id: 5, action: 'definitely-not-a-real-action-xyz' },
        { id: 6, action: 'key_press', params: { key: 'definitely-not-a-real-key-xyz' } },
        { id: 7, action: 'screenshot', params: { region: { x: primary.x, y: primary.y, width: 0, height: 120 } } },
        { id: 8, action: 'screenshot', params: { region: { x: 100_000_000, y: 100_000_000, width: 200, height: 120 } } },
        { id: 9, action: 'status', params: { bogusParam: 1 } },
        { id: 10, action: 'mouse_move', params: { x: 'not-a-number', y: 0 } },
        { id: 11, action: 'screenshot', params: { region: clipRegion } },
      ];
      const input = `${requests.map((request) => JSON.stringify(request)).join('\n')}\n`;
      const result = runPython([CLI, '--stdin'], { input, timeoutMs: 180_000 });
      assert(result.status === 0, `--stdin 退出码 ${result.status}：${oneLine(result.stderr, 400)}`);
      const envelopes = parseJsonLines(result.stdout, '--stdin');
      assert(
        envelopes.length === requests.length,
        `--stdin 期望 ${requests.length} 行响应，实际 ${envelopes.length} 行`,
      );
      const byId = new Map();
      for (const envelope of envelopes) {
        assert(Number.isInteger(envelope.id), `响应缺少数字 id：${oneLine(JSON.stringify(envelope), 300)}`);
        byId.set(envelope.id, envelope);
      }
      for (const request of requests) {
        assert(byId.has(request.id), `响应缺少 id=${request.id}（worker/stdin 必须回显 id）`);
      }
      session = {
        byId,
        requests,
        clips: { clipRegion },
        primary,
        virtualScreen,
        raw: envelopes,
      };
      return `${requests.length} 条请求一次会话完成，id 全部回显`;
    });

    const sessionCheck = (id, name, fn) => {
      if (!session) {
        skip(id, name, '前置 5.6（--stdin 会话）失败');
        return;
      }
      check(id, name, fn);
    };

    const getResult = (envelope, label) => {
      assert(envelope.ok === true, `${label}：ok!==true（${oneLine(JSON.stringify(envelope.error), 300)}）`);
      assert(envelope.result && typeof envelope.result === 'object', `${label}：缺少 result`);
      return envelope.result;
    };

    // 5.7 全屏截图
    sessionCheck('5.7', '全屏截图：PNG 落盘 + 尺寸正确', () => {
      const result = getResult(session.byId.get(2), '全屏截图');
      const primary = session.primary;
      assert(result.path === 'accept-full.png', `相对 path 未按传入值返回：${result.path}`);
      assert(
        result.width === primary.width && result.height === primary.height,
        `全屏尺寸应为主屏 ${primary.width}x${primary.height}，实际 ${result.width}x${result.height}`,
      );
      const file = result.absolutePath;
      assert(
        path.resolve(file).startsWith(path.resolve(SHOT_DIR)),
        `截图未落到 GAME_CONTROL_HOME/shots：${file}`,
      );
      const size = assertPngFile(file, '全屏截图文件');
      assert(result.bytes === size, `返回 bytes=${result.bytes} 与文件实际大小 ${size} 不一致`);
      assert(result.format === 'png', `format 应为 png，实际 ${result.format}`);
      return `${result.width}x${result.height} PNG ${fmtBytes(size)} → ${file}`;
    });

    // 5.8 区域截图
    sessionCheck('5.8', '区域截图 200×120：尺寸精确', () => {
      const result = getResult(session.byId.get(3), '区域截图');
      assert(result.width === 200 && result.height === 120, `区域尺寸应为 200x120，实际 ${result.width}x${result.height}`);
      assertPngFile(result.absolutePath, '区域截图文件');
      return `200x120 PNG ${fmtBytes(result.bytes)} → ${result.absolutePath}`;
    });

    // 5.9 base64
    sessionCheck('5.9', 'base64 截图：可解码为 PNG', () => {
      const result = getResult(session.byId.get(4), 'base64 截图');
      assert(typeof result.base64 === 'string' && result.base64.length > 100, 'base64 字段缺失或过短');
      assert(
        Number.isInteger(result.base64Length) && result.base64Length === result.base64.length,
        `base64Length=${result.base64Length} 与 base64 长度 ${result.base64.length} 不一致`,
      );
      const decoded = Buffer.from(result.base64, 'base64');
      assert(decoded.subarray(0, 8).equals(PNG_MAGIC), 'base64 解码后不是 PNG 魔数');
      assert(
        /^shot_\d{8}_\d{6}_\d{3}\.(png|jpg)$/.test(String(result.path)),
        `未指定 path 时应使用默认文件名 shot_YYYYMMDD_HHMMSS_fff.png，实际：${result.path}`,
      );
      assertFile(result.absolutePath, 'base64 截图落盘文件');
      return `base64Length=${result.base64Length}，解码 ${fmtBytes(decoded.length)}，默认文件名 ${result.path}`;
    });

    // 5.10 部分越界裁剪
    sessionCheck('5.10', '部分越界区域：自动裁剪不报错', () => {
      const result = getResult(session.byId.get(11), '部分越界区域截图');
      assert(result.width > 0 && result.height > 0, `裁剪后尺寸非法：${result.width}x${result.height}`);
      assert(
        result.width < 200,
        `区域仅右侧 50px 在虚拟桌面内，width 应被裁剪到 < 200，实际 ${result.width}`,
      );
      assertPngFile(result.absolutePath, '裁剪区域截图文件');
      return `请求 200x120，裁剪为 ${result.width}x${result.height}`;
    });

    const expectError = (envelope, code, label) => {
      assert(envelope && envelope.ok === false, `${label}：期望 ok=false，实际 ok=${envelope?.ok}`);
      assert(envelope.error && typeof envelope.error === 'object', `${label}：缺少 error 对象`);
      assert(
        envelope.error.code === code,
        `${label}：期望错误码 ${code}，实际 ${JSON.stringify(envelope.error.code)}（message=${oneLine(envelope.error.message, 200)}）`,
      );
      assert(
        typeof envelope.error.message === 'string' && envelope.error.message !== '',
        `${label}：error.message 不能为空`,
      );
    };

    sessionCheck('5.11', '错误码：非法 action → UNKNOWN_ACTION', () => {
      expectError(session.byId.get(5), 'UNKNOWN_ACTION', '非法 action');
      return 'code=UNKNOWN_ACTION';
    });

    sessionCheck('5.12', '错误码：非法 key → INVALID_KEY', () => {
      expectError(session.byId.get(6), 'INVALID_KEY', '非法 key');
      return 'code=INVALID_KEY';
    });

    sessionCheck('5.13', '错误码：非法 region（width=0）→ INVALID_REGION', () => {
      expectError(session.byId.get(7), 'INVALID_REGION', 'region width=0');
      return 'code=INVALID_REGION';
    });

    sessionCheck('5.14', '错误码：完全屏外区域 → OUT_OF_BOUNDS', () => {
      expectError(session.byId.get(8), 'OUT_OF_BOUNDS', '屏外 region');
      return 'code=OUT_OF_BOUNDS';
    });

    sessionCheck('5.15', '错误码：未知参数 → UNKNOWN_PARAM', () => {
      expectError(session.byId.get(9), 'UNKNOWN_PARAM', '未知参数');
      return 'code=UNKNOWN_PARAM';
    });

    sessionCheck('5.16', '错误码：非法参数类型 → INVALID_ARGUMENT', () => {
      expectError(session.byId.get(10), 'INVALID_ARGUMENT', 'x 传字符串');
      return 'code=INVALID_ARGUMENT';
    });

    // 5.17 无副作用审计：新增审计行里不得有任何成功的输入动作
    sessionCheck('5.17', '无副作用审计：未执行任何鼠标/键盘输入动作', () => {
      const lines = fs
        .readFileSync(auditFile, 'utf8')
        .split('\n')
        .filter((line) => line.trim() !== '')
        .slice(auditBaseline);
      assert(lines.length > 0, '本次会话后 actions.jsonl 没有新增审计行');
      const allowed = new Set(['status', 'list_displays', 'screenshot']);
      const entries = lines.map((line, index) => {
        try {
          return JSON.parse(line);
        } catch (error) {
          throw new Error(`actions.jsonl 第 ${auditBaseline + index + 1} 行不是合法 JSON：${oneLine(line, 200)}`);
        }
      });
      const bad = entries.filter((entry) => entry.ok === true && !allowed.has(entry.action));
      assert(
        bad.length === 0,
        `审计日志出现「成功执行」的输入动作，验收必须无副作用：${JSON.stringify(bad.slice(0, 5))}`,
      );
      assert(
        entries.some((entry) => entry.ok === true && entry.action === 'screenshot'),
        '审计日志里没有成功的 screenshot 记录（审计功能可能失效）',
      );
      return `新增 ${entries.length} 行，成功动作仅 ${[...new Set(entries.filter((e) => e.ok === true).map((e) => e.action))].join('/')}`;
    });

    // 5.18 日志与审计
    sessionCheck('5.18', '日志与审计文件（SPEC §2.6）', () => {
      const logFile = path.join(LOG_DIR, 'game-control.log');
      const logSize = assertFile(logFile, 'logs/game-control.log').size;
      const auditText = fs.readFileSync(auditFile, 'utf8');
      const lines = auditText.split('\n').filter((line) => line.trim() !== '');
      assert(lines.length > 0, 'actions.jsonl 为空');
      for (const [index, line] of lines.entries()) {
        let entry;
        try {
          entry = JSON.parse(line);
        } catch {
          throw new Error(`actions.jsonl 第 ${index + 1} 行不是合法 JSON：${oneLine(line, 200)}`);
        }
        assert(typeof entry.ts === 'string' && entry.ts !== '', `actions.jsonl 第 ${index + 1} 行缺 ts`);
        assert(typeof entry.action === 'string' && entry.action !== '', `actions.jsonl 第 ${index + 1} 行缺 action`);
        assert(typeof entry.ok === 'boolean', `actions.jsonl 第 ${index + 1} 行 ok 不是布尔`);
      }
      assert(
        !auditText.includes(PNG_MAGIC.toString('base64').slice(0, 12)),
        'actions.jsonl 疑似记录了 base64 内容（审计必须只记元数据）',
      );
      return `game-control.log ${fmtBytes(logSize)}，actions.jsonl ${lines.length} 行全部为合法 JSON`;
    });

    // 5.19 --serve 常驻 worker
    try {
      const detail = await runServeProbe();
      emit('PASS', '5.19', '--serve 常驻 worker：ready 事件 + id 回显', detail);
    } catch (error) {
      emit('FAIL', '5.19', '--serve 常驻 worker：ready 事件 + id 回显', (error && error.message) || error);
    }

    /* ------------------------------------------------------------------ *
     * v0.2：窗口枚举与后台窗口截图（SPEC §2.3.1）
     * 只测无副作用路径：list_windows / capture_window engine=screen / 参数错误。
     * 不测 WGC——DSH 沙箱内 WGC 可能不可用，真后台路径由 Lead 在未受限环境验收。
     * ------------------------------------------------------------------ */
    const platformName = String(statusResult.platform || '').toLowerCase();
    if (platformName !== 'windows') {
      for (const [id, name] of PYTHON_CHECKS) {
        if (Number(id.split('.')[1]) >= 20) {
          skip(id, name, `当前平台 ${statusResult.platform} 不支持窗口枚举/后台窗口截图（SPEC §2.3.1 仅 Windows）`);
        }
      }
    } else {
      /** @type {any} */
      let targetWindow = null;

      check('5.20', 'list_windows：返回 count>0 且字段合法（hwnd/title/rect/minimized/visible）', () => {
        const result = runPython(
          [CLI, '--json', JSON.stringify({ action: 'list_windows', params: { limit: 50 } })],
          { timeoutMs: 30_000 },
        );
        assert(result.status === 0, `list_windows 退出码 ${result.status}：${oneLine(result.stderr, 400)}`);
        const data = getResult(parseSingleEnvelope(result, 'list_windows'), 'list_windows');
        assert(Number.isInteger(data.count) && data.count > 0, `count 必须是正整数，实际 ${JSON.stringify(data.count)}`);
        assert(Array.isArray(data.windows) && data.windows.length > 0, 'windows 必须是非空数组');
        assert(
          data.count === data.windows.length,
          `count=${data.count} 与 windows.length=${data.windows.length} 不一致`,
        );
        for (const [index, item] of data.windows.entries()) {
          const label = `windows[${index}]`;
          assert(Number.isInteger(item.hwnd) && item.hwnd > 0, `${label}.hwnd 必须是正整数`);
          assert(typeof item.title === 'string', `${label}.title 必须是字符串`);
          assert(
            item.rect && ['x', 'y', 'width', 'height'].every((key) => Number.isFinite(item.rect[key])),
            `${label}.rect 必须是 {x,y,width,height} 数字`,
          );
          assert(typeof item.minimized === 'boolean', `${label}.minimized 必须是布尔`);
          assert(typeof item.visible === 'boolean', `${label}.visible 必须是布尔`);
          if (item.foreground !== undefined) assert(typeof item.foreground === 'boolean', `${label}.foreground 必须是布尔`);
          if (item.cloaked !== undefined) {
            assert(
              typeof item.cloaked === 'boolean' || typeof item.cloaked === 'number',
              `${label}.cloaked 必须是布尔/数字`,
            );
          }
          if (item.class !== undefined) assert(typeof item.class === 'string', `${label}.class 必须是字符串`);
          if (item.pid !== undefined) assert(Number.isInteger(item.pid), `${label}.pid 必须是整数`);
          if (item.process !== undefined) assert(typeof item.process === 'string', `${label}.process 必须是字符串`);
        }
        targetWindow = pickCaptureTarget(data.windows);
        assert(targetWindow !== null, '没有可用于 capture_window 的可见、非最小化、带标题窗口');
        return `${data.count} 个窗口；目标 "${oneLine(targetWindow.title, 60)}" hwnd=${targetWindow.hwnd} ${targetWindow.rect.width}x${targetWindow.rect.height}`;
      });

      check('5.21', 'capture_window 缺 window/hwnd → INVALID_ARGUMENT', () => {
        const result = runPython(
          [CLI, '--json', JSON.stringify({ action: 'capture_window', params: {} })],
          { timeoutMs: 30_000 },
        );
        assert(result.status === 0, `capture_window 退出码 ${result.status}：${oneLine(result.stderr, 300)}`);
        expectError(parseSingleEnvelope(result, 'capture_window {}'), 'INVALID_ARGUMENT', 'capture_window 缺窗口参数');
        return 'code=INVALID_ARGUMENT';
      });

      check('5.22', 'capture_window 不存在的窗口 → WINDOW_NOT_FOUND', () => {
        const result = runPython(
          [
            CLI,
            '--json',
            JSON.stringify({
              action: 'capture_window',
              params: { window: 'dgc-acceptance-no-such-window-xyz-2026' },
            }),
          ],
          { timeoutMs: 40_000 },
        );
        assert(result.status === 0, `capture_window 退出码 ${result.status}：${oneLine(result.stderr, 300)}`);
        expectError(parseSingleEnvelope(result, 'capture_window 不存在窗口'), 'WINDOW_NOT_FOUND', '不存在的窗口');
        return 'code=WINDOW_NOT_FOUND';
      });

      const windowAuditBaseline = (() => {
        try {
          return fs.readFileSync(auditFile, 'utf8').split('\n').filter((line) => line.trim() !== '').length;
        } catch {
          return 0;
        }
      })();

      /** @type {any} */
      let windowSession = null;
      if (targetWindow === null) {
        for (const [id, name] of PYTHON_CHECKS) {
          if (Number(id.split('.')[1]) >= 23) skip(id, name, '前置 5.20 未找到可抓取的目标窗口');
        }
      } else {
        const titleSub = titleSubstring(targetWindow.title);
        const windowSessionCheck = (id, name, fn) => {
          if (!windowSession) {
            skip(id, name, '前置 5.23（窗口用例 --stdin 会话）失败');
            return;
          }
          check(id, name, fn);
        };

        check('5.23', 'capture_window engine=screen：PNG 落盘 + 尺寸>0（含 engine/window 字段）', () => {
          const requests = [
            {
              id: 1,
              action: 'capture_window',
              params: { hwnd: targetWindow.hwnd, engine: 'screen', path: 'accept-window.png' },
            },
            {
              id: 2,
              action: 'capture_window',
              params: { window: titleSub, engine: 'screen', path: 'accept-window-title.png' },
            },
            { id: 3, action: 'list_windows', params: { titleContains: titleSub, limit: 10 } },
            {
              id: 4,
              action: 'capture_window',
              params: { hwnd: targetWindow.hwnd, engine: 'definitely-not-a-valid-engine' },
            },
          ];
          const input = `${requests.map((request) => JSON.stringify(request)).join('\n')}\n`;
          const result = runPython([CLI, '--stdin'], { input, timeoutMs: 180_000 });
          assert(result.status === 0, `窗口用例 --stdin 退出码 ${result.status}：${oneLine(result.stderr, 500)}`);
          const envelopes = parseJsonLines(result.stdout, '窗口用例 --stdin');
          assert(
            envelopes.length === requests.length,
            `窗口用例期望 ${requests.length} 行响应，实际 ${envelopes.length} 行`,
          );
          const byId = new Map();
          for (const envelope of envelopes) {
            assert(Number.isInteger(envelope.id), `响应缺少数字 id：${oneLine(JSON.stringify(envelope), 300)}`);
            byId.set(envelope.id, envelope);
          }
          for (const request of requests) {
            assert(byId.has(request.id), `响应缺少 id=${request.id}`);
          }
          windowSession = { byId, titleSub };

          const data = getResult(byId.get(1), 'capture_window hwnd+engine=screen');
          assert(
            data.engine === 'screen',
            `显式 engine=screen 时 result.engine 必须为 screen，实际 ${JSON.stringify(data.engine)}`,
          );
          const win = data.window || {};
          assert(Number.isInteger(win.hwnd) && win.hwnd > 0, 'result.window.hwnd 必须是正整数');
          assert(typeof win.title === 'string' && win.title.trim() !== '', 'result.window.title 不能为空');
          assert(
            win.rect && ['x', 'y', 'width', 'height'].every((key) => Number.isFinite(win.rect[key])),
            'result.window.rect 字段非法',
          );
          assert(data.path === 'accept-window.png', `传入相对 path 应原样返回，实际 ${JSON.stringify(data.path)}`);
          assert(
            path.resolve(data.absolutePath).startsWith(path.resolve(SHOT_DIR)),
            `截图未落到 GAME_CONTROL_HOME/shots：${data.absolutePath}`,
          );
          assert(
            Number.isInteger(data.width) && data.width > 0 && Number.isInteger(data.height) && data.height > 0,
            `截图尺寸必须 > 0，实际 ${data.width}x${data.height}`,
          );
          assert(data.format === 'png', `format 应为 png，实际 ${JSON.stringify(data.format)}`);
          const size = assertPngFile(data.absolutePath, 'capture_window 截图文件');
          assert(data.bytes === size, `返回 bytes=${data.bytes} 与实际文件大小 ${size} 不一致`);
          if (data.warnings !== undefined) {
            assert(
              Array.isArray(data.warnings) && data.warnings.every((item) => typeof item === 'string'),
              'warnings 必须是字符串数组',
            );
          }
          if (data.fallbackReason !== undefined) {
            assert(typeof data.fallbackReason === 'string', 'fallbackReason 必须是字符串');
          }
          return `engine=screen，${data.width}x${data.height} PNG ${fmtBytes(size)}，window="${oneLine(win.title, 60)}"`;
        });

        windowSessionCheck('5.24', 'capture_window 按标题子串（window 参数）选窗', () => {
          const data = getResult(windowSession.byId.get(2), 'capture_window window=标题子串');
          assert(data.engine === 'screen', `显式 engine=screen 时 result.engine 必须为 screen，实际 ${JSON.stringify(data.engine)}`);
          const title = String(data.window?.title ?? '');
          assert(
            title.toLowerCase().includes(windowSession.titleSub.toLowerCase()),
            `返回窗口标题 ${JSON.stringify(title)} 不含过滤子串 ${JSON.stringify(windowSession.titleSub)}`,
          );
          assert(
            Number.isInteger(data.width) && data.width > 0 && Number.isInteger(data.height) && data.height > 0,
            `截图尺寸必须 > 0，实际 ${data.width}x${data.height}`,
          );
          assertPngFile(data.absolutePath, '按标题抓取的窗口截图文件');
          return `window="${oneLine(title, 60)}"，engine=screen，${data.width}x${data.height}`;
        });

        windowSessionCheck('5.25', 'list_windows titleContains 过滤生效（大小写不敏感）', () => {
          const data = getResult(windowSession.byId.get(3), 'list_windows titleContains');
          assert(Array.isArray(data.windows) && data.windows.length >= 1, 'titleContains 至少应匹配 1 个窗口');
          assert(data.count === data.windows.length, `count=${data.count} 与 windows.length=${data.windows.length} 不一致`);
          for (const [index, item] of data.windows.entries()) {
            assert(
              String(item.title).toLowerCase().includes(windowSession.titleSub.toLowerCase()),
              `windows[${index}].title=${JSON.stringify(item.title)} 不含过滤子串`,
            );
          }
          return `${data.count} 个窗口匹配「${oneLine(windowSession.titleSub, 40)}」`;
        });

        windowSessionCheck('5.26', 'capture_window 非法 engine → INVALID_ARGUMENT', () => {
          expectError(windowSession.byId.get(4), 'INVALID_ARGUMENT', 'capture_window 非法 engine');
          return 'code=INVALID_ARGUMENT';
        });

        windowSessionCheck('5.27', '窗口动作审计：无输入副作用', () => {
          const lines = fs
            .readFileSync(auditFile, 'utf8')
            .split('\n')
            .filter((line) => line.trim() !== '')
            .slice(windowAuditBaseline);
          assert(lines.length > 0, '窗口会话后 actions.jsonl 没有新增审计行');
          const entries = lines.map((line, index) => {
            try {
              return JSON.parse(line);
            } catch {
              throw new Error(`actions.jsonl 第 ${windowAuditBaseline + index + 1} 行不是合法 JSON：${oneLine(line, 200)}`);
            }
          });
          const allowed = new Set(['list_windows', 'capture_window']);
          const bad = entries.filter((entry) => entry.ok === true && !allowed.has(entry.action));
          assert(
            bad.length === 0,
            `窗口动作审计出现非窗口/非只读的成功动作：${JSON.stringify(bad.slice(0, 5))}`,
          );
          assert(
            entries.some((entry) => entry.ok === true && entry.action === 'capture_window'),
            '审计日志缺少成功的 capture_window 记录',
          );
          return `新增 ${entries.length} 行，成功动作仅 list_windows/capture_window`;
        });
      }
    }

    /* ------------------------------------------------------------------ *
     * v0.3：游戏自动化动作 + 技能库（SPEC §8.2 / §8.3 / §8.5）
     * 只做只读/干跑：mouse_circle dryRun:true；find_image / wait_image 只读截图；
     * skill_* 在本次唯一 GAME_CONTROL_HOME 下做 CRUD + dryRun + 仅 wait(0) 技能真执行。
     * 绝不点击、按键、拖拽、激活窗口，也不真实画圆。
     * ------------------------------------------------------------------ */
    fs.mkdirSync(V03_DIR, { recursive: true });
    const SKILL_DIR = path.join(HOME_DIR, 'skills');

    const runCli = (request, timeoutMs = 60_000) => {
      const result = runPython([CLI, '--json', JSON.stringify(request)], { timeoutMs });
      assert(result.status === 0, `${request.action} 退出码 ${result.status}：${oneLine(result.stderr, 400)}`);
      return parseSingleEnvelope(result, request.action);
    };

    check('5.28', 'Python ACTION_PARAMS/ACTION_HANDLERS 含 5 个控制新动作 + 8 个 skill_* 动作（SPEC §8.2/§8.3/§10.4）', () => {
      const text = readText(path.join(PY_DIR, 'game_control.py'), 'python/game_control.py');
      const paramsBlock = text.match(/ACTION_PARAMS\s*=\s*\{([\s\S]*?)\n\}/);
      assert(paramsBlock, 'game_control.py 找不到 ACTION_PARAMS 字典定义');
      const handlersBlock = text.match(/ACTION_HANDLERS\s*=\s*\{([\s\S]*?)\n\}/);
      assert(handlersBlock, 'game_control.py 找不到 ACTION_HANDLERS 字典定义');
      const required = [
        'activate_window',
        'mouse_circle',
        'wait',
        'find_image',
        'wait_image',
        'skill_list',
        'skill_get',
        'skill_save',
        'skill_versions',
        'skill_restore',
        'skill_delete',
        'skill_run',
        'skill_progress',
      ];
      const missing = required.filter((name) => {
        const pattern = new RegExp("['\"]" + name + "['\"]");
        return !pattern.test(paramsBlock[1]) || !pattern.test(handlersBlock[1]);
      });
      assert(missing.length === 0, `ACTION_PARAMS/ACTION_HANDLERS 缺少：${missing.join('、')}`);
      return `${required.length} 个 v0.3 action 已在参数表与 handler 表注册`;
    });

    if (platformName === 'windows') {
      check('5.29', 'mouse_circle dryRun:true 返回 samples/不含输入（SPEC §8.2.2）', () => {
        const primary = statusResult.primary;
        const centerX = primary.x + Math.round(primary.width / 2);
        const centerY = primary.y + Math.round(primary.height / 2);
        const durationMs = 1000;
        const samplesPerSecond = 30;
        const startedLocal = Date.now();
        const data = getResult(
          runCli({
            action: 'mouse_circle',
            params: {
              centerX,
              centerY,
              radius: 100,
              periodMs: 2500,
              durationMs,
              direction: 'cw',
              samplesPerSecond,
              dryRun: true,
            },
          }),
          'mouse_circle dryRun',
        );
        const expectedSamples = Math.floor((durationMs / 1000) * samplesPerSecond) + 1;
        assert(
          data.samples === expectedSamples,
          `samples 应为 floor(duration/1000*sps)+1=${expectedSamples}，实际 ${JSON.stringify(data.samples)}`,
        );
        assert(data.aborted === false, `dryRun 不应被中止，实际 aborted=${JSON.stringify(data.aborted)}`);
        assert(data.direction === 'cw', `direction 应为 cw，实际 ${JSON.stringify(data.direction)}`);
        assert(
          data.start && Number.isFinite(data.start.x) && Number.isFinite(data.start.y),
          `dryRun 应返回 start 坐标，实际 ${JSON.stringify(data.start)}`,
        );
        assert(Date.now() - startedLocal < 10_000, 'dryRun:true 不应真实等待 durationMs');
        // 非法半径（0 不在 1–5000 范围）→ INVALID_ARGUMENT，且仍是干跑、不注入
        expectError(
          runCli({ action: 'mouse_circle', params: { centerX, centerY, radius: 0, dryRun: true } }),
          'INVALID_ARGUMENT',
          'mouse_circle radius=0',
        );
        return `samples=${data.samples} start=(${data.start.x},${data.start.y}) loops=${data.loops}（未注入输入）`;
      });

      check('5.30', 'find_image 不存在的模板 → TEMPLATE_NOT_FOUND', () => {
        expectError(
          runCli({ action: 'find_image', params: { template: 'dgc-acceptance/definitely-no-such-template.png' } }),
          'TEMPLATE_NOT_FOUND',
          'find_image 不存在的模板',
        );
        return 'code=TEMPLATE_NOT_FOUND';
      });

      check('5.31', 'find_image 损坏模板 → INVALID_TEMPLATE', () => {
        const corrupt = path.join(V03_DIR, 'corrupt-template.png');
        fs.writeFileSync(corrupt, 'this is definitely not a png image');
        expectError(
          runCli({ action: 'find_image', params: { template: corrupt } }),
          'INVALID_TEMPLATE',
          'find_image 损坏模板',
        );
        return 'code=INVALID_TEMPLATE';
      });

      check('5.32', 'wait_image 合成模板 + 短超时 → timedOut:true（只读截图）', () => {
        const template = createSyntheticPng(path.join(V03_DIR, 'template-red-20x20.png'), 20, 20, '#ff0000');
        const primary = statusResult.primary;
        const region = {
          x: primary.x + Math.round(primary.width / 2),
          y: primary.y + Math.round(primary.height / 2),
          width: 120,
          height: 120,
        };
        const data = getResult(
          runCli(
            {
              action: 'wait_image',
              params: { template, region, threshold: 0.99, timeoutMs: 200, intervalMs: 50 },
            },
            60_000,
          ),
          'wait_image 短超时',
        );
        assert(data.found === false, `短超时用例应 found=false，实际 ${JSON.stringify(data.found)}`);
        assert(data.timedOut === true, `短超时后 timedOut 应为 true，实际 ${JSON.stringify(data.timedOut)}`);
        assert(
          Number.isInteger(data.attempts) && data.attempts >= 1,
          `attempts 必须是正整数，实际 ${JSON.stringify(data.attempts)}`,
        );
        assert(Number.isFinite(data.elapsedMs) && data.elapsedMs >= 0, `elapsedMs 必须是数字，实际 ${JSON.stringify(data.elapsedMs)}`);
        return `timedOut=true attempts=${data.attempts} elapsedMs=${data.elapsedMs}（只读截图，无输入）`;
      });
    } else {
      for (const [id, name] of PYTHON_CHECKS) {
        if (['5.29', '5.30', '5.31', '5.32'].includes(id)) {
          skip(id, name, `当前平台 ${statusResult.platform} 的 v0.3 动作返回 UNSUPPORTED_PLATFORM（本脚本只在 Windows 做 CLI 用例）`);
        }
      }
    }

    /** 技能库检查依赖的共享状态（由 5.33 建立）。 */
    const skillState = {};
    const skillCheck = (id, name, fn) => {
      if (!skillState.id) {
        skip(id, name, '前置 5.33（技能库基线）失败');
        return;
      }
      check(id, name, fn);
    };

    check('5.33', '技能库 skill_save → skill_list → skill_get 基线流程', () => {
      const saved = getResult(
        runCli({
          action: 'skill_save',
          params: {
            title: 'DGC Acceptance Skill',
            description: 'acceptance v0.3',
            game: 'Acceptance',
            tags: ['acceptance'],
            markdown: '## acceptance\n仅含 wait 步骤，验收用。',
            steps: [{ action: 'wait', params: { durationMs: 0 } }],
          },
        }),
        'skill_save 新建',
      );
      assert(
        typeof saved.id === 'string' && /^[a-z0-9][a-z0-9._-]{0,63}$/.test(saved.id),
        `skill id 不合法：${JSON.stringify(saved.id)}`,
      );
      assert(saved.created === true, `新建技能 created 应为 true，实际 ${JSON.stringify(saved.created)}`);
      skillState.id = saved.id;

      const list = getResult(runCli({ action: 'skill_list' }), 'skill_list');
      assert(Number.isInteger(list.count) && list.count >= 1, `skill_list count 应 >= 1，实际 ${JSON.stringify(list.count)}`);
      const item = (list.skills || []).find((skill) => skill.id === saved.id);
      assert(item, `skill_list 里找不到 id=${saved.id}`);
      for (const key of ['id', 'title', 'description', 'game', 'tags', 'stepCount', 'updatedAt', 'backupCount']) {
        assert(item[key] !== undefined, `skill_list 条目缺字段 ${key}`);
      }
      assert(item.stepCount === 1, `stepCount 应为 1，实际 ${JSON.stringify(item.stepCount)}`);

      const got = getResult(runCli({ action: 'skill_get', params: { id: saved.id } }), 'skill_get');
      assert(got.skill && got.skill.id === saved.id, 'skill_get 返回的 skill.id 不一致');
      assert(Array.isArray(got.skill.steps) && got.skill.steps.length === 1, 'skill_get steps 应为 1 步');
      assert(typeof got.skill.title === 'string' && got.skill.title !== '', 'skill_get 缺 title');
      return `id=${saved.id} created=true stepCount=1`;
    });

    skillCheck('5.34', '技能库：更新自动备份 → skill_versions → skill_restore 回滚', () => {
      const updated = getResult(
        runCli({
          action: 'skill_save',
          params: {
            id: skillState.id,
            title: 'DGC Acceptance Skill v2',
            steps: [
              { action: 'wait', params: { durationMs: 0 } },
              { action: 'wait', params: { durationMs: 0 } },
            ],
          },
        }),
        'skill_save 更新',
      );
      assert(updated.created === false, `更新已存在技能 created 应为 false，实际 ${JSON.stringify(updated.created)}`);
      assert(
        typeof updated.backupPath === 'string' && updated.backupPath !== '',
        '覆盖前应自动备份旧版本并返回 backupPath',
      );
      assert(
        Number.isInteger(updated.backupCount) && updated.backupCount >= 1,
        `backupCount 应 >= 1，实际 ${JSON.stringify(updated.backupCount)}`,
      );

      const versions = getResult(runCli({ action: 'skill_versions', params: { id: skillState.id } }), 'skill_versions');
      assert(Number.isInteger(versions.count) && versions.count >= 1, `versions.count 应 >= 1，实际 ${JSON.stringify(versions.count)}`);
      const version = (versions.versions || [])[0];
      assert(version && typeof version.name === 'string' && version.name !== '', 'versions[0].name 不合法');
      assert(typeof version.savedAt === 'string' && version.savedAt !== '', 'versions[0].savedAt 不合法');
      assert(Number.isInteger(version.bytes) && version.bytes > 0, 'versions[0].bytes 不合法');

      const restored = getResult(
        runCli({ action: 'skill_restore', params: { id: skillState.id, backup: 'latest' } }),
        'skill_restore latest',
      );
      assert(
        typeof restored.restoredFrom === 'string' && restored.restoredFrom !== '',
        'skill_restore 缺 restoredFrom',
      );
      const after = getResult(runCli({ action: 'skill_get', params: { id: skillState.id } }), 'skill_get after restore');
      assert(
        after.skill.title === 'DGC Acceptance Skill',
        `恢复后 title 应回到 v1，实际 ${JSON.stringify(after.skill.title)}`,
      );
      assert(after.skill.steps.length === 1, '恢复后 steps 应回到 1 步');
      return `backupCount=${updated.backupCount} versions=${versions.count} restored→v1`;
    });

    skillCheck('5.35', '技能库 skill_run：dryRun:true 与仅 wait(0) 技能真执行（零输入）', () => {
      const dry = getResult(
        runCli({ action: 'skill_run', params: { id: skillState.id, dryRun: true } }),
        'skill_run dryRun',
      );
      assert(dry.dryRun === true, `dryRun 结果应为 true，实际 ${JSON.stringify(dry.dryRun)}`);
      assert(
        Array.isArray(dry.steps) && dry.steps.length === 1,
        `dryRun 应规划 1 步，实际 ${Array.isArray(dry.steps) ? dry.steps.length : JSON.stringify(dry.steps)}`,
      );
      assert(dry.steps[0].action === 'wait', `dryRun 步骤 action 应为 wait，实际 ${JSON.stringify(dry.steps[0].action)}`);

      // 仅 wait(0) 的技能可真实执行且零输入副作用（不点击/不按键/不移动）
      const real = getResult(
        runCli({ action: 'skill_run', params: { id: skillState.id, repeat: 1, timeoutMs: 30_000 } }, 90_000),
        'skill_run 真实执行',
      );
      assert(
        real.success === true,
        `仅 wait(0) 技能应执行成功：${JSON.stringify(real.failedStep ?? real.steps)}`,
      );
      assert(real.dryRun === false, '真实执行的 dryRun 应为 false');
      assert(
        Number.isInteger(real.repeatsDone) && real.repeatsDone >= 1,
        `repeatsDone 不合法：${JSON.stringify(real.repeatsDone)}`,
      );
      assert(
        Array.isArray(real.steps) && real.steps.length === 1 && real.steps[0].ok === true,
        '真实执行 steps 应为 1 步且 ok=true',
      );
      return `dryRun 规划 1 步；真实执行 success=${real.success} repeatsDone=${real.repeatsDone}（仅 wait 0ms）`;
    });

    skillCheck('5.36', '技能库软删除 + SKILL_NOT_FOUND / INVALID_SKILL', () => {
      const deleted = getResult(
        runCli({ action: 'skill_delete', params: { id: skillState.id } }),
        'skill_delete 软删',
      );
      assert(deleted.deleted === true, `delete.deleted 应为 true，实际 ${JSON.stringify(deleted.deleted)}`);
      assert(
        deleted.hardDeleted === false,
        `默认应软删除（hardDeleted=false），实际 ${JSON.stringify(deleted.hardDeleted)}`,
      );
      assert(typeof deleted.trashPath === 'string' && deleted.trashPath !== '', 'delete.trashPath 缺失');
      assert(
        path.resolve(deleted.trashPath).startsWith(path.resolve(path.join(SKILL_DIR, '.trash'))),
        `软删目录应在 skills/.trash 下，实际 ${deleted.trashPath}`,
      );

      expectError(
        runCli({ action: 'skill_get', params: { id: skillState.id } }),
        'SKILL_NOT_FOUND',
        '删除后 skill_get',
      );
      const list = getResult(runCli({ action: 'skill_list' }), 'skill_list after delete');
      assert(
        !(list.skills || []).some((skill) => skill.id === skillState.id),
        '软删后 skill_list 不应再列出该技能',
      );
      expectError(
        runCli({ action: 'skill_save', params: { title: 'bad skill', steps: [] } }),
        'INVALID_SKILL',
        'steps 为空的 skill_save',
      );
      return `软删 → trashPath=${oneLine(deleted.trashPath, 80)}；SKILL_NOT_FOUND / INVALID_SKILL 正确`;
    });

    check('5.37', '--selftest 含 v0.3 新能力检查（numpy / 模板目录 / 中止键）', () => {
      const result = runPython([CLI, '--selftest'], { timeoutMs: 90_000 });
      assert(result.status === 0, `--selftest 退出码 ${result.status}：${oneLine(result.stderr, 400)}`);
      const payload = parseSingleEnvelope(result, '--selftest v0.3');
      assert(payload.ok === true, `--selftest 不应失败：${JSON.stringify(payload.failures)}`);
      const names = (payload.checks || []).map((item) => String(item.name)).join(' | ');
      assert(/numpy/i.test(names), `selftest 缺少 numpy 能力检查，现有：${names}`);
      assert(/模板|template/i.test(names), `selftest 缺少模板目录检查，现有：${names}`);
      assert(/GetAsyncKeyState|中止|abort|按键/i.test(names), `selftest 缺少中止键能力检查，现有：${names}`);
      return `checks=${payload.checks.length}，含 numpy/模板目录/中止键能力检查`;
    });

    /* ------------------------------------------------------------------ *
     * v0.4：技能文件 JSONC + 中文注释 + 参数显式化（SPEC §9）
     * 在本次唯一 GAME_CONTROL_HOME 下保存技能 → 检查磁盘 JSONC 文本 →
     * content 全文编辑 / note 保留 / 坏 content / 旧纯 JSON 兼容 / 版本恢复。
     * 全部只写临时目录，零输入副作用。
     * ------------------------------------------------------------------ */
    const skillFileOf = (id) => path.join(HOME_DIR, 'skills', id, 'skill.json');
    /** @type {{id: string|null, file: string|null}} */
    const jsoncState = { id: null, file: null };
    const v04Check = (id, name, fn) => {
      if (!jsoncState.id) {
        skip(id, name, '前置 5.38（JSONC 结构化保存）失败');
        return;
      }
      check(id, name, fn);
    };

    check('5.38', 'v0.4 结构化保存 → 技能文件为 JSONC（// 第 N 步 + 显式 waitMs + 归一化默认参数）', () => {
      const saved = getResult(
        runCli({
          action: 'skill_save',
          params: {
            title: 'DGC JSONC Skill',
            description: 'v0.4 JSONC acceptance',
            game: 'Acceptance',
            tags: ['jsonc'],
            markdown: '## JSONC 验收',
            steps: [
              { action: 'mouse_click', params: { x: 120, y: 240 }, waitMs: 500, note: '点击开始游戏按钮' },
              { action: 'mouse_circle', params: { centerX: 1000, centerY: 800, radius: 300 }, waitMs: 0 },
              { action: 'mouse_drag', params: { x1: 10, y1: 20, x2: 300, y2: 400 }, waitMs: 120 },
            ],
          },
        }),
        'v0.4 skill_save 结构化',
      );
      assert(saved.created === true, `新建技能 created 应为 true，实际 ${JSON.stringify(saved.created)}`);
      jsoncState.id = saved.id;
      jsoncState.file = skillFileOf(saved.id);
      const text = readText(jsoncState.file, 'skills/<id>/skill.json');
      for (const stepNo of [1, 2, 3]) {
        assert(
          new RegExp(`//[^\\n]*第\\s*${stepNo}\\s*步`).test(text),
          `JSONC 缺少「// 第 ${stepNo} 步：」中文注释`,
        );
      }
      assert(/"waitMs"\s*:\s*0/.test(text), '每步 waitMs 必须显式写入（含 0）');
      assert(/"waitMs"\s*:\s*500/.test(text), '自定义 waitMs=500 未写入');
      assert(/"waitMs"\s*:\s*120/.test(text), '自定义 waitMs=120 未写入');
      assert(/"clicks"\s*:\s*1/.test(text), 'mouse_click 默认 clicks=1 未显式化');
      assert(/"button"\s*:\s*"left"/.test(text), '默认 button=left 未显式化');
      assert(/"intervalMs"\s*:\s*60/.test(text), 'mouse_click 默认 intervalMs=60 未显式化');
      assert(/"periodMs"\s*:\s*2500/.test(text), 'mouse_circle 默认 periodMs=2500 未显式化');
      assert(/"durationMs"\s*:\s*30000/.test(text), 'mouse_circle 默认 durationMs=30000 未显式化');
      assert(/"direction"\s*:\s*"cw"/.test(text), 'mouse_circle 默认 direction=cw 未显式化');
      assert(/"startAngleDeg"\s*:\s*-90/.test(text), 'mouse_circle 默认 startAngleDeg=-90 未显式化');
      assert(/"samplesPerSecond"\s*:\s*120/.test(text), 'mouse_circle 默认 samplesPerSecond=120 未显式化');
      assert(/"leadInMs"\s*:\s*300/.test(text), 'mouse_circle 默认 leadInMs=300 未显式化');
      assert(/"abortKey"\s*:\s*"esc"/.test(text), 'mouse_circle 默认 abortKey=esc 未显式化');
      assert(/"steps"\s*:\s*20/.test(text), 'mouse_drag 默认 steps=20 未显式化');
      assert(/"durationMs"\s*:\s*300/.test(text), 'mouse_drag 默认 durationMs=300 未显式化');
      assert(text.includes('点击开始游戏按钮'), 'note 内容应保留在 JSONC 中');
      assert(/\/\/[^\n]*(?:鼠标|圆形|拖拽)/.test(text), '步骤注释应包含动作中文摘要');
      const parsed = JSON.parse(stripJsoncComments(text));
      assert(Array.isArray(parsed.steps) && parsed.steps.length === 3, 'JSONC 去注释后 steps 应为 3 步');
      for (const [index, step] of parsed.steps.entries()) {
        assert(typeof step.action === 'string' && step.action !== '', `steps[${index}] 缺 action`);
        assert(step.params && typeof step.params === 'object', `steps[${index}] 缺 params`);
        assert(typeof step.waitMs === 'number', `steps[${index}] 缺显式 waitMs`);
        assert(typeof step.continueOnError === 'boolean', `steps[${index}] 缺显式 continueOnError`);
      }
      return `id=${saved.id}，3 步 JSONC：// 第 N 步 + waitMs 显式 + 归一化参数齐全`;
    });

    v04Check('5.39', 'v0.4 skill_get 返回 content/stepsView（content 与磁盘一致，comment 与注释同文）', () => {
      const got = getResult(runCli({ action: 'skill_get', params: { id: jsoncState.id } }), 'v0.4 skill_get');
      assert(typeof got.content === 'string' && got.content.trim() !== '', 'skill_get 缺少 content（原始 JSONC 文本）');
      assert(got.content.includes('//'), 'skill_get.content 应保留 // 注释');
      const disk = readText(jsoncState.file, 'skills/<id>/skill.json');
      assert(got.content.trim() === disk.trim(), 'content 应与磁盘文件内容一致');
      assert(
        Array.isArray(got.stepsView) && got.stepsView.length === 3,
        `stepsView 应有 3 步，实际 ${JSON.stringify(got.stepsView && got.stepsView.length)}`,
      );
      for (const [index, item] of got.stepsView.entries()) {
        assert(item.index === index, `stepsView[${index}].index 应为 ${index}`);
        assert(typeof item.action === 'string' && item.action !== '', `stepsView[${index}].action 非法`);
        assert(typeof item.comment === 'string' && item.comment.includes('第'), `stepsView[${index}].comment 应为中文摘要注释`);
        assert(typeof item.waitMs === 'number', `stepsView[${index}].waitMs 必须是数字`);
      }
      assert(
        got.skill && Array.isArray(got.skill.steps) && got.skill.steps.length === 3,
        'skill_get.skill.steps 应为 3 步（解析/归一化后的对象）',
      );
      return `content=${got.content.length} 字符，stepsView=${got.stepsView.length} 步`;
    });

    v04Check('5.40', 'v0.4 content 模式保存：注释重新生成、note 保留、数据不变（返回 content 可刷新编辑器）', () => {
      const content = [
        '// 手工编辑的注释 —— 保存后会被插件重新生成',
        '{',
        '  "schema": 1,',
        `  "id": ${JSON.stringify(jsoncState.id)},`,
        '  "title": "DGC JSONC Skill（content 模式）",',
        '  "description": "content mode",',
        '  "steps": [',
        '    // 第 1 步：等待 —— 手工写的注释',
        '    {',
        '      "action": "wait",',
        '      "params": { "durationMs": 0 },',
        '      "waitMs": 0,',
        '      "continueOnError": false,',
        '      "note": "自定义备注ABC"',
        '    }',
        '  ]',
        '}',
      ].join('\n');
      const saved = getResult(
        runCli({ action: 'skill_save', params: { id: jsoncState.id, content } }),
        'v0.4 skill_save content',
      );
      assert(saved.created === false, `content 更新已存在技能 created 应为 false，实际 ${JSON.stringify(saved.created)}`);
      assert(typeof saved.content === 'string' && saved.content.includes('//'), 'skill_save 应返回重新渲染的 content（含注释）');
      assert(saved.content.includes('自定义备注ABC'), '返回 content 应保留 note 自定义备注');
      assert(/\/\/[^\n]*第\s*1\s*步/.test(saved.content), '返回 content 应由插件重新生成「// 第 1 步：」注释');
      assert(Array.isArray(saved.stepsView) && saved.stepsView.length === 1, 'skill_save 应返回 stepsView（1 步）');
      jsoncState.file = skillFileOf(jsoncState.id);
      const disk = readText(jsoncState.file, 'skills/<id>/skill.json');
      assert(/\/\/[^\n]*第\s*1\s*步/.test(disk), '磁盘文件应重新生成中文注释');
      assert(disk.includes('自定义备注ABC'), '磁盘文件应保留 note 自定义备注');
      const parsed = JSON.parse(stripJsoncComments(disk));
      assert(parsed.title === 'DGC JSONC Skill（content 模式）', `content 模式 title 未生效：${JSON.stringify(parsed.title)}`);
      assert(
        Array.isArray(parsed.steps) && parsed.steps.length === 1 && parsed.steps[0].action === 'wait',
        'content 模式 steps 未生效',
      );
      return 'content 模式：note 保留、注释重生成、title/steps 数据生效';
    });

    v04Check('5.41', 'v0.4 非法 content / 未知步骤字段 → INVALID_SKILL', () => {
      expectError(
        runCli({ action: 'skill_save', params: { id: jsoncState.id, content: '{ "title": "坏技能", "steps": [ }' } }),
        'INVALID_SKILL',
        '坏 JSON content',
      );
      const unknownField = JSON.stringify({
        schema: 1,
        id: jsoncState.id,
        title: '未知字段技能',
        steps: [
          { action: 'wait', params: { durationMs: 0 }, waitMs: 0, continueOnError: false, bogusField: 1 },
        ],
      });
      expectError(
        runCli({ action: 'skill_save', params: { id: jsoncState.id, content: unknownField } }),
        'INVALID_SKILL',
        '未知步骤字段 content',
      );
      return '坏 JSON / 未知步骤字段均返回 INVALID_SKILL';
    });

    v04Check('5.42', 'v0.4 旧纯 JSON 技能兼容 + JSONC 技能 skill_run dryRun 通过', () => {
      const legacyId = 'dgc-legacy-json-skill';
      const legacyDir = path.join(HOME_DIR, 'skills', legacyId);
      fs.mkdirSync(legacyDir, { recursive: true });
      const legacy = {
        schema: 1,
        id: legacyId,
        title: 'Legacy JSON Skill',
        description: '旧的无注释 JSON',
        game: 'Acceptance',
        tags: [],
        markdown: '',
        steps: [{ action: 'wait', params: { durationMs: 0 } }],
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      };
      fs.writeFileSync(path.join(legacyDir, 'skill.json'), `${JSON.stringify(legacy, null, 2)}\n`, 'utf8');
      const got = getResult(runCli({ action: 'skill_get', params: { id: legacyId } }), 'v0.4 旧 JSON skill_get');
      assert(got.skill && got.skill.id === legacyId, '旧纯 JSON 技能应能读取');
      assert(typeof got.content === 'string' && got.content.includes('"steps"'), '旧纯 JSON 的 content 应返回原文');
      const dry = getResult(
        runCli({ action: 'skill_run', params: { id: jsoncState.id, dryRun: true } }),
        'v0.4 JSONC skill_run dryRun',
      );
      assert(dry.dryRun === true, 'JSONC 技能 skill_run dryRun 应为 true');
      assert(dry.success === true, `JSONC 技能 dryRun 应成功：${JSON.stringify(dry.failedStep ?? dry.steps)}`);
      assert(
        Array.isArray(dry.steps) && dry.steps.length === 1,
        `content 模式技能应规划 1 步，实际 ${JSON.stringify(dry.steps && dry.steps.length)}`,
      );
      const legacyDry = getResult(
        runCli({ action: 'skill_run', params: { id: legacyId, dryRun: true } }),
        'v0.4 旧 JSON skill_run dryRun',
      );
      assert(legacyDry.dryRun === true && legacyDry.success === true, '旧纯 JSON 技能 dryRun 应成功');
      return '旧纯 JSON 可读；JSONC content 技能 dryRun 计划 1 步';
    });

    v04Check('5.43', 'v0.4 版本恢复保留历史 JSONC 文本（恢复后 content 仍含中文注释）', () => {
      const versions = getResult(
        runCli({ action: 'skill_versions', params: { id: jsoncState.id } }),
        'v0.4 skill_versions',
      );
      assert(
        Number.isInteger(versions.count) && versions.count >= 1,
        `JSONC 技能应有备份版本，实际 ${JSON.stringify(versions.count)}`,
      );
      const restored = getResult(
        runCli({ action: 'skill_restore', params: { id: jsoncState.id, backup: 'latest' } }),
        'v0.4 skill_restore',
      );
      assert(typeof restored.restoredFrom === 'string' && restored.restoredFrom !== '', 'skill_restore 缺 restoredFrom');
      const got = getResult(runCli({ action: 'skill_get', params: { id: jsoncState.id } }), 'v0.4 恢复后 skill_get');
      assert(
        typeof got.content === 'string' && /\/\/[^\n]*第\s*\d+\s*步/.test(got.content),
        '恢复后 content 应保留历史 JSONC 中文注释',
      );
      const parsed = JSON.parse(stripJsoncComments(got.content));
      assert(Array.isArray(parsed.steps) && parsed.steps.length >= 1, '恢复后 JSONC 应可解析且含步骤');
      return `versions=${versions.count}，restore 后仍为 JSONC（含 // 第 N 步注释）`;
    });

    /* ------------------------------------------------------------------ *
     * v0.5：Esc 默认中止 / templates 多模板 / skill_run 前置校验 / 运行进度
     * 全部只读或干跑，不产生真实输入。
     * ------------------------------------------------------------------ */
    check('5.44', 'v0.5 mouse_circle dryRun 默认 abortKey=esc（不注入输入）', () => {
      const primary = statusResult.primary;
      const centerX = primary.x + Math.round(primary.width / 2);
      const centerY = primary.y + Math.round(primary.height / 2);
      const data = getResult(
        runCli({ action: 'mouse_circle', params: { centerX, centerY, radius: 100, dryRun: true } }),
        'v0.5 mouse_circle dryRun 默认 abortKey',
      );
      assert(
        data.abortKey === 'esc',
        `dryRun 结果 abortKey 应为 esc，实际 ${JSON.stringify(data.abortKey)}（SPEC §10.1/§10.5）`,
      );
      assert(data.aborted === false, `dryRun 不应被中止，实际 ${JSON.stringify(data.aborted)}`);
      const none = getResult(
        runCli({ action: 'mouse_circle', params: { centerX, centerY, radius: 100, dryRun: true, abortKey: 'none' } }),
        'v0.5 mouse_circle dryRun abortKey=none',
      );
      assert(none.abortKey === 'none', `显式 abortKey="none" 应回显 none，实际 ${JSON.stringify(none.abortKey)}`);
      return `默认 abortKey=${data.abortKey}，显式 none=${none.abortKey}（dryRun 未注入）`;
    });

    check('5.45', 'v0.5 templates 多模板参数校验（template 同给 / 超过 8 个 / 空数组 → INVALID_ARGUMENT）', () => {
      expectError(
        runCli({
          action: 'find_image',
          params: { template: 'keep-on-mining/next_round.png', templates: ['keep-on-mining/next_round.png'] },
        }),
        'INVALID_ARGUMENT',
        'template 与 templates 同时给',
      );
      expectError(
        runCli({
          action: 'find_image',
          params: { templates: Array.from({ length: 9 }, (_, index) => `dgc-acceptance/t${index}.png`) },
        }),
        'INVALID_ARGUMENT',
        'templates 9 个（超过 8）',
      );
      expectError(
        runCli({ action: 'find_image', params: { templates: [] } }),
        'INVALID_ARGUMENT',
        'templates 空数组',
      );
      return 'template+templates / 9 个 / 空数组 均返回 INVALID_ARGUMENT';
    });

    check('5.46', 'v0.5 templates 多模板：缺失文件 → TEMPLATE_NOT_FOUND，1 个合法模板可正常查找', () => {
      expectError(
        runCli({ action: 'find_image', params: { templates: ['dgc-acceptance/no-such-template-v05.png'] } }),
        'TEMPLATE_NOT_FOUND',
        'templates 缺失文件',
      );
      const template = createSyntheticPng(path.join(V03_DIR, 'v05-template-40x20.png'), 40, 20, '#00ff00');
      const primary = statusResult.primary;
      const data = getResult(
        runCli(
          {
            action: 'find_image',
            params: {
              templates: [template],
              region: { x: primary.x, y: primary.y, width: 160, height: 160 },
              threshold: 0.99,
              abortKey: 'none',
            },
          },
          60_000,
        ),
        'templates 单模板查找',
      );
      assert(typeof data.found === 'boolean', `find_image.templates 结果缺 found：${JSON.stringify(data.found)}`);
      assert(data.template && typeof data.template.path === 'string' && data.template.path !== '', '结果应返回 template.path');
      return `缺失文件→TEMPLATE_NOT_FOUND；单模板查找 found=${data.found}（只读截图）`;
    });

    v04Check('5.47', 'v0.5 skill_run dryRun 返回 estimatedDurationMs + 时长前置校验（超 timeoutMs → INVALID_ARGUMENT）', () => {
      const dry = getResult(
        runCli({ action: 'skill_run', params: { id: jsoncState.id, dryRun: true, timeoutMs: 120000 } }, 60_000),
        'v0.5 skill_run dryRun estimatedDurationMs',
      );
      assert(
        Number.isInteger(dry.estimatedDurationMs) && dry.estimatedDurationMs > 0,
        `dryRun 应返回 estimatedDurationMs（正整数），实际 ${JSON.stringify(dry.estimatedDurationMs)}`,
      );
      assert(
        dry.estimatedDurationMs >= 30000,
        `含 30s mouse_circle 的技能 estimatedDurationMs 应 >=30000，实际 ${dry.estimatedDurationMs}`,
      );
      const envelope = runCli({ action: 'skill_run', params: { id: jsoncState.id, dryRun: true, timeoutMs: 2000 } });
      expectError(envelope, 'INVALID_ARGUMENT', 'skill_run 前置时长校验');
      const error = envelope.error || {};
      assert(
        /timeoutMs|时长|增大|缩小/i.test(`${error.message || ''} ${error.hint || ''}`),
        `前置校验 hint 应提示增大 timeoutMs / 缩小参数，实际 ${JSON.stringify(error)}`,
      );
      return `estimatedDurationMs=${dry.estimatedDurationMs}；timeoutMs=2000 → INVALID_ARGUMENT`;
    });

    check('5.48', 'v0.5 skill_progress 动作 + logs/skill-progress.json 结构（运行结束 running=false）', () => {
      const saved = getResult(
        runCli({
          action: 'skill_save',
          params: {
            title: 'DGC Progress Skill',
            description: 'v0.5 progress acceptance',
            steps: [{ action: 'wait', params: { durationMs: 0 } }],
          },
        }),
        'v0.5 progress skill_save',
      );
      const run = getResult(
        runCli({ action: 'skill_run', params: { id: saved.id, timeoutMs: 30_000 } }, 90_000),
        'v0.5 progress skill_run',
      );
      assert(run.success === true, `wait(0) 技能应执行成功：${JSON.stringify(run.failedStep ?? run.steps)}`);
      const progressFile = path.join(HOME_DIR, 'logs', 'skill-progress.json');
      assertFile(progressFile, 'logs/skill-progress.json');
      let progress;
      try {
        progress = JSON.parse(fs.readFileSync(progressFile, 'utf8'));
      } catch (error) {
        throw new Error(`skill-progress.json 不是合法 JSON：${error.message}`);
      }
      assert(progress.running === false, `进度文件运行结束后 running 应为 false，实际 ${JSON.stringify(progress.running)}`);
      assert(progress.skillId === saved.id, `progress.skillId 应为 ${saved.id}，实际 ${JSON.stringify(progress.skillId)}`);
      for (const key of ['startedAt', 'updatedAt', 'elapsedMs', 'repeat', 'repeatsDone']) {
        assert(progress[key] !== undefined, `进度文件缺字段 ${key}`);
      }
      assert(typeof progress.elapsedMs === 'number' && progress.elapsedMs >= 0, 'progress.elapsedMs 必须是非负数字');
      assert(
        progress.step &&
          Number.isInteger(progress.step.index) &&
          Number.isInteger(progress.step.total) &&
          typeof progress.step.action === 'string' &&
          progress.step.action !== '',
        '进度文件 step 字段非法',
      );
      const envelope = getResult(runCli({ action: 'skill_progress' }), 'v0.5 skill_progress 动作');
      const fromAction = envelope.progress !== undefined ? envelope.progress : envelope;
      assert(
        fromAction && fromAction.skillId === saved.id && fromAction.running === false,
        `skill_progress 动作应返回文件内容：${JSON.stringify(fromAction)}`,
      );
      return `skill-progress.json running=false skillId=${saved.id}；skill_progress 动作一致`;
    });

    check('5.49', 'v0.6.1 CLI 兼容 skill_run.repeat（面板/工具 schema 已不暴露，Python CLI 仍可用）', () => {
      const saved = getResult(
        runCli({
          action: 'skill_save',
          params: {
            title: 'DGC Repeat Compat Skill',
            description: 'v0.6.1 repeat CLI compatibility',
            steps: [{ action: 'wait', params: { durationMs: 0 } }],
          },
        }),
        'v0.6.1 repeat-compat skill_save',
      );
      const run = getResult(
        runCli({ action: 'skill_run', params: { id: saved.id, repeat: 2, timeoutMs: 30_000 } }, 90_000),
        'v0.6.1 CLI skill_run repeat=2',
      );
      assert(run.success === true, `CLI repeat=2 应执行成功：${JSON.stringify(run.failedStep ?? run.steps)}`);
      assert(
        Number(run.repeatsDone) === 2,
        `CLI repeat=2 的 repeatsDone 应为 2，实际 ${JSON.stringify(run.repeatsDone)}（repeat 仅 CLI/内部兼容）`,
      );
      return `CLI skill_run.repeat=2 兼容：repeatsDone=${run.repeatsDone}（工具 schema 已不暴露 repeat）`;
    });
  }
}

/* ------------------------------------------------------------------ */
/* 6. index.js 静态契约                                                */
/* ------------------------------------------------------------------ */

function checkHostStatic() {
  section('6. Host 静态契约（dsh-game-control/index.js，SPEC §6.6 / §3）');

  check('6.1', 'index.js 关键字（ctx.tools.register / /api/game-control / --serve / spawn( / PYTHON_NOT_FOUND）', () => {
    const text = readText(path.join(PLUGIN, 'index.js'), 'dsh-game-control/index.js');
    const tokens = ['ctx.tools.register', '/api/game-control', '--serve', 'spawn(', 'PYTHON_NOT_FOUND'];
    const missing = tokens.filter((token) => !text.includes(token));
    assert(missing.length === 0, `缺少关键字：${JSON.stringify(missing)}`);
    return tokens.join(' , ');
  });

  check('6.2', 'index.js 导出与生命周期契约（SPEC §3 / §3.4）', () => {
    const text = readText(path.join(PLUGIN, 'index.js'), 'dsh-game-control/index.js');
    assert(
      /export\s+const\s+name\s*=\s*'@local\/dsh-game-control'/.test(text),
      "缺少 export const name = '@local/dsh-game-control'",
    );
    assert(/export\s+const\s+inject\s*=\s*\[[^\]]*['"]tools['"]/.test(text), "inject 未包含 'tools'");
    assert(/export\s+function\s+apply\s*\(/.test(text), '缺少 export function apply(ctx)');
    assert(text.includes('ctx.effect('), '缺少 ctx.effect 注册/清理（插件卸载必须清理）');
    assert(text.includes("'package.json'"), '版本号应从 package.json 读取（SPEC §3.4）');
    assert(!/from\s+['"]@deepseek-ai\//.test(text), 'index.js 不得 import @deepseek-ai/* 包（本地 link 解析不到）');
    return 'name/inject/apply/effect/package.json 版本/@deepseek-ai 无依赖';
  });

  check('6.3', 'index.js 窗口动作契约（list_windows / capture_window / engine 字段，SPEC §2.3.1）', () => {
    const text = readText(path.join(PLUGIN, 'index.js'), 'dsh-game-control/index.js');
    for (const token of ["'list_windows'", "'capture_window'"]) {
      assert(text.includes(token), `index.js 缺少动作 ${token}`);
    }
    assert(/engine\s*:/.test(text), 'index.js 工具参数缺少 engine 字段');
    for (const token of ['titleContains', 'includeInvisible', 'includeCursor', 'hwnd', 'timeoutMs']) {
      assert(text.includes(token), `index.js 缺少窗口动作参数 ${token}`);
    }
    for (const token of ["'wgc'", "'printwindow'", "'screen'"]) {
      assert(text.includes(token), `index.js engine 枚举缺少 ${token}`);
    }
    return 'list_windows / capture_window / engine(wgc,printwindow,screen) 等参数齐全';
  });

  check('6.4', 'agent 工具 action 枚举 23 个（18 原有 + 5 个 v0.3 新动作，SPEC §8.4）', () => {
    const text = readText(path.join(PLUGIN, 'index.js'), 'dsh-game-control/index.js');
    const match =
      text.match(/const CONTROL_ACTIONS\s*=\s*\[([\s\S]*?)\]\s*;/) ||
      text.match(/const ACTIONS\s*=\s*\[([\s\S]*?)\]\s*;/);
    assert(match, 'index.js 找不到控制动作清单（CONTROL_ACTIONS 或 ACTIONS）定义');
    const actions = [...match[1].matchAll(/'([a-z_]+)'/g)].map((item) => item[1]);
    assert(actions.length === 23, `期望 23 个 action，实际 ${actions.length} 个：${actions.join(',')}`);
    assert(new Set(actions).size === actions.length, `action 枚举有重复：${actions.join(',')}`);
    const expected = [
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
    const missing = expected.filter((name) => !actions.includes(name));
    assert(missing.length === 0, `缺少 action：${missing.join(',')}`);
    return `23 个 action，含 activate_window/mouse_circle/wait/find_image/wait_image`;
  });

  check('6.5', 'index.js 注册 game_skill 工具（8 个技能动作 + v0.6 循环动作，SPEC §8.3/§10.4/§11.2）', () => {
    const text = readText(path.join(PLUGIN, 'index.js'), 'dsh-game-control/index.js');
    assert(
      text.includes("name: 'game_control'"),
      "index.js 找不到 game_control 工具定义（name: 'game_control'）",
    );
    assert(text.includes("name: 'game_skill'"), "index.js 找不到 game_skill 工具定义（name: 'game_skill'）");
    const match = text.match(/const SKILL_ACTIONS\s*=\s*\[([\s\S]*?)\]\s*;/);
    const skillActions = match
      ? [...match[1].matchAll(/'([a-z_]+)'/g)].map((item) => item[1])
      : [];
    const expected = [
      'skill_list',
      'skill_get',
      'skill_save',
      'skill_versions',
      'skill_restore',
      'skill_delete',
      'skill_run',
      'skill_progress',
    ];
    const missing = expected.filter(
      (action) => !skillActions.includes(action) && !text.includes(`'${action}'`),
    );
    assert(missing.length === 0, `game_skill 缺少动作：${missing.join('、')}`);
    if (text.includes("'loop_start'")) {
      for (const loopAction of ['loop_start', 'loop_status', 'loop_stop']) {
        assert(text.includes(`'${loopAction}'`), `v0.6 game_skill 缺少循环动作 ${loopAction}`);
      }
    }
    assert(
      /tools\.register/.test(text),
      'index.js 找不到 ctx.tools.register（game_control + game_skill 应通过它注册）',
    );
    assert(text.includes('skillToolDefinition'), '缺少 game_skill 工具定义（skillToolDefinition）');
    for (const token of ['steps', 'dryRun', 'repeat', 'stopOnError', 'backup', 'hard']) {
      assert(text.includes(token), `game_skill 参数 schema 缺少 ${token}`);
    }
    assert(/additionalProperties/.test(text), '参数 schema 必须显式写 additionalProperties');
    return `game_skill 工具 + ${expected.length} 个 skill_* 动作${text.includes("'loop_start'") ? ' + 3 个循环动作' : ''}`;
  });

  check('6.6', 'index.js timeoutFor 含 v0.3 分支（mouse_circle / wait / wait_image / skill_run 等，SPEC §8.4）', () => {
    const text = readText(path.join(PLUGIN, 'index.js'), 'dsh-game-control/index.js');
    const match = text.match(/function timeoutFor\s*\([^)]*\)\s*\{([\s\S]*?)\n\}/);
    assert(match, 'index.js 找不到 function timeoutFor(...) { ... } 定义');
    const body = match[1];
    for (const token of ['activate_window', 'mouse_circle', 'wait_image', 'find_image', 'skill_run']) {
      assert(body.includes(token), `timeoutFor 缺少 ${token} 分支（SPEC §8.4）`);
    }
    assert(
      /['"]wait['"]/.test(body),
      "timeoutFor 缺少 wait 分支（min(620000, durationMs+5000)）",
    );
    return 'timeoutFor 含 activate_window / mouse_circle / wait / find_image / wait_image / skill_run 分支';
  });

  check('6.7', 'index.js toolDefinition 含 v0.3 参数（centerX/centerY/radius/periodMs/direction/startAngleDeg/samplesPerSecond/abortKey/template/threshold/scales）', () => {
    const text = readText(path.join(PLUGIN, 'index.js'), 'dsh-game-control/index.js');
    const tokens = [
      'centerX',
      'centerY',
      'radius',
      'periodMs',
      'direction',
      'startAngleDeg',
      'samplesPerSecond',
      'abortKey',
      'template',
      'threshold',
      'scales',
      'leadInMs',
      'dryRun',
      'restore',
    ];
    const missing = tokens.filter((token) => !text.includes(token));
    assert(missing.length === 0, `index.js toolDefinition 缺少参数字段：${missing.join('、')}`);
    return `${tokens.length} 个 v0.3 参数字段齐全`;
  });

  check('6.8', 'index.js game_skill schema 含 content（JSONC 全文）/ stepsView（v0.4，SPEC §9.3/§9.4）', () => {
    const text = readText(path.join(PLUGIN, 'index.js'), 'dsh-game-control/index.js');
    assert(
      /content\s*:\s*\{\s*type:\s*['"]string['"]/.test(text),
      "game_skill 参数 schema 缺少 content（应为 { type: 'string' }）",
    );
    assert(text.includes('stepsView'), 'index.js 缺少 stepsView 说明（skill_get 返回 content/stepsView）');
    assert(/JSONC/i.test(text), 'index.js game_skill 描述缺少 JSONC 说明');
    return 'game_skill: content 参数 + stepsView + JSONC 说明';
  });

  check('6.9', 'index.js v0.5：skill-progress 路由 / templates / abortKey 默认 esc（SPEC §10.1/§10.4）', () => {
    const text = readText(path.join(PLUGIN, 'index.js'), 'dsh-game-control/index.js');
    assert(text.includes('/skill-progress'), "index.js 缺少 GET /api/game-control/skill-progress 路由");
    assert(
      text.includes("'skill_progress'") || text.includes('"skill_progress"'),
      'index.js 缺少 skill_progress 动作（Python 也提供 skill_progress）',
    );
    assert(text.includes('templates'), 'index.js toolDefinition 缺少 templates 多模板参数');
    assert(
      /abortKey[\s\S]{0,160}esc/i.test(text),
      'index.js 的 abortKey 描述应说明默认 esc（兼容 escape/none）',
    );
    assert(/Esc/i.test(text), 'index.js 工具描述应说明 Esc 中止');
    return 'skill-progress 路由 + skill_progress 动作 + templates + abortKey 默认 esc';
  });

  check('6.10', 'index.js v0.6 长循环：loop_start/loop_status/loop_stop + loop-state.json/loop.jsonl + LOOP_ALREADY_RUNNING（SPEC §11.2）', () => {
    const text = readText(path.join(PLUGIN, 'index.js'), 'dsh-game-control/index.js');
    for (const action of ['loop_start', 'loop_status', 'loop_stop']) {
      assert(text.includes(`'${action}'`), `index.js 缺少循环动作 ${action}（SPEC §11.2）`);
    }
    for (const token of [
      'loop-state.json',
      'loop.jsonl',
      'LOOP_ALREADY_RUNNING',
      'roundTimeoutMs',
      'stopRequested',
      'perRoundTimeoutMs',
      'completedRounds',
      'failedRounds',
      'currentRound',
      'failedStep',
    ]) {
      assert(text.includes(token), `index.js 长循环实现缺少 ${token}`);
    }
    assert(/rounds/.test(text) && /intervalMs/.test(text), 'index.js 长循环缺少 rounds/intervalMs 参数');
    assert(/jobId/.test(text), 'index.js 长循环缺少 jobId');
    return 'loop_start/loop_status/loop_stop + loop-state.json/loop.jsonl + LOOP_ALREADY_RUNNING';
  });

  check('6.11', 'index.js v0.6：skill_run 超时余量 120000 / skill-progress 返回 bridge 与 stale（SPEC §11.4/§11.5）', () => {
    const text = readText(path.join(PLUGIN, 'index.js'), 'dsh-game-control/index.js');
    const match = text.match(/function timeoutFor\s*\([^)]*\)\s*\{([\s\S]*?)\n\}/);
    assert(match, 'index.js 找不到 function timeoutFor(...) { ... } 定义');
    assert(
      /120[_,]?000/.test(match[1]),
      'timeoutFor 中 skill_run 应使用 +120000 超时余量（SPEC §11.4）',
    );
    assert(/stale/i.test(text), 'index.js 缺少进度 stale 标记（SPEC §11.5）');
    assert(/backend-stopped/.test(text), 'index.js 缺少 abortReason:"backend-stopped" 标记');
    assert(
      /skill-progress[\s\S]{0,800}bridge/.test(text) || /bridge[\s\S]{0,800}skill-progress/.test(text),
      'GET /api/game-control/skill-progress 应返回 bridge 信息',
    );
    return 'skill_run +120000 余量 + /skill-progress 返回 bridge + stale/backend-stopped';
  });

  check('6.12', 'index.js timeoutFor 含 loop_* 分支（15000/15000/30000，SPEC §11.4）', () => {
    const text = readText(path.join(PLUGIN, 'index.js'), 'dsh-game-control/index.js');
    const match = text.match(/function timeoutFor\s*\([^)]*\)\s*\{([\s\S]*?)\n\}/);
    assert(match, 'index.js 找不到 function timeoutFor(...) { ... } 定义');
    const body = match[1];
    for (const token of ['loop_start', 'loop_status', 'loop_stop']) {
      assert(body.includes(token), `timeoutFor 缺少 ${token} 分支`);
    }
    assert(/15[_,]?000/.test(body), 'loop_start/loop_status 超时应为 15000');
    assert(/30[_,]?000/.test(body), 'loop_stop 超时应为 30000');
    return 'timeoutFor 含 loop_start/loop_status(15s)/loop_stop(30s) 分支';
  });

  check('6.13', 'index.js v0.6.1：game_skill schema 不含 repeat + loop_status.currentStep（SPEC §12.2/§12.3）', () => {
    const text = readText(path.join(PLUGIN, 'index.js'), 'dsh-game-control/index.js');
    const start = text.indexOf('function skillToolDefinition');
    assert(start >= 0, 'index.js 找不到 skillToolDefinition 定义');
    const end = text.indexOf('function registerRoutes', start);
    const block = text.slice(start, end > start ? end : start + 12000);
    assert(
      !/^\s*repeat\s*:/m.test(block),
      'game_skill 工具 schema 不应再暴露 repeat 属性（v0.6.1 已 deprecated，仅 CLI/内部兼容）',
    );
    assert(
      /单轮|只执行 1 轮|one-round/i.test(block),
      'skill_run 工具描述应说明它是单轮底层原语',
    );
    assert(
      /deprecated/i.test(block) || /deprecated/i.test(text),
      'index.js 应说明 repeat 已 deprecated（仅 CLI/内部兼容）',
    );
    assert(text.includes('currentStep'), 'loop_status 实现缺少 currentStep（当前步骤）');
    assert(
      /currentStep[\s\S]{0,160}index/.test(text) && /currentStep[\s\S]{0,200}total/.test(text),
      'currentStep 应包含 index/total 字段',
    );
    return 'game_skill schema 无 repeat（deprecated 说明）+ loop_status.currentStep{index,total,...}';
  });

  check('6.14', 'python/game_control.py v0.6.2：WAIT_IMAGE_TIMEOUT / screenshotPath / skill-fail / onMissing（SPEC §13.2.1）', () => {
    const text = readText(path.join(PY_DIR, 'game_control.py'), 'python/game_control.py');
    for (const token of ['WAIT_IMAGE_TIMEOUT', 'screenshotPath', 'skill-fail-', 'onMissing', 'failedStep']) {
      assert(text.includes(token), `python/game_control.py 缺少 v0.6.2 关键字「${token}」`);
    }
    assert(
      /WAIT_IMAGE_TIMEOUT[\s\S]{0,300}TEMPLATE_NOT_FOUND|TEMPLATE_NOT_FOUND[\s\S]{0,300}WAIT_IMAGE_TIMEOUT/.test(text),
      '模板失败错误码应同时定义 WAIT_IMAGE_TIMEOUT 与 TEMPLATE_NOT_FOUND（wait_image/find_image 各一）',
    );
    assert(/find_image/.test(text) && /TEMPLATE_NOT_FOUND/.test(text), 'find_image 模板失败应返回 TEMPLATE_NOT_FOUND');
    assert(/等待模板超时|模板未找到/.test(text), '失败 message 应写明模板未找到/等待超时');
    assert(
      /skill-fail-%s-step%d\.png|skill-fail-/.test(text),
      '失败截图文件名应为 skill-fail-<时间>-step<idx>.png',
    );
    assert(/failedStep/.test(text) && /screenshotPath/.test(text), 'failedStep 应携带 screenshotPath');
    return 'game_control.py: WAIT_IMAGE_TIMEOUT/TEMPLATE_NOT_FOUND + failedStep.screenshotPath + skill-fail-';
  });
}

/* ------------------------------------------------------------------ */
/* 7. client.js 静态契约                                               */
/* ------------------------------------------------------------------ */

function checkClientStatic() {
  section('7. Client 静态契约（dsh-game-control/client.js，SPEC §6.7 / §4）');

  check('7.1', 'client.js 关键字（sidebar.panellist / main / /api/game-control/exec / ctx.locale.register）', () => {
    const text = readText(path.join(PLUGIN, 'client.js'), 'dsh-game-control/client.js');
    for (const token of ['sidebar.panellist', '/api/game-control/exec', 'ctx.locale.register']) {
      assert(text.includes(token), `缺少关键字 ${JSON.stringify(token)}`);
    }
    assert(/name:\s*'main'/.test(text), "缺少 main 槽位注册 { name: 'main' }");
    assert(text.includes('__ModuleLoader__.load'), '缺少 window.__ModuleLoader__.load 加载声明');
    return 'sidebar.panellist + main + exec 路由 + locale + ModuleLoader';
  });

  check('7.2', 'client.js 加载与注入契约（SPEC §4）', () => {
    const text = readText(path.join(PLUGIN, 'client.js'), 'dsh-game-control/client.js');
    assert(/id:\s*'@local\/dsh-game-control'/.test(text), "ModuleLoader id 必须是 '@local/dsh-game-control'");
    assert(/inject:\s*\[\s*'slots'\s*,\s*'locale'\s*\]/.test(text), "必须返回 inject: ['slots','locale']");
    assert(text.includes("require('react')"), "factory 里必须 require('react')");
    assert(!/from\s+['"]@deepseek-ai\//.test(text), 'client.js 不得 import Harness Client 包');
    return "id=@local/dsh-game-control，inject=['slots','locale']，react 来自 require";
  });

  check('7.3', 'client.js 窗口截图入口（list_windows / capture_window exec 调用，v0.2）', () => {
    const text = readText(path.join(PLUGIN, 'client.js'), 'dsh-game-control/client.js');
    for (const action of ['list_windows', 'capture_window']) {
      assert(text.includes(`'${action}'`) || text.includes(`"${action}"`), `client.js 缺少窗口动作调用 ${action}`);
      const callPattern = new RegExp(
        `(?:run|exec|execAction|call|post)\\s*\\(\\s*['"]${action}['"]|action:\\s*['"]${action}['"]`,
      );
      assert(callPattern.test(text), `client.js 里 ${action} 不是通过 exec/run 调用（应走 /api/game-control/exec）`);
    }
    assert(text.includes('/api/game-control/exec'), 'client.js 走 exec 路由的封装缺失');
    return 'list_windows + capture_window 均通过 exec 调用';
  });

  check('7.4', 'client.js v0.3 UI 入口（activate_window / mouse_circle / find_image / 技能库，SPEC §8.4）', () => {
    const text = readText(path.join(PLUGIN, 'client.js'), 'dsh-game-control/client.js');
    for (const action of ['activate_window', 'mouse_circle', 'find_image', 'skill_list', 'skill_save', 'skill_run']) {
      assert(
        text.includes(`'${action}'`) || text.includes(`"${action}"`),
        `client.js 缺少 v0.3 UI 动作 ${action}`,
      );
    }
    for (const label of ['激活窗口', '圆形轨迹', '模板找图', '技能库']) {
      assert(text.includes(label), `client.js 缺少 v0.3 面板文案「${label}」`);
    }
    assert(text.includes('/api/game-control/exec'), 'client.js 走 exec 路由的封装缺失');
    return 'activate_window / mouse_circle / find_image / 技能库 UI 文案与动作入口齐全';
  });

  check('7.5', 'client.js v0.4 技能文件编辑器（完整配置文件 / content / stepsView / note，SPEC §9.4）', () => {
    const text = readText(path.join(PLUGIN, 'client.js'), 'dsh-game-control/client.js');
    for (const token of ['完整配置文件', 'content', 'stepsView']) {
      assert(text.includes(token), `client.js 缺少 v0.4 技能编辑器关键字「${token}」`);
    }
    assert(
      text.includes('note') || text.includes('备注'),
      'client.js 缺少 note（自定义备注）/ 备注提示文案',
    );
    assert(text.includes('/api/game-control/exec'), 'client.js 走 exec 路由的封装缺失');
    return '完整配置文件编辑器（content）+ stepsView 步骤列表 + note 提示';
  });

  check('7.6', 'client.js 技能库第二项 / 结构化步骤表格 / 循环进度轮询（v0.5→v0.6.1，SPEC §10.2/§10.3/§12.2）', () => {
    const text = readText(path.join(PLUGIN, 'client.js'), 'dsh-game-control/client.js');
    assert(
      /结构化|stepTable|stepsTable|step-table|stepRows/i.test(text),
      'client.js 缺少结构化步骤表格（step table）实现关键字',
    );
    assert(/已等待|elapsed/i.test(text), 'client.js 缺少「已等待 X 秒」进度文案');
    assert(/禁用|disabled/.test(text), 'client.js 缺少运行期间禁用保存/刷新相关处理');
    assert(text.includes('完整配置文件'), 'client.js 缺少「高级」完整配置文件（JSONC）编辑区');
    assert(text.includes('loop_status'), 'client.js 运行进度应统一轮询 loop_status（v0.6.1）');
    // 技能库必须位于第二张卡片：状态卡 → 技能库卡 → 鼠标卡（按 JSX 渲染顺序检查）
    const cardStatus = text.indexOf("title: t('statusTitle')");
    const cardSkill = text.indexOf("title: t('skillTitle')");
    const cardMouse = text.indexOf("title: t('mouseTitle')");
    const cardOrderKnown = cardStatus >= 0 && cardSkill >= 0 && cardMouse >= 0;
    const orderOk = cardOrderKnown
      ? cardStatus < cardSkill && cardSkill < cardMouse
      : /第二项|第 2 项|second card|secondary card/i.test(text);
    assert(
      orderOk,
      `client.js 中技能库应位于第二项（status=${cardStatus}, skill=${cardSkill}, mouse=${cardMouse}）`,
    );
    return '技能库第二项 + 结构化步骤表格 + loop_status 进度/已等待 + 运行期禁用';
  });

  check('7.7', 'client.js v0.6.1 单套轮数控制（轮数+运行/停止/立即停止，无重复次数/运行一次/启动循环，SPEC §12.2/§12.3）', () => {
    const text = readText(path.join(PLUGIN, 'client.js'), 'dsh-game-control/client.js');
    for (const action of ['loop_start', 'loop_status', 'loop_stop']) {
      assert(
        text.includes(`'${action}'`) || text.includes(`"${action}"`),
        `client.js 缺少循环动作调用 ${action}`,
      );
    }
    for (const removedToken of ['重复次数', '运行一次', '启动循环']) {
      assert(
        !text.includes(removedToken),
        `client.js 仍包含 v0.6.1 已删除的双控制文案「${removedToken}」（应统一为「轮数 + 运行/停止」）`,
      );
    }
    for (const token of ['轮数', '轮间隔', '运行', '停止', '立即停止']) {
      assert(text.includes(token), `client.js 缺少统一轮数控制文案「${token}」`);
    }
    assert(/rounds/.test(text), 'client.js 缺少 rounds（轮数）参数');
    assert(
      /loop_start[\s\S]{0,200}rounds|rounds[\s\S]{0,200}loop_start/.test(text),
      'client.js 应统一用 loop_start + rounds 启动（rounds=1 单次、>1 循环）',
    );
    const progressText =
      /第\s*\{?\w*\}?\s*\/\s*\{?\w*\}?\s*轮/.test(text) ||
      (/已完成/.test(text) && /失败/.test(text) && /当前轮已等待/.test(text)) ||
      text.includes('completedRounds');
    assert(progressText, 'client.js 缺少统一轮次进度文案（第 N/M 轮 · 已完成/失败/当前轮已等待）');
    assert(
      /currentStep|当前步骤/.test(text),
      'client.js 缺少 loop_status.currentStep / 当前步骤显示',
    );
    assert(/dryRun|干跑/.test(text), 'client.js 应保留 dryRun 干跑');
    return '单一轮数控制（轮数+运行/停止/立即停止）+ loop_start.rounds + 当前步骤，已无重复次数/运行一次/启动循环';
  });

  check('7.8', 'client.js v0.6 连接中断修复（后端连接中断 / 重置状态 / stale / bridge，SPEC §11.5）', () => {
    const text = readText(path.join(PLUGIN, 'client.js'), 'dsh-game-control/client.js');
    for (const token of ['后端连接中断', '重置状态']) {
      assert(text.includes(token), `client.js 缺少连接中断文案「${token}」`);
    }
    assert(/stale/i.test(text), 'client.js 缺少 stale 处理');
    assert(/bridge/i.test(text), 'client.js 未处理 /skill-progress 返回的 bridge 信息');
    assert(
      /Failed to fetch|连接中断|中断/.test(text),
      'client.js 未把 fetch 失败转成中文可读提示',
    );
    return '后端连接中断提示 + 重置状态 + stale/bridge 处理';
  });

  check('7.9', 'client.js v0.6.2：「失败后继续」复选 + 每轮 failedStep/失败截图展示（SPEC §13.2.2）', () => {
    const text = readText(path.join(PLUGIN, 'client.js'), 'dsh-game-control/client.js');
    for (const token of ['失败后继续', 'failedStep', 'stopOnError']) {
      assert(text.includes(token), `client.js 缺少 v0.6.2 关键字「${token}」`);
    }
    assert(
      /failedStep[\s\S]{0,400}screenshotPath|screenshotPath[\s\S]{0,400}failedStep/.test(text),
      'client.js 应在每轮失败信息中展示 failedStep.screenshotPath（失败截图）',
    );
    assert(/失败截图|截图路径|screenshotPath/.test(text), 'client.js 缺少失败截图展示文案');
    assert(
      /stopOnError[\s\S]{0,200}false|false[\s\S]{0,200}stopOnError/.test(text),
      '「失败后继续」应对应 loop_start.stopOnError=false',
    );
    return '「失败后继续」↔ stopOnError=false + 每轮 failedStep/screenshotPath 展示';
  });
}

/* ------------------------------------------------------------------ */
/* 8. 清理与汇总                                                       */
/* ------------------------------------------------------------------ */

function cleanup() {
  if (KEEP_TMP) {
    console.log(`\n临时目录保留（--keep-tmp）：${HOME_DIR}`);
    return false;
  }
  let removed = false;
  try {
    if (fs.existsSync(SPAWN_DIR)) fs.rmSync(SPAWN_DIR, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
  } catch {
    /* ignore */
  }
  try {
    if (fs.existsSync(V03_DIR)) fs.rmSync(V03_DIR, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
  } catch {
    /* ignore */
  }
  // Windows 上目录可能被其它进程短暂占用：重试几次，仍失败只警告（不影响验收结论）。
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      if (!fs.existsSync(HOME_DIR)) {
        removed = true;
        break;
      }
      fs.rmSync(HOME_DIR, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
      removed = !fs.existsSync(HOME_DIR);
    } catch (error) {
      if (attempt === 3) console.log(`\n⚠ 清理临时目录失败（可能被其它进程占用）：${error.message}`);
    }
    if (!removed) {
      const waitUntil = Date.now() + 150;
      while (Date.now() < waitUntil) {
        /* 同步短等，给占用进程一点时间释放句柄 */
      }
    }
  }
  try {
    if (fs.existsSync(TMP_DIR) && fs.readdirSync(TMP_DIR).length === 0) fs.rmdirSync(TMP_DIR);
  } catch {
    /* ignore */
  }
  return removed;
}

function printSummary(cleaned) {
  const pass = results.filter((item) => item.status === 'PASS').length;
  const fail = results.filter((item) => item.status === 'FAIL');
  const skipped = results.filter((item) => item.status === 'SKIP');
  const seconds = ((Date.now() - startedAt) / 1000).toFixed(2);

  console.log('\n════════════════════════════════════════════');
  console.log(
    `汇总：共 ${results.length} 项 · PASS ${pass} · FAIL ${fail.length} · SKIP ${skipped.length} · 耗时 ${seconds}s`,
  );
  if (skipped.length > 0) {
    console.log('SKIP（环境不满足，不算失败，但验收不完整）：');
    for (const item of skipped) console.log(`  - ${item.id} ${item.name}：${item.detail}`);
  }
  if (fail.length > 0) {
    console.log('FAIL 明细：');
    for (const item of fail) console.log(`  - ${item.id} ${item.name}：${item.detail}`);
    console.log('结果：失败（退出码 1）');
  } else {
    console.log(`结果：通过（退出码 0）${skipped.length > 0 ? '，但存在 SKIP，建议补齐环境后重跑' : ''}`);
  }
  console.log(`临时产物：${cleaned ? 'test/.tmp 下的截图/日志/进程输出已清理' : '未清理（见上）'}`);
  console.log('════════════════════════════════════════════');
}

/* ------------------------------------------------------------------ */
/* main                                                                */
/* ------------------------------------------------------------------ */

async function main() {
  fs.mkdirSync(TMP_DIR, { recursive: true });

  console.log('════════════════════════════════════════════');
  console.log(' dsh-game-control 最小验收（SPEC.md §6 八项 + v0.2 窗口截图 + v0.3 游戏自动化/技能库 + v0.4 JSONC + v0.5 Esc/多模板/进度 + v0.6 长循环/连接修复 + v0.6.1 单一轮数 + v0.6.2 模板超时修复）');
  console.log('════════════════════════════════════════════');
  console.log(`工作区：${ROOT}`);
  console.log(`Node：${process.version}（${process.execPath}）`);
  console.log('模式：只读检查 + 截图；不点击、不按键、不拖拽、不输入文本');
  if (VERBOSE) console.log('详细模式：--verbose');

  checkFiles();
  checkNodeSyntax();
  checkManifest();
  checkLocaleAndIcon();
  await checkPython();
  checkHostStatic();
  checkClientStatic();

  const cleaned = cleanup();
  printSummary(cleaned);
  process.exitCode = results.some((item) => item.status === 'FAIL') ? 1 : 0;
}

try {
  await main();
} catch (error) {
  console.error('\n验收脚本自身异常（这是测试脚本 bug，不是被测代码的失败）：');
  console.error(error && error.stack ? error.stack : error);
  try {
    cleanup();
  } catch {
    /* ignore */
  }
  process.exitCode = 1;
}
