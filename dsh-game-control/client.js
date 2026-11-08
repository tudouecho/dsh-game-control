/**
 * dsh-game-control · Client 半（浏览器侧）
 *
 * 结构：
 *   1. window.__ModuleLoader__.load 注册模块，React 从 require('react') 取，只用 React.createElement；
 *   2. locale 字典（zh 为主 / en 备用），apply 时 ctx.locale.register(NS, ...) 并 bind 出 t；
 *   3. 请求封装：GET /api/game-control/status、POST /api/game-control/exec；
 *   4. SidebarIcon: sidebar.panellist 左侧栏入口；Panel: main 主面板；
 *   5. apply(ctx): 注册 locale、注入 dgc-* 样式（ctx.effect 清理）、注册两个槽位。
 *
 * 约束：不 import 任何 Harness Client 包、不用 JSX、不改 document.body、不轮询动作/截图，
 * 面板只在挂载时 GET 一次 /status（用户可用「刷新」手动重取）。
 */
window.__ModuleLoader__.load({
  id: '@local/dsh-game-control',
  factory: (require) => {
    const React = require('react');
    const h = React.createElement;
    const { useState, useEffect, useRef } = React;

    const ID = 'game-control';
    const NS = 'gameControl.panel';
    const API_BASE = '/api/game-control';
    // 快捷按键：按住超过该毫秒数即转为 key_down，松开时补 key_up。
    const HOLD_DELAY_MS = 260;

    /* ------------------------------------------------------------------ *
     * locale 字典（中文为主，英文备用）
     * ------------------------------------------------------------------ */
    const dictionaries = {
      zh: {
        nav: '游戏操作',
        title: '游戏操作',
        subtitle: '在面板里直接操作鼠标、键盘并截图；所有动作由本机 Python 后端执行，只影响当前聚焦窗口。',
        safety: '不会后台自动操作：每个动作都需要你在这里点击触发，没有轮询，也不会自动注入输入。',
        busy: '执行中',
        ready: '就绪',
        notReady: '未就绪',
        unknown: '未知',
        error: '错误',
        loading: '加载中…',
        done: '完成',
        refresh: '刷新',
        refreshing: '刷新中…',
        clear: '清空',
        copy: '复制',
        copied: '已复制',
        copyFailed: '复制失败，请手动选择文本',
        yes: '是',
        no: '否',
        execFailed: '执行失败',
        statusTitle: '后端状态',
        statusBridge: '桥接状态',
        statusPythonPath: 'Python 路径',
        statusPythonVersion: 'Python 版本',
        statusPlatform: '平台',
        statusBackend: '输入后端',
        statusDpi: 'DPI 感知',
        statusScreen: '屏幕尺寸',
        statusPrimary: '主屏',
        statusPointer: '当前指针',
        statusShotDir: '截图目录',
        statusLogDir: '日志目录',
        statusBackendHint: 'Python 后端当前不可用：面板仍会显示，但所有动作都会失败。请检查 Python 路径与依赖，然后点「刷新」重试。',
        mouseTitle: '鼠标',
        x: 'X 坐标',
        y: 'Y 坐标',
        move: '移动',
        leftClick: '左键单击',
        rightClick: '右键单击',
        doubleClick: '双击',
        mouseDown: '按下',
        mouseUp: '抬起',
        drag: '拖拽',
        dragToX: '终点 X',
        dragToY: '终点 Y',
        scrollUp: '滚轮上',
        scrollDown: '滚轮下',
        position: '读取当前位置',
        needPoint: '请填写有效的 X / Y 数字坐标',
        needDrag: '请填写有效的终点 X / Y 数字坐标',
        mouseHint: '坐标是物理像素，原点为主屏左上角；多屏时允许负坐标。滚轮不填坐标则在当前指针位置滚动。',
        keyboardTitle: '键盘',
        keyLabel: '单个按键',
        keyPlaceholder: '例如 enter 或 a',
        pressKey: '按键',
        comboLabel: '组合键',
        comboPlaceholder: '例如 ctrl+shift+s',
        sendCombo: '发送',
        textLabel: '输入文本',
        textPlaceholder: '要输入到当前窗口的文本…',
        typeText: '输入文本',
        quickKeys: '快捷按键',
        quickHint: '点击 = 单次按键；长按 = 按住，松开时抬起。',
        keySpace: '空格',
        needKey: '请填写按键名',
        needCombo: '请填写组合键，例如 ctrl+shift+s',
        comboLimited: '组合键最多 4 个键',
        needText: '请填写要输入的文本',
        textTooLong: '文本不能超过 10000 个字符',
        shotTitle: '截图',
        shotFull: '全屏截图',
        shotRegion: '区域截图',
        wantBase64: '返回 base64（用于预览）',
        regionX: '区域 X',
        regionY: '区域 Y',
        regionWidth: '区域宽',
        regionHeight: '区域高',
        needRegion: '请填写有效的区域 X / Y / 宽 / 高（宽高需大于 0）',
        shotPreview: '截图预览',
        shotNoPreview: '本次没有返回 base64，暂无预览。',
        shotPath: '保存路径',
        noShot: '还没有截图。',
        shotMeta: '尺寸/大小',
        shotHint: '截图会保存到后端截图目录；勾选 base64 才能在面板里预览。',
        windowTitle: '窗口截图（后台窗口）',
        windowsRefresh: '刷新窗口列表',
        windowsRefreshing: '正在刷新窗口列表…',
        windowsNotLoaded: '尚未加载窗口列表',
        windowsEmpty: '没有可枚举的窗口',
        windowsCount: '已加载窗口数',
        windowPick: '选择窗口',
        foregroundTag: '前台',
        minimizedTag: '最小化',
        windowEngine: '抓取引擎',
        engineAuto: 'auto（自动回退）',
        engineWgc: 'wgc（后台抓取，推荐）',
        enginePrintwindow: 'printwindow（GDI 兼容）',
        engineScreen: 'screen（仅可见区域）',
        windowCapture: '抓取窗口',
        windowCapturing: '正在抓取窗口…',
        needWindow: '请先刷新并选择一个窗口',
        windowNoShot: '还没有窗口截图。',
        windowHint: 'wgc 可在窗口被遮挡或在后台时抓取；screen 只能拿到屏幕上的可见像素。抓取不会激活目标窗口，也不会注入键鼠。',
        windowActualEngine: '实际引擎',
        windowFallback: '回退原因',
        windowWarnings: '提示',
        windowUnsupported: '窗口枚举与后台抓取仅支持 Windows（其它平台会返回 UNSUPPORTED_PLATFORM）。',
        activateWindow: '激活/前置窗口',
        activatingWindow: '正在激活…',
        activateSuccess: '已前置窗口',
        activationAttempts: '尝试次数',
        needSelectedWindow: '请先刷新并选择一个窗口',
        circleTitle: '圆形轨迹',
        circleCenterX: '圆心 X',
        circleCenterY: '圆心 Y',
        circleRadius: '半径',
        circlePeriod: '一圈耗时 (ms)',
        circleDuration: '总时长 (ms)',
        circleDirection: '方向',
        circleCw: '顺时针 cw',
        circleCcw: '逆时针 ccw',
        circleSamples: '每秒采样',
        circleButton: '按住按键',
        circleBtnNone: 'none（不按键）',
        circleBtnLeft: 'left（按住左键）',
        circleBtnRight: 'right（按住右键）',
        circleBtnMiddle: 'middle（按住中键）',
        abortKey: '中止键',
        circleStart: '开始（会阻塞）',
        circleStarting: '画圆中…',
        circleFillFromWindow: '用当前窗口填圆心/半径',
        circleNeedWindow: '请先加载窗口列表并选择一个窗口（需要 clientRect）',
        circleNeedParams: '请填写有效的圆心 X/Y 与半径',
        circleBlockHint: '点击后阻塞 durationMs 并注入鼠标移动；按中止键（默认 Esc）立即停止。选中窗口时会传 hwnd，前台变化自动停止。',
        circleSamplesResult: '采样数',
        circleLoops: '圈数',
        circleActualDuration: '实际时长',
        circleAborted: '已中止',
        circleAbortReason: '中止原因',
        dryRun: '干跑',
        findTitle: '模板找图',
        findTemplate: '模板路径',
        findTemplatePlaceholder: 'keep-on-mining/start_game.png',
        findThreshold: '阈值（0–1）',
        findRegion: '搜索区域',
        findRegionWindow: '当前窗口 clientRect',
        findRegionScreen: '主屏',
        findRun: '查找',
        findRunning: '查找中…',
        findFound: '命中',
        findNotFound: '未找到匹配（found:false）',
        findMatches: '匹配数',
        findScore: '分数',
        findScale: '缩放',
        findClickPoint: '点击该点',
        findNeedTemplate: '请填写模板路径',
        findNeedWindow: '搜索区域选「当前窗口」时，请先选择一个窗口',
        findInvalidThreshold: '阈值需在 0–1 之间',
        findHint: '模板路径相对 <GAME_CONTROL_HOME>/templates/，也可用绝对路径；只读操作，不会激活窗口。',
        findElapsed: '耗时',
        skillTitle: '技能库',
        skillRefresh: '刷新技能列表',
        skillNew: '新建',
        skillListLabel: '技能列表',
        skillListEmpty: '还没有技能；点「新建」或让 agent 用 game_skill 创建。',
        skillNotLoaded: '尚未加载技能列表',
        skillFieldTitle: '标题',
        skillFieldGame: '游戏',
        skillStepsView: '步骤列表（只读）',
        skillStepsViewEmpty: '还没有步骤；保存配置文件后这里会显示步骤列表。',
        skillStepLabel: '第 {n} 步',
        skillConfigTitle: '完整配置文件（skill.json，JSONC，含中文注释）',
        skillConfigHint: '保存时自动补全每步参数并重新生成中文注释；手工注释会被覆盖，自定义备注请写 note 字段。',
        skillJsoncHint: 'JSONC 支持 // 行注释与 /* */ 块注释；保存后会用归一化结果重新渲染整份文件，参数与注释以返回内容为准。',
        skillContentEmpty: '配置文件内容不能为空',
        skillContentTooLong: '配置文件内容不能超过 1,000,000 字符',
        skillNewTemplateNote: '已生成模板：改好 id / title / steps 后点保存。',
        skillSave: '保存',
        skillSaving: '保存中…',
        skillCreated: '已创建技能',
        skillSaved: '已保存',
        skillBackupPath: '备份文件',
        skillStepCount: '步骤数',
        skillBackupCount: '备份数',
        skillUpdatedAt: '更新时间',
        skillVersions: '历史版本',
        skillVersionPick: '选择版本',
        skillVersionLatest: 'latest（最新备份）',
        skillRestore: '恢复',
        skillRestoring: '恢复中…',
        skillRestored: '已恢复，当前版本已自动备份',
        skillVersionsEmpty: '暂无历史版本',
        skillNeedId: '请先选择或保存一个技能',
        skillRunConfig: '运行设置',
        skillLoopRounds: '轮数 rounds',
        skillLoopRoundsHint: '1 = 单次运行；>1 = 循环（每轮独立执行）',
        skillLoopInterval: '轮间隔 intervalMs',
        skillLoopIntervalHint: '仅轮数 > 1 时生效',
        skillLoopStop: '停止',
        skillLoopStopping: '正在停止…',
        skillLoopImmediate: '立即停止（终止当前轮）',
        skillLoopImmediateHint: '勾选「立即停止」后再点「停止」会终止当前轮，当前轮记为失败。',
        skillLoopContinueOnError: '失败后继续',
        skillLoopFailureHint: '模板等待超时会在该步失败并保存失败截图（日志目录 skill-fail-*.png）；勾选「失败后继续」可让长循环跳过偶发失败轮。',
        skillErrorCode: '错误码',
        skillScreenshot: '失败截图',
        skillLoopProgressText: '第 {current}/{rounds} 轮 · 已完成 {completed} · 失败 {failed} · 当前轮已等待 {sec} 秒',
        skillLoopRequested: '已请求停止（当前轮结束后停止）',
        skillLoopResults: '每轮结果',
        skillLoopNoResults: '还没有轮次结果。',
        skillLoopSummary: '循环结束',
        skillLoopCompleted: '完成轮数',
        skillLoopFailed: '失败轮数',
        skillLoopLastError: '最后错误',
        skillLoopNeedRounds: '轮数需为 1–10000 的整数',
        skillLoopNeedInterval: '轮间隔需为 0–60000 的整数',
        skillLoopRound: '第 {n} 轮',
        skillConnectionLost: '后端连接中断（可能因超时被杀/被停止）',
        skillResetState: '重置状态',
        skillResetHint: '重置只清除本地面板的「运行中」并恢复按钮，不会停止后台循环；如需停止请先点「停止」。',
        skillRun: '运行',
        skillRunOverall: '总结果',
        skillRunSuccess: '成功',
        skillRunFailed: '失败',
        skillRunAborted: '已中止',
        skillRunDuration: '耗时',
        skillEstimate: '预计时长',
        skillRunSteps: '步骤结果',
        skillRunNoSteps: '没有步骤结果',
        skillFailedStep: '失败步骤',
        skillDelete: '删除',
        skillDeleteConfirm: '确认删除',
        skillDeleted: '已软删除',
        skillTrashPath: '回收站路径',
        skillHint: '技能存在 <GAME_CONTROL_HOME>/skills/；每次保存/恢复/删除前自动备份。步骤 JSON 支持 $window.* 与 $<saveAs>.* 变量。',
        skillDryRunNote: '干跑只做静态校验与变量规划，不执行任何动作。',
        skillStructuredTitle: '结构化步骤（表格）',
        skillStructuredHint: '表格保存会自动补全每步参数并重新生成中文注释；「高级」JSONC 编辑区用于直接改整份文件。',
        skillAddStep: '＋ 新增步骤',
        skillStepAction: '操作',
        skillStepParams: '关键条件/参数',
        skillStepWaitCol: '等待(ms)',
        skillStepNoteCol: '备注',
        skillStepUp: '上移',
        skillStepDown: '下移',
        skillStepDelete: '删除该步',
        skillStepJsonInvalid: '该步参数不是合法 JSON',
        skillStepActionRequired: '每步都必须有 action',
        skillNeedSteps: '至少需要 1 个步骤',
        skillNeedTitle: '请填写标题',
        skillSaveContent: '保存 JSONC 内容',
        skillAdvancedTitle: '高级：完整配置文件（JSONC，含中文注释）',
        skillChars: '{n} 字符',
        skillTemplatesHint: '多个模板用英文逗号分隔，任一命中即算成功。',
        skillCurrentStepText: '当前步骤 {index}/{total} {action}',
        skillRunLocked: '运行中：保存/刷新/新建/删除/恢复等技能操作已暂时禁用。',
        skillEditorTitle: '技能编辑器',
        skillNoEditor: '选择或新建一个技能开始编辑。',
        skillPickPlaceholder: '选择技能…',
        cancel: '取消',
        logTitle: '最近操作日志',
        logEmpty: '还没有操作记录。',
        logLimit: '最多保留最近 30 条。',
      },
      en: {
        nav: 'Game Control',
        title: 'Game Control',
        subtitle: 'Operate the mouse and keyboard and take screenshots here; every action runs in the local Python backend and only affects the focused window.',
        safety: 'No background input: each action is triggered by a click here; no polling and no automatic input injection.',
        busy: 'Running',
        ready: 'Ready',
        notReady: 'Not ready',
        unknown: 'Unknown',
        error: 'Error',
        loading: 'Loading…',
        done: 'Done',
        refresh: 'Refresh',
        refreshing: 'Refreshing…',
        clear: 'Clear',
        copy: 'Copy',
        copied: 'Copied',
        copyFailed: 'Copy failed; select the text manually',
        yes: 'Yes',
        no: 'No',
        execFailed: 'Action failed',
        statusTitle: 'Backend status',
        statusBridge: 'Bridge',
        statusPythonPath: 'Python path',
        statusPythonVersion: 'Python version',
        statusPlatform: 'Platform',
        statusBackend: 'Input backend',
        statusDpi: 'DPI aware',
        statusScreen: 'Screen size',
        statusPrimary: 'Primary display',
        statusPointer: 'Pointer',
        statusShotDir: 'Screenshot dir',
        statusLogDir: 'Log dir',
        statusBackendHint: 'The Python backend is unavailable. The panel still renders, but actions will fail. Check the Python path and dependencies, then press Refresh.',
        mouseTitle: 'Mouse',
        x: 'X coordinate',
        y: 'Y coordinate',
        move: 'Move',
        leftClick: 'Left click',
        rightClick: 'Right click',
        doubleClick: 'Double click',
        mouseDown: 'Button down',
        mouseUp: 'Button up',
        drag: 'Drag',
        dragToX: 'To X',
        dragToY: 'To Y',
        scrollUp: 'Scroll up',
        scrollDown: 'Scroll down',
        position: 'Read position',
        needPoint: 'Enter valid numeric X / Y coordinates',
        needDrag: 'Enter a valid numeric target X / Y',
        mouseHint: 'Coordinates are physical pixels with the primary screen top-left as origin; negative values are allowed on multi-monitor setups. Scrolling without coordinates uses the current pointer position.',
        keyboardTitle: 'Keyboard',
        keyLabel: 'Single key',
        keyPlaceholder: 'e.g. enter or a',
        pressKey: 'Press',
        comboLabel: 'Shortcut',
        comboPlaceholder: 'e.g. ctrl+shift+s',
        sendCombo: 'Send',
        textLabel: 'Text',
        textPlaceholder: 'Text to type into the focused window…',
        typeText: 'Type text',
        quickKeys: 'Quick keys',
        quickHint: 'Click = press once; hold = keep the key down until you release.',
        keySpace: 'Space',
        needKey: 'Enter a key name',
        needCombo: 'Enter a shortcut such as ctrl+shift+s',
        comboLimited: 'A shortcut uses at most 4 keys',
        needText: 'Enter the text to type',
        textTooLong: 'Text cannot exceed 10,000 characters',
        shotTitle: 'Screenshots',
        shotFull: 'Full screen',
        shotRegion: 'Region',
        wantBase64: 'Return base64 (for preview)',
        regionX: 'Region X',
        regionY: 'Region Y',
        regionWidth: 'Region width',
        regionHeight: 'Region height',
        needRegion: 'Enter a valid region X / Y / width / height (width and height must be positive)',
        shotPreview: 'Screenshot preview',
        shotNoPreview: 'No base64 was returned, so there is no preview.',
        shotPath: 'Saved path',
        noShot: 'No screenshot yet.',
        shotMeta: 'Size / bytes',
        shotHint: 'Screenshots are saved in the backend screenshot directory; enable base64 to preview them here.',
        windowTitle: 'Window capture (background)',
        windowsRefresh: 'Refresh windows',
        windowsRefreshing: 'Refreshing windows…',
        windowsNotLoaded: 'Window list not loaded',
        windowsEmpty: 'No windows found',
        windowsCount: 'Loaded windows',
        windowPick: 'Window',
        foregroundTag: 'foreground',
        minimizedTag: 'minimized',
        windowEngine: 'Engine',
        engineAuto: 'auto (fallback chain)',
        engineWgc: 'wgc (background, recommended)',
        enginePrintwindow: 'printwindow (GDI fallback)',
        engineScreen: 'screen (visible only)',
        windowCapture: 'Capture window',
        windowCapturing: 'Capturing window…',
        needWindow: 'Refresh and select a window first',
        windowNoShot: 'No window capture yet.',
        windowHint: 'wgc captures occluded or background windows; screen only sees pixels visible on screen. Capturing never activates the target window or injects input.',
        windowActualEngine: 'Used engine',
        windowFallback: 'Fallback reason',
        windowWarnings: 'Warnings',
        windowUnsupported: 'Window enumeration and background capture are Windows-only (other platforms return UNSUPPORTED_PLATFORM).',
        activateWindow: 'Activate / bring to front',
        activatingWindow: 'Activating…',
        activateSuccess: 'Window is in the foreground',
        activationAttempts: 'Attempts',
        needSelectedWindow: 'Refresh and select a window first',
        circleTitle: 'Circular motion',
        circleCenterX: 'Center X',
        circleCenterY: 'Center Y',
        circleRadius: 'Radius',
        circlePeriod: 'Period (ms)',
        circleDuration: 'Duration (ms)',
        circleDirection: 'Direction',
        circleCw: 'Clockwise (cw)',
        circleCcw: 'Counter-clockwise (ccw)',
        circleSamples: 'Samples/s',
        circleButton: 'Hold button',
        circleBtnNone: 'none (no button)',
        circleBtnLeft: 'left (hold)',
        circleBtnRight: 'right (hold)',
        circleBtnMiddle: 'middle (hold)',
        abortKey: 'Abort key',
        circleStart: 'Start (blocking)',
        circleStarting: 'Drawing…',
        circleFillFromWindow: 'Fill center/radius from window',
        circleNeedWindow: 'Load the window list and select a window first (needs clientRect)',
        circleNeedParams: 'Enter a valid center X/Y and radius',
        circleBlockHint: 'Blocks for durationMs and injects mouse movement; press the abort key (default Esc) to stop. When a window is selected its hwnd is passed so a foreground change stops the motion.',
        circleSamplesResult: 'Samples',
        circleLoops: 'Loops',
        circleActualDuration: 'Actual duration',
        circleAborted: 'Aborted',
        circleAbortReason: 'Abort reason',
        dryRun: 'Dry run',
        findTitle: 'Template search',
        findTemplate: 'Template path',
        findTemplatePlaceholder: 'keep-on-mining/start_game.png',
        findThreshold: 'Threshold (0–1)',
        findRegion: 'Search region',
        findRegionWindow: 'Current window clientRect',
        findRegionScreen: 'Primary screen',
        findRun: 'Find',
        findRunning: 'Searching…',
        findFound: 'Found',
        findNotFound: 'No match (found:false)',
        findMatches: 'Matches',
        findScore: 'Score',
        findScale: 'Scale',
        findClickPoint: 'Click this point',
        findNeedTemplate: 'Enter a template path',
        findNeedWindow: 'Select a window before searching inside the current window',
        findInvalidThreshold: 'Threshold must be between 0 and 1',
        findHint: 'Template paths are relative to <GAME_CONTROL_HOME>/templates/ or absolute; searching is read-only and never activates a window.',
        findElapsed: 'Elapsed',
        skillTitle: 'Skill library',
        skillRefresh: 'Refresh skills',
        skillNew: 'New',
        skillListLabel: 'Skill list',
        skillListEmpty: 'No skills yet; press New or let the agent create one with game_skill.',
        skillNotLoaded: 'Skill list not loaded',
        skillFieldTitle: 'Title',
        skillFieldGame: 'Game',
        skillStepsView: 'Steps (read-only)',
        skillStepsViewEmpty: 'No steps yet; the step list appears here after saving the config file.',
        skillStepLabel: 'Step {n}',
        skillConfigTitle: 'Full config file (skill.json, JSONC with Chinese comments)',
        skillConfigHint: 'Saving fills in every step parameter and regenerates the Chinese comments; manual comment edits are overwritten — put custom notes in the note field.',
        skillJsoncHint: 'JSONC supports // line and /* */ block comments; after saving, the whole file is re-rendered from the normalized data, so returned content is authoritative.',
        skillContentEmpty: 'Config file content cannot be empty',
        skillContentTooLong: 'Config file content cannot exceed 1,000,000 characters',
        skillNewTemplateNote: 'Template created: edit id / title / steps, then press Save.',
        skillSave: 'Save',
        skillSaving: 'Saving…',
        skillCreated: 'Skill created',
        skillSaved: 'Saved',
        skillBackupPath: 'Backup file',
        skillStepCount: 'Steps',
        skillBackupCount: 'Backups',
        skillUpdatedAt: 'Updated',
        skillVersions: 'Versions',
        skillVersionPick: 'Version',
        skillVersionLatest: 'latest (newest backup)',
        skillRestore: 'Restore',
        skillRestoring: 'Restoring…',
        skillRestored: 'Restored; the previous version was backed up',
        skillVersionsEmpty: 'No versions yet',
        skillNeedId: 'Select or save a skill first',
        skillRunConfig: 'Run options',
        skillLoopRounds: 'Rounds',
        skillLoopRoundsHint: '1 = single run; >1 = loop (each round runs separately)',
        skillLoopInterval: 'Round interval (ms)',
        skillLoopIntervalHint: 'Only used when rounds > 1',
        skillLoopStop: 'Stop',
        skillLoopStopping: 'Stopping…',
        skillLoopImmediate: 'Stop immediately (kill current round)',
        skillLoopImmediateHint: 'With "Stop immediately" checked, Stop kills the current round and records it as failed.',
        skillLoopContinueOnError: 'Continue after failure',
        skillLoopFailureHint: 'A template wait timeout fails that step and saves a failure screenshot (skill-fail-*.png in the log dir); check "Continue after failure" so long loops skip occasional failed rounds.',
        skillErrorCode: 'Error code',
        skillScreenshot: 'Failure screenshot',
        skillLoopProgressText: 'Round {current}/{rounds} · completed {completed} · failed {failed} · {sec}s in current round',
        skillLoopRequested: 'Stop requested (stops after the current round)',
        skillLoopResults: 'Round results',
        skillLoopNoResults: 'No round results yet.',
        skillLoopSummary: 'Loop finished',
        skillLoopCompleted: 'Completed rounds',
        skillLoopFailed: 'Failed rounds',
        skillLoopLastError: 'Last error',
        skillLoopNeedRounds: 'Rounds must be an integer between 1 and 10000',
        skillLoopNeedInterval: 'Round interval must be an integer between 0 and 60000',
        skillLoopRound: 'Round {n}',
        skillConnectionLost: 'Backend connection lost (possibly killed or stopped due to timeout)',
        skillResetState: 'Reset state',
        skillResetHint: 'Reset only clears the local running state and restores buttons; it does not stop a background loop — use Stop first.',
        skillRun: 'Run',
        skillRunOverall: 'Overall',
        skillRunSuccess: 'Success',
        skillRunFailed: 'Failed',
        skillRunAborted: 'Aborted',
        skillRunDuration: 'Duration',
        skillEstimate: 'Estimated',
        skillRunSteps: 'Step results',
        skillRunNoSteps: 'No step results',
        skillFailedStep: 'Failed step',
        skillDelete: 'Delete',
        skillDeleteConfirm: 'Confirm delete',
        skillDeleted: 'Soft-deleted',
        skillTrashPath: 'Trash path',
        skillHint: 'Skills live under <GAME_CONTROL_HOME>/skills/; every save/restore/delete backs up first. Steps support $window.* and $<saveAs>.* variables.',
        skillDryRunNote: 'Dry run only validates and plans variables without executing any action.',
        skillStructuredTitle: 'Structured steps (table)',
        skillStructuredHint: 'Saving the table fills in every step parameter and regenerates the Chinese comments; the Advanced JSONC editor edits the whole file directly.',
        skillAddStep: '+ Add step',
        skillStepAction: 'Action',
        skillStepParams: 'Key parameters',
        skillStepWaitCol: 'Wait (ms)',
        skillStepNoteCol: 'Note',
        skillStepUp: 'Up',
        skillStepDown: 'Down',
        skillStepDelete: 'Delete step',
        skillStepJsonInvalid: 'This step parameters are not valid JSON',
        skillStepActionRequired: 'Every step needs an action',
        skillNeedSteps: 'At least one step is required',
        skillNeedTitle: 'Enter a title',
        skillSaveContent: 'Save JSONC content',
        skillAdvancedTitle: 'Advanced: full config file (JSONC with Chinese comments)',
        skillChars: '{n} chars',
        skillTemplatesHint: 'Separate multiple templates with commas; any match counts.',
        skillCurrentStepText: 'Current step {index}/{total} {action}',
        skillRunLocked: 'Running: skill save/refresh/new/delete/restore are temporarily disabled.',
        skillEditorTitle: 'Skill editor',
        skillNoEditor: 'Select or create a skill to edit.',
        skillPickPlaceholder: 'Pick a skill…',
        cancel: 'Cancel',
        logTitle: 'Recent actions',
        logEmpty: 'No actions yet.',
        logLimit: 'The latest 30 entries are kept.',
      },
    };

    /* ------------------------------------------------------------------ *
     * 样式：类名统一 dgc- 前缀，颜色只用 --dsw-alias-* 主题 token（带 fallback）
     * ------------------------------------------------------------------ */
    const CSS = [
      '.dgc-wrap{display:flex;flex-direction:column;gap:12px;width:100%;max-width:100%;min-width:0;height:100%;max-height:100%;overflow:auto;box-sizing:border-box;padding:2px 2px 28px;color:var(--dsw-alias-label-primary,inherit);font-size:13px;line-height:1.55;overflow-wrap:anywhere}',
      '.dgc-wrap *{box-sizing:border-box}',
      '.dgc-header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap}',
      '.dgc-title{margin:0;font-size:18px;line-height:1.35;font-weight:650}',
      '.dgc-sub{margin:3px 0 0;font-size:12.5px;color:var(--dsw-alias-label-secondary,inherit);max-width:78ch}',
      '.dgc-header-side{display:flex;align-items:center;gap:8px;flex-wrap:wrap}',
      '.dgc-badge{display:inline-flex;align-items:center;gap:4px;font-size:11.5px;line-height:1.5;padding:2px 9px;border-radius:999px;border:1px solid var(--dsw-alias-border-l2,#8884);color:var(--dsw-alias-label-secondary,inherit);white-space:nowrap}',
      '.dgc-badge[data-tone="ok"]{color:var(--dsw-alias-state-success-primary,#16a34a);border-color:var(--dsw-alias-state-success-primary,#16a34a)}',
      '.dgc-badge[data-tone="warn"]{color:var(--dsw-alias-state-warning-primary,#d97706);border-color:var(--dsw-alias-state-warning-primary,#d97706)}',
      '.dgc-badge[data-tone="error"]{color:var(--dsw-alias-state-error-primary,#dc2626);border-color:var(--dsw-alias-state-error-primary,#dc2626)}',
      '.dgc-result{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;border:1px solid var(--dsw-alias-border-l2,#8884);border-radius:10px;padding:7px 12px;font-size:12.5px;background:var(--dsw-alias-bg-overlay,#8881);min-width:0}',
      '.dgc-result[data-ok="0"]{color:var(--dsw-alias-state-error-primary,#dc2626);border-color:var(--dsw-alias-state-error-primary,#dc2626)}',
      '.dgc-result-text{min-width:0;word-break:break-all}',
      '.dgc-card{border:1px solid var(--dsw-alias-border-l2,#8883);border-radius:12px;padding:12px 14px;background:var(--dsw-alias-bg-overlay,#8881);display:flex;flex-direction:column;gap:10px;min-width:0}',
      '.dgc-card-head{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;min-width:0}',
      '.dgc-card-title{margin:0;font-size:13.5px;font-weight:600}',
      '.dgc-row{display:flex;align-items:flex-end;gap:8px;flex-wrap:wrap;min-width:0}',
      '.dgc-field{display:flex;flex-direction:column;gap:4px;min-width:0;flex:0 1 118px}',
      '.dgc-field-label{font-size:11.5px;line-height:1.4;color:var(--dsw-alias-label-tertiary,inherit);white-space:nowrap}',
      '.dgc-input,.dgc-textarea{width:100%;min-width:0;font:inherit;font-size:12.5px;color:var(--dsw-alias-label-primary,inherit);background:transparent;border:1px solid var(--dsw-alias-border-l2,#8884);border-radius:8px;padding:5px 8px;min-height:30px}',
      '.dgc-input:focus-visible,.dgc-textarea:focus-visible{outline:2px solid var(--dsw-alias-label-secondary,#888);outline-offset:1px}',
      '.dgc-textarea{min-height:64px;resize:vertical;flex:1 1 240px}',
      '.dgc-btn{appearance:none;font:inherit;font-size:12.5px;line-height:1.4;color:var(--dsw-alias-label-primary,inherit);background:var(--dsw-alias-bg-overlay,#8881);border:1px solid var(--dsw-alias-border-l2,#8884);border-radius:8px;padding:6px 11px;min-height:30px;cursor:pointer;white-space:nowrap}',
      '.dgc-btn:not(:disabled):hover{background:var(--dsw-alias-interactive-bg-hover,#8882)}',
      '.dgc-btn[data-primary="1"]{background:var(--dsw-alias-label-primary,inherit);border-color:var(--dsw-alias-label-primary,transparent);color:var(--dsw-alias-bg-layer-1,#fff);font-weight:600}',
      '.dgc-btn[data-primary="1"]:not(:disabled):hover{opacity:.88}',
      '.dgc-btn:disabled{opacity:.45;cursor:not-allowed}',
      '.dgc-quick{display:flex;flex-wrap:wrap;gap:6px;min-width:0}',
      '.dgc-quick-btn{appearance:none;font:inherit;font-size:12px;min-width:38px;min-height:30px;padding:4px 8px;color:inherit;background:transparent;border:1px solid var(--dsw-alias-border-l2,#8884);border-radius:8px;cursor:pointer;user-select:none;-webkit-user-select:none;touch-action:none;white-space:nowrap}',
      '.dgc-quick-btn:hover{background:var(--dsw-alias-interactive-bg-hover,#8882)}',
      '.dgc-quick-btn:active{background:var(--dsw-alias-bg-layer-2,#8882)}',
      '.dgc-check{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--dsw-alias-label-secondary,inherit);cursor:pointer;white-space:nowrap}',
      '.dgc-check input{width:15px;height:15px;margin:0;accent-color:var(--dsw-alias-label-primary,inherit)}',
      '.dgc-status-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:6px 14px;min-width:0}',
      '.dgc-kv{display:flex;flex-direction:column;gap:1px;min-width:0;font-size:12px}',
      '.dgc-kv-label{font-size:11px;line-height:1.4;color:var(--dsw-alias-label-tertiary,inherit);white-space:nowrap}',
      '.dgc-kv-value{color:var(--dsw-alias-label-primary,inherit);word-break:break-all;overflow-wrap:anywhere}',
      '.dgc-mono{font-family:ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace;font-size:11.5px}',
      '.dgc-error{margin:0;font-size:12px;color:var(--dsw-alias-state-error-primary,#dc2626);white-space:pre-wrap;word-break:break-word}',
      '.dgc-pre{margin:0;font-family:ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace;font-size:11px;line-height:1.45;white-space:pre-wrap;word-break:break-word;max-height:120px;overflow:auto;color:var(--dsw-alias-label-tertiary,inherit)}',
      '.dgc-note{margin:0;font-size:11.5px;color:var(--dsw-alias-label-tertiary,inherit)}',
      '.dgc-hint{margin:0;font-size:11.5px;color:var(--dsw-alias-label-secondary,inherit)}',
      '.dgc-shot{display:flex;flex-direction:column;gap:8px;min-width:0}',
      '.dgc-shot-img{display:block;max-width:100%;height:auto;max-height:420px;border-radius:8px;border:1px solid var(--dsw-alias-border-l2,#8884);object-fit:contain;background:var(--dsw-alias-bg-layer-2,#8881)}',
      '.dgc-grow{flex:1 1 220px;min-width:120px}',
      '.dgc-window-select{flex:1 1 320px;min-width:200px}',
      '.dgc-window-select select{width:100%}',
      '.dgc-warn{margin:0;font-size:11.5px;color:var(--dsw-alias-state-warning-primary,#d97706);white-space:pre-wrap;word-break:break-word}',
      '.dgc-log{display:flex;flex-direction:column;max-height:240px;overflow:auto;min-width:0}',
      '.dgc-log-item{display:flex;gap:8px;align-items:baseline;font-size:12px;padding:4px 0;border-bottom:1px dashed var(--dsw-alias-border-l2,#8883);min-width:0}',
      '.dgc-log-item:last-child{border-bottom:0}',
      '.dgc-log-item[data-ok="0"] .dgc-log-msg{color:var(--dsw-alias-state-error-primary,#dc2626)}',
      '.dgc-log-time{color:var(--dsw-alias-label-tertiary,inherit);font-variant-numeric:tabular-nums;flex:0 0 auto}',
      '.dgc-log-action{color:var(--dsw-alias-label-secondary,inherit);flex:0 0 auto}',
      '.dgc-log-msg{min-width:0;word-break:break-all}',
      '.dgc-field-wide{flex:1 1 320px;min-width:220px}',
      '.dgc-field-full{flex:1 1 100%;min-width:0}',
      '.dgc-code{font-family:ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace;font-size:11.5px;line-height:1.5;min-height:120px;resize:vertical;width:100%}',
      '.dgc-textarea.dgc-code{min-height:140px;flex:1 1 100%}',
      '.dgc-code-tall{min-height:380px;height:auto}',
      '.dgc-step-comment{min-width:0;flex:1 1 280px;color:var(--dsw-alias-label-secondary,inherit);white-space:pre-wrap;word-break:break-word}',
      '.dgc-step-list-compact{max-height:150px}',
      '.dgc-step-table{display:flex;flex-direction:column;gap:4px;min-width:0;overflow-x:auto;padding-bottom:2px}',
      '.dgc-step-row{display:grid;grid-template-columns:26px 122px minmax(250px,1fr) 78px minmax(130px,200px) 84px;gap:6px;align-items:center;min-width:740px;padding:3px 0;border-bottom:1px dashed var(--dsw-alias-border-l2,#8883)}',
      '.dgc-step-row:last-child{border-bottom:0}',
      '.dgc-step-row-head{font-size:11px;font-weight:600;color:var(--dsw-alias-label-tertiary,inherit);border-bottom:1px solid var(--dsw-alias-border-l2,#8883);padding-bottom:4px}',
      '.dgc-step-cells{display:flex;flex-wrap:wrap;gap:4px;align-items:center;min-width:0}',
      '.dgc-cell{flex:0 0 auto}',
      '.dgc-cell-num{width:74px}',
      '.dgc-cell-sm{width:108px}',
      '.dgc-cell-list{width:164px}',
      '.dgc-cell-text{width:190px}',
      '.dgc-cell-wide{width:100%;min-width:220px}',
      '.dgc-cell-action{width:118px}',
      '.dgc-step-ops{display:flex;gap:4px;flex-wrap:nowrap;justify-content:flex-start}',
      '.dgc-details{border-top:1px dashed var(--dsw-alias-border-l2,#8883);padding-top:8px;min-width:0}',
      '.dgc-details>summary{cursor:pointer;list-style:none;display:flex;align-items:center;gap:6px;font-size:12.5px;font-weight:600;color:var(--dsw-alias-label-secondary,inherit)}',
      '.dgc-details>summary::-webkit-details-marker{display:none}',
      '.dgc-details>summary:after{content:"";display:block;margin-inline-start:auto;width:7px;height:7px;border-inline-end:1.75px solid currentColor;border-bottom:1.75px solid currentColor;transform:rotate(45deg);flex:none}',
      '.dgc-details[open]>summary:after{transform:rotate(225deg)}',
      '.dgc-details-body{display:flex;flex-direction:column;gap:8px;padding-top:8px;min-width:0}',
      '.dgc-subhead{margin:2px 0 0;font-size:12.5px;font-weight:600;color:var(--dsw-alias-label-secondary,inherit);white-space:nowrap}',
      '.dgc-meta{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:center;font-size:11.5px;color:var(--dsw-alias-label-secondary,inherit);min-width:0}',
      '.dgc-subblock{display:flex;flex-direction:column;gap:8px;border-top:1px dashed var(--dsw-alias-border-l2,#8883);padding-top:10px;min-width:0}',
      '.dgc-step-list{display:flex;flex-direction:column;max-height:220px;overflow:auto;min-width:0}',
      '.dgc-step{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;font-size:11.5px;padding:3px 0;border-bottom:1px dashed var(--dsw-alias-border-l2,#8883);min-width:0}',
      '.dgc-step:last-child{border-bottom:0}',
      '.dgc-step[data-ok="0"]{color:var(--dsw-alias-state-error-primary,#dc2626)}',
      '.dgc-step-fail{display:flex;flex-wrap:wrap;gap:6px;align-items:baseline;width:100%;padding:2px 0 0 18px;font-size:11.5px;color:var(--dsw-alias-label-secondary,inherit);min-width:0}',
      '@media (max-width:640px){.dgc-field{flex:1 1 100px}.dgc-card{padding:10px 12px}.dgc-wrap{padding:0 0 20px}}',
    ].join('\n');

    /* ------------------------------------------------------------------ *
     * fetch 封装：统一处理 JSON、HTTP 错误与超时（错误信息中文）
     * ------------------------------------------------------------------ */
    async function api(path, options = {}) {
      const { timeoutMs = 30000, ...init } = options;
      const controller = typeof AbortController === 'function' ? new AbortController() : null;
      const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
      let response;
      try {
        response = await fetch(API_BASE + path, {
          credentials: 'same-origin',
          cache: 'no-store',
          headers: { 'content-type': 'application/json' },
          ...init,
          signal: controller ? controller.signal : undefined,
        });
      } catch (error) {
        if (timer) clearTimeout(timer);
        if (error && error.name === 'AbortError') throw new Error('请求超时：后端可能繁忙或未响应');
        // 网络层失败（浏览器 fetch 抛出的错误）统一转中文，避免把原始错误直接抛给界面
        throw new Error('后端连接中断（可能因超时被杀/被停止）');
      }
      try {
        const text = await response.text();
        let value = null;
        try {
          value = text ? JSON.parse(text) : null;
        } catch {
          value = null;
        }
        if (!response.ok) {
          const info = value && value.error ? value.error : null;
          const message = info && (info.message || info.code) ? info.message || info.code : `HTTP ${response.status}`;
          const error = new Error(message);
          error.code = info && info.code ? info.code : 'HTTP_ERROR';
          error.hint = info && info.hint ? info.hint : '';
          throw error;
        }
        if (!value || typeof value !== 'object') throw new Error('响应不是有效的 JSON');
        return value;
      } finally {
        if (timer) clearTimeout(timer);
      }
    }

    /* ------------------------------------------------------------------ *
     * 纯展示 / 格式化辅助
     * ------------------------------------------------------------------ */

    // 侧栏图标：自绘手柄 + 鼠标，使用 currentColor，active 时描边更亮更粗。
    function SidebarIcon({ size = 20, active = false } = {}) {
      const pixel = Number(size) > 0 ? Number(size) : 20;
      return h('svg', {
        width: pixel,
        height: pixel,
        viewBox: '0 0 24 24',
        fill: 'none',
        stroke: 'currentColor',
        strokeWidth: active ? 2 : 1.75,
        strokeLinecap: 'round',
        strokeLinejoin: 'round',
        'aria-hidden': true,
        focusable: false,
        'data-active': active ? '1' : '0',
        style: { color: 'currentColor', opacity: active ? 1 : 0.88, display: 'block' },
      },
      // 手柄主体
      h('path', {
        key: 'pad',
        d: 'M6.8 7.2h10.4a4.6 4.6 0 0 1 4.5 3.7l.8 4.2a3.1 3.1 0 0 1-5.4 2.6l-1.4-1.6h-6l-1.4 1.6A3.1 3.1 0 0 1 2.9 15l.8-4.2a4.6 4.6 0 0 1 3.1-3.6Z',
      }),
      // 十字方向键
      h('path', { key: 'dpad', d: 'M8.6 10.4v3.4M6.9 12.1h3.4' }),
      // 两个功能键
      h('circle', { key: 'btn-a', cx: 15.9, cy: 10.9, r: 1.05, fill: 'currentColor', stroke: 'none' }),
      h('circle', { key: 'btn-b', cx: 18.2, cy: 13.2, r: 1.05, fill: 'currentColor', stroke: 'none' }));
    }

    function Card({ title, actions, children }) {
      return h('section', { className: 'dgc-card' },
        h('div', { className: 'dgc-card-head' },
          h('h3', { className: 'dgc-card-title' }, title),
          actions ? h('div', { className: 'dgc-row' }, actions) : null),
        children);
    }

    function Field({ label, value, onChange, placeholder, numeric, wide }) {
      return h('label', { className: 'dgc-field' + (wide ? ' dgc-field-wide' : '') },
        h('span', { className: 'dgc-field-label' }, label),
        h('input', {
          type: 'text',
          inputMode: numeric ? 'decimal' : 'text',
          className: 'dgc-input',
          value,
          placeholder: placeholder || '',
          spellCheck: false,
          autoComplete: 'off',
          'aria-label': label,
          onChange: (event) => onChange(event.target.value),
        }));
    }

    function SelectField({ label, value, onChange, options, disabled, title, wide }) {
      return h('label', { className: 'dgc-field' + (wide ? ' dgc-field-wide' : '') },
        h('span', { className: 'dgc-field-label' }, label),
        h('select', {
          className: 'dgc-input',
          value,
          disabled,
          title,
          'aria-label': label,
          onChange: (event) => onChange(event.target.value),
        }, (options || []).map((item) => h('option', {
          key: String(item.value),
          value: item.value,
        }, item.label))));
    }

    function ActionButton({ children, primary, disabled, onClick, title }) {
      return h('button', {
        type: 'button',
        className: 'dgc-btn',
        'data-primary': primary ? '1' : undefined,
        disabled,
        title,
        onClick,
      }, children);
    }

    function Kv({ label, value, mono }) {
      const text = value === null || value === undefined || value === '' ? '—' : String(value);
      return h('div', { className: 'dgc-kv' },
        h('span', { className: 'dgc-kv-label' }, label),
        h('span', { className: 'dgc-kv-value' + (mono ? ' dgc-mono' : ''), title: text }, text));
    }

    function formatScreen(screen) {
      if (!screen) return '';
      const width = Number(screen.width);
      const height = Number(screen.height);
      if (!Number.isFinite(width) || !Number.isFinite(height)) return '';
      const x = Number(screen.x) || 0;
      const y = Number(screen.y) || 0;
      const base = `${width} × ${height}`;
      return x === 0 && y === 0 ? base : `${base} @ (${x}, ${y})`;
    }

    function formatBytes(bytes) {
      const value = Number(bytes);
      if (!Number.isFinite(value) || value < 0) return '—';
      if (value < 1024) return `${value} B`;
      if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
      return `${(value / (1024 * 1024)).toFixed(2)} MB`;
    }

    // 毫秒数 → 可读时长
    function formatDurationMs(value) {
      const ms = Number(value);
      if (!Number.isFinite(ms) || ms < 0) return '—';
      if (ms < 1000) return `${Math.round(ms)} ms`;
      return `${(ms / 1000).toFixed(1)} s`;
    }

    // ISO 时间 → 本地可读时间（解析失败时原样返回）
    function formatTime(value) {
      if (!value) return '';
      const time = Date.parse(String(value));
      if (!Number.isFinite(time)) return String(value);
      return new Date(time).toLocaleString();
    }

    // 新建技能时的默认配置文件文本（JSONC；保存后插件会补全参数并重生成中文注释）
    function defaultSkillTemplate() {
      const now = new Date();
      const pad = (value) => String(value).padStart(2, '0');
      const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`
        + `${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
      const suffix = Math.random().toString(36).slice(2, 6);
      return [
        '// 技能配置文件（JSONC：支持 // 行注释与 /* */ 块注释）',
        '// 保存时插件会自动补全每步参数并重新生成中文注释；手工修改的注释会被覆盖。',
        '// 自定义备注请写每步的 note 字段（≤200 字符），它会保留并拼进生成的注释。',
        '{',
        `  "id": "new-skill-${stamp}-${suffix}",`,
        '  "title": "新技能",',
        '  "description": "",',
        '  "game": "",',
        '  "tags": [],',
        '  "markdown": "## 使用说明\\n在这里写技能说明。",',
        '  // 步骤（1–200 步）：action 必填；params 为动作参数；',
        '  // waitMs 为执行该步前的等待毫秒；continueOnError 控制失败后是否继续。',
        '  "steps": [',
        '    {',
        '      "action": "wait",',
        '      "params": { "durationMs": 0 },',
        '      "waitMs": 0,',
        '      "continueOnError": false,',
        '      "note": "示例步骤：等待 0ms；可改为 find_image / mouse_click / mouse_circle 等"',
        '    }',
        '  ]',
        '}',
      ].join('\n');
    }

    // 简单模板填充：{name} → values[name]
    function fillTemplate(text, values) {
      return String(text === undefined || text === null ? '' : text).replace(/\{(\w+)\}/g, (match, name) => (
        values && values[name] !== undefined && values[name] !== null ? String(values[name]) : match
      ));
    }

    // 泛 JSON 克隆（步骤数据都是 JSON 安全值）
    function cloneJson(value) {
      try {
        return JSON.parse(JSON.stringify(value));
      } catch (error) {
        return {};
      }
    }

    // SPEC 10.3：结构化步骤表格里按 action 渲染的可编辑关键字段（字段名与动作参数逐字一致）
    const STEP_PARAM_FIELDS = {
      activate_window: [
        { key: 'window', type: 'text' },
        { key: 'hwnd', type: 'number' },
        { key: 'timeoutMs', type: 'number' },
      ],
      find_image: [
        { key: 'template', type: 'text' },
        { key: 'templates', type: 'list' },
        { key: 'threshold', type: 'number' },
        { key: 'window', type: 'text' },
      ],
      wait_image: [
        { key: 'template', type: 'text' },
        { key: 'templates', type: 'list' },
        { key: 'threshold', type: 'number' },
        { key: 'timeoutMs', type: 'number' },
        { key: 'intervalMs', type: 'number' },
        { key: 'mode', type: 'text' },
        { key: 'window', type: 'text' },
      ],
      mouse_click: [
        { key: 'x', type: 'number' },
        { key: 'y', type: 'number' },
        { key: 'clicks', type: 'number' },
        { key: 'button', type: 'text' },
        { key: 'intervalMs', type: 'number' },
      ],
      mouse_circle: [
        { key: 'centerX', type: 'number' },
        { key: 'centerY', type: 'number' },
        { key: 'radius', type: 'number' },
        { key: 'periodMs', type: 'number' },
        { key: 'durationMs', type: 'number' },
        { key: 'direction', type: 'text' },
        { key: 'abortKey', type: 'text' },
      ],
      wait: [{ key: 'durationMs', type: 'number' }],
      mouse_drag: [
        { key: 'x1', type: 'number' },
        { key: 'y1', type: 'number' },
        { key: 'x2', type: 'number' },
        { key: 'y2', type: 'number' },
        { key: 'durationMs', type: 'number' },
        { key: 'steps', type: 'number' },
        { key: 'button', type: 'text' },
      ],
      key_press: [{ key: 'key', type: 'text' }],
      key_down: [{ key: 'key', type: 'text' }],
      key_up: [{ key: 'key', type: 'text' }],
      hotkey: [{ key: 'keys', type: 'list' }],
      type_text: [{ key: 'text', type: 'text' }],
    };

    // 保存时把表格里的字符串强类型化：变量字符串原样保留，列表逗号拆分，数字键转 number
    const NUMERIC_PARAM_KEYS = new Set([
      'x', 'y', 'x1', 'y1', 'x2', 'y2', 'dx', 'dy', 'clicks', 'intervalMs', 'amount', 'presses',
      'durationMs', 'periodMs', 'centerX', 'centerY', 'radius', 'startAngleDeg', 'samplesPerSecond',
      'leadInMs', 'steps', 'threshold', 'timeoutMs', 'maxResults', 'quality', 'monitor', 'hwnd',
    ]);
    const LIST_PARAM_KEYS = new Set(['templates', 'keys', 'scales']);

    // 磁盘/接口上的 step → 表格可编辑副本（保留 saveAs / note / continueOnError）
    function editableStepFromSkill(step) {
      const source = step && typeof step === 'object' ? step : {};
      const params = source.params && typeof source.params === 'object' && !Array.isArray(source.params)
        ? cloneJson(source.params)
        : {};
      const editable = {
        action: String(source.action || 'wait'),
        params,
        waitMs: source.waitMs === undefined || source.waitMs === null ? 0 : source.waitMs,
        continueOnError: Boolean(source.continueOnError),
      };
      if (source.saveAs) editable.saveAs = String(source.saveAs);
      if (source.note) editable.note = String(source.note);
      return editable;
    }

    // 表格参数 → 结构化 steps 参数（数字/变量/列表）
    function coerceSkillParams(params) {
      const result = {};
      if (!params || typeof params !== 'object' || Array.isArray(params)) return result;
      for (const [key, rawValue] of Object.entries(params)) {
        if (typeof rawValue !== 'string') {
          result[key] = rawValue;
          continue;
        }
        const text = rawValue.trim();
        if (text === '') continue;
        if (text.startsWith('$')) {
          // 变量字符串（$window.* / $saveAs.*）保持原样
          result[key] = rawValue;
          continue;
        }
        if (LIST_PARAM_KEYS.has(key)) {
          result[key] = text.split(',').map((item) => item.trim()).filter(Boolean);
          continue;
        }
        if (NUMERIC_PARAM_KEYS.has(key)) {
          const number = Number(text);
          result[key] = Number.isFinite(number) ? number : rawValue;
          continue;
        }
        result[key] = rawValue;
      }
      return result;
    }
    function formatErrorInfo(info) {
      if (!info) return '';
      if (typeof info === 'string') return info;
      const code = info.code ? `[${info.code}] ` : '';
      const hint = info.hint ? `（${info.hint}）` : '';
      return `${code}${info.message || ''}${hint}`;
    }

    function compactPairs(result) {
      const parts = [];
      const keys = ['x', 'y', 'x1', 'y1', 'x2', 'y2', 'button', 'clicks', 'amount', 'horizontal', 'durationMs'];
      for (const key of keys) {
        if (result[key] !== undefined && result[key] !== null) parts.push(`${key}=${result[key]}`);
      }
      return parts.join(', ');
    }

    // 把 Python 信封里的 result 压成一行可读文本（用于「最近结果」和操作日志）
    function formatResult(action, result) {
      if (result === undefined || result === null) return '';
      if (typeof result !== 'object') return String(result);
      switch (action) {
        case 'mouse_move':
        case 'mouse_move_rel':
        case 'mouse_click':
        case 'mouse_down':
        case 'mouse_up':
        case 'mouse_drag':
        case 'mouse_scroll':
        case 'mouse_position':
          return compactPairs(result);
        case 'key_press':
          return `key=${result.key} presses=${result.presses === undefined ? 1 : result.presses}`;
        case 'key_down':
        case 'key_up':
          return `key=${result.key}`;
        case 'hotkey':
          return `keys=${Array.isArray(result.keys) ? result.keys.join('+') : ''}`;
        case 'type_text':
          return `length=${result.length}`;
        case 'screenshot':
          return `${result.width}×${result.height} ${result.format || 'png'} ${formatBytes(result.bytes)} → ${result.absolutePath || result.path || ''}`;
        case 'list_windows':
          return `windows=${result.count === undefined
            ? (Array.isArray(result.windows) ? result.windows.length : 0)
            : result.count}`;
        case 'capture_window':
          return `${result.width}×${result.height} ${result.format || 'png'} ${formatBytes(result.bytes)}`
            + ` engine=${result.engine || 'auto'}`
            + `${Array.isArray(result.warnings) && result.warnings.length ? ` warnings=${result.warnings.length}` : ''}`
            + ` → ${result.absolutePath || result.path || ''}`;
        case 'activate_window':
          return `foreground=${result.foreground} attempts=${result.attempts}`
            + `${result.window && result.window.title ? ` → ${result.window.title}` : ''}`;
        case 'mouse_circle':
          return `samples=${result.samples} loops=${result.loops} duration=${result.actualDurationMs}ms`
            + ` aborted=${result.aborted}${result.abortReason ? ` (${result.abortReason})` : ''}`;
        case 'find_image':
          return `found=${result.found}`
            + `${result.found && Array.isArray(result.matches) ? ` matches=${result.matches.length}` : ''}`
            + `${result.elapsedMs !== undefined ? ` ${result.elapsedMs}ms` : ''}`;
        case 'skill_list':
          return `skills=${result.count}`;
        case 'skill_get':
          return `skill=${result.skill ? result.skill.id : ''}`;
        case 'skill_save':
          return `skill=${result.id} created=${result.created}`
            + `${result.backupPath ? ` backup=${result.backupPath}` : ''}`;
        case 'skill_versions':
          return `skill=${result.id} versions=${result.count}`;
        case 'skill_restore':
          return `skill=${result.id} from=${result.restoredFrom}`;
        case 'skill_delete':
          return `skill=${result.id} deleted=${result.deleted}`
            + `${result.trashPath ? ` → ${result.trashPath}` : ''}`;
        case 'skill_run':
          return `success=${result.success} aborted=${result.aborted} duration=${result.durationMs}ms`
            + `${result.failedStep ? ` failedStep=${result.failedStep.index}:${result.failedStep.action}` : ''}`;
        case 'loop_start':
          return `job=${result.jobId || ''} rounds=${result.rounds} running=${result.running}`
            + `${result.perRoundTimeoutMs === undefined ? '' : ` perRoundTimeout=${result.perRoundTimeoutMs}ms`}`;
        case 'loop_status':
          return `running=${result.running} round=${result.currentRound}/${result.rounds}`
            + ` completed=${result.completedRounds} failed=${result.failedRounds}`;
        case 'loop_stop':
          return `stopRequested=${result.stopRequested} running=${result.running}`;
        default:
          return JSON.stringify(result).slice(0, 240);
      }
    }

    // 默认窗口：优先前台窗口；否则矩形面积最大者（与 SPEC 2.3.1 的选窗偏好一致）
    function pickDefaultWindowHwnd(list) {
      if (!Array.isArray(list) || list.length === 0) return '';
      const areaOf = (item) => {
        const rect = item && item.rect ? item.rect : {};
        const width = Number(rect.width);
        const height = Number(rect.height);
        return Number.isFinite(width) && Number.isFinite(height) ? width * height : 0;
      };
      const foreground = list.find((item) => item && item.foreground);
      const chosen = foreground || list.reduce((best, item) => (!best || areaOf(item) > areaOf(best) ? item : best), null);
      if (!chosen || chosen.hwnd === undefined || chosen.hwnd === null) return '';
      return String(chosen.hwnd);
    }

    function mimeOf(format) {
      return String(format || 'png').toLowerCase() === 'jpeg' ? 'image/jpeg' : 'image/png';
    }

    /* ------------------------------------------------------------------ *
     * 主面板
     * ------------------------------------------------------------------ */
    function Panel({ t }) {
      // ---- 状态 ----
      const [status, setStatus] = useState(null); // GET /status 的响应
      const [statusError, setStatusError] = useState('');
      const [statusBusy, setStatusBusy] = useState(false);
      const [busy, setBusy] = useState(false);
      const [result, setResult] = useState(null); // {action, ok, text}
      const [logs, setLogs] = useState([]); // {id, time, action, ok, message}
      // ---- 鼠标 / 键盘 / 截图表单 ----
      const [pos, setPos] = useState({ x: '', y: '', x2: '', y2: '' });
      const [keyInput, setKeyInput] = useState('');
      const [comboInput, setComboInput] = useState('');
      const [textInput, setTextInput] = useState('');
      const [region, setRegion] = useState({ x: '', y: '', width: '', height: '' });
      const [wantBase64, setWantBase64] = useState(true);
      const [shot, setShot] = useState(null);
      const [copyNote, setCopyNote] = useState('');
      // ---- 窗口截图（v0.2：list_windows + capture_window） ----
      const [windows, setWindows] = useState(null); // null=未加载；[]=空列表
      const [windowsBusy, setWindowsBusy] = useState(false);
      const [selectedHwnd, setSelectedHwnd] = useState('');
      const [engine, setEngine] = useState('auto');
      const [capturingWindow, setCapturingWindow] = useState(false);
      const [windowShot, setWindowShot] = useState(null);
      const [windowCopyNote, setWindowCopyNote] = useState('');
      const [windowError, setWindowError] = useState('');
      // ---- 窗口激活（v0.3：activate_window） ----
      const [activatingWindow, setActivatingWindow] = useState(false);
      const [windowActivation, setWindowActivation] = useState(null);
      // ---- 圆形轨迹（v0.3：mouse_circle） ----
      const [circle, setCircle] = useState({
        centerX: '', centerY: '', radius: '',
        periodMs: '2500', durationMs: '30000', direction: 'cw',
        samplesPerSecond: '120', button: 'none', abortKey: 'esc',
      });
      const [circleResult, setCircleResult] = useState(null);
      const [circleError, setCircleError] = useState('');
      const [circleRunning, setCircleRunning] = useState(false);
      // ---- 模板找图（v0.3：find_image） ----
      const [find, setFind] = useState({ template: '', threshold: '0.85', regionMode: 'window' });
      const [findResult, setFindResult] = useState(null);
      const [findBusy, setFindBusy] = useState(false);
      const [findError, setFindError] = useState('');
      // ---- 技能库（v0.3：skill_*） ----
      const [skills, setSkills] = useState(null); // null=未加载；[]=空列表
      const [skillsBusy, setSkillsBusy] = useState(false);
      const [skillEditor, setSkillEditor] = useState(null); // {id, content, stepsView, summary, meta, steps}
      const [skillDetailBusy, setSkillDetailBusy] = useState(false);
      const [skillVersions, setSkillVersions] = useState(null); // {id,count,versions:[...]}
      const [skillVersionPick, setSkillVersionPick] = useState('latest');
      const [skillSaveNote, setSkillSaveNote] = useState('');
      const [skillRestoreNote, setSkillRestoreNote] = useState('');
      const [skillError, setSkillError] = useState('');
      const [skillRunResult, setSkillRunResult] = useState(null); // 干跑结果
      const [skillDeleteArmed, setSkillDeleteArmed] = useState(false);
      const [skillDeleteNote, setSkillDeleteNote] = useState('');
      // ---- 循环（v0.6/v0.6.1：唯一「轮数」控制，rounds=1 也走 LoopRunner） ----
      const [loopConfig, setLoopConfig] = useState({ rounds: '1', intervalMs: '500', immediate: false, continueOnError: false });
      const [loopState, setLoopState] = useState(null); // loop_start / loop_status 返回的循环状态
      const [loopError, setLoopError] = useState('');
      const [loopBusy, setLoopBusy] = useState(false); // 启动/停止请求进行中
      const [loopCopyNote, setLoopCopyNote] = useState(''); // 失败截图路径复制反馈
      const [connError, setConnError] = useState(''); // 连接中断 / stale 提示（含重置按钮）

      // ---- refs ----
      const logSeq = useRef(0);
      const inflightRef = useRef(0);
      const busyRef = useRef(false);
      const holdRef = useRef(new Map()); // key -> {held, timer, detach}
      const pathRef = useRef(null);
      const windowPathRef = useRef(null);
      // v0.6：循环状态轮询会话与「当前轮开始时间」（用于本地换算已等待秒数）
      const loopPollSessionRef = useRef(null);
      const loopPollTimerRef = useRef(0);
      const loopRoundSinceRef = useRef(0);
      const loopLastRoundRef = useRef(0);

      /* ---------------- 通用辅助（都在 Panel 内，可访问 t） ---------------- */

      const readNumber = (raw) => {
        const text = String(raw === null || raw === undefined ? '' : raw).trim();
        if (!text) return null;
        const value = Number(text);
        return Number.isFinite(value) ? value : null;
      };

      const readInt = (raw) => {
        const value = readNumber(raw);
        return value === null ? null : Math.round(value);
      };

      const messageOf = (error) => {
        if (!error) return t('unknown');
        const text = String(error.message || error);
        const code = error.code ? `[${error.code}] ` : '';
        const hint = error.hint ? `（${error.hint}）` : '';
        return `${code}${text}${hint}`;
      };

      const pushLog = (action, ok, message) => {
        logSeq.current += 1;
        const entry = {
          id: logSeq.current,
          time: new Date().toLocaleTimeString([], { hour12: false }),
          action,
          ok,
          message,
        };
        setLogs((previous) => [entry, ...previous].slice(0, 30));
      };

      // 前端校验失败：不上后端，直接提示并记日志
      const reject = (action, message) => {
        setResult({ action, ok: false, text: message });
        pushLog(action, false, message);
      };

      const refreshStatus = async () => {
        setStatusBusy(true);
        setStatusError('');
        try {
          const value = await api('/status', { timeoutMs: 20000 });
          setStatus(value);
        } catch (error) {
          setStatusError(messageOf(error));
        } finally {
          setStatusBusy(false);
        }
      };

      // 所有 POST 都走 /api/game-control/exec，body 固定为 {action, params}
      const exec = async (action, params = {}, options = {}) => {
        const value = await api('/exec', {
          method: 'POST',
          body: JSON.stringify({ action, params }),
          timeoutMs: options.timeoutMs || 30000,
        });
        if (value.ok === false) {
          const info = value.error && typeof value.error === 'object' ? value.error : {};
          const error = new Error(info.message || t('execFailed'));
          error.code = info.code || 'BACKEND_ERROR';
          error.hint = info.hint || '';
          throw error;
        }
        return value;
      };

      // 执行一个动作：统一 busy 计数、最近结果、操作日志与错误兜底
      const run = async (action, params = {}, options = {}) => {
        inflightRef.current += 1;
        setBusy(true);
        try {
          const envelope = await exec(action, params, options);
          const text = formatResult(action, envelope.result) || t('done');
          setResult({ action, ok: true, text });
          pushLog(action, true, text);
          return envelope;
        } catch (error) {
          const text = messageOf(error);
          setResult({ action, ok: false, text });
          pushLog(action, false, text);
          if (typeof options.onError === 'function') options.onError(text);
          return null;
        } finally {
          inflightRef.current = Math.max(0, inflightRef.current - 1);
          if (inflightRef.current === 0) setBusy(false);
        }
      };

      /* ---------------- 鼠标 ---------------- */

      const pointOf = () => {
        const x = readInt(pos.x);
        const y = readInt(pos.y);
        return x === null || y === null ? null : { x, y };
      };

      const dragEndOf = () => {
        const x = readInt(pos.x2);
        const y = readInt(pos.y2);
        return x === null || y === null ? null : { x, y };
      };

      const setPosField = (name) => (value) => setPos((current) => ({ ...current, [name]: value }));

      const onMove = () => {
        const point = pointOf();
        if (!point) return reject('mouse_move', t('needPoint'));
        run('mouse_move', point);
      };

      const onClickButton = (button) => () => {
        const point = pointOf();
        if (!point) return reject('mouse_click', t('needPoint'));
        run('mouse_click', { ...point, button, clicks: 1 });
      };

      const onDoubleClick = () => {
        const point = pointOf();
        if (!point) return reject('mouse_click', t('needPoint'));
        run('mouse_click', { ...point, button: 'left', clicks: 2 });
      };

      const onMouseButton = (action) => () => {
        const point = pointOf();
        if (!point) return reject(action, t('needPoint'));
        run(action, { ...point, button: 'left' });
      };

      const onDrag = () => {
        const start = pointOf();
        if (!start) return reject('mouse_drag', t('needPoint'));
        const end = dragEndOf();
        if (!end) return reject('mouse_drag', t('needDrag'));
        run('mouse_drag', { x1: start.x, y1: start.y, x2: end.x, y2: end.y, button: 'left' });
      };

      const onScroll = (amount) => () => {
        const point = pointOf();
        // x / y 是可选项：解析不出坐标时就在当前指针位置滚动
        run('mouse_scroll', point ? { amount, ...point } : { amount });
      };

      const onReadPosition = async () => {
        const envelope = await run('mouse_position', {});
        const value = envelope && envelope.result;
        if (value && Number.isFinite(Number(value.x)) && Number.isFinite(Number(value.y))) {
          setPos((current) => ({ ...current, x: String(value.x), y: String(value.y) }));
        }
      };

      /* ---------------- 键盘 ---------------- */

      const onPressKey = () => {
        const key = keyInput.trim();
        if (!key) return reject('key_press', t('needKey'));
        run('key_press', { key });
      };

      const onSendCombo = () => {
        const keys = comboInput.split('+').map((part) => part.trim()).filter(Boolean);
        if (!keys.length) return reject('hotkey', t('needCombo'));
        if (keys.length > 4) return reject('hotkey', t('comboLimited'));
        run('hotkey', { keys });
      };

      const onTypeText = () => {
        if (!textInput) return reject('type_text', t('needText'));
        if (textInput.length > 10000) return reject('type_text', t('textTooLong'));
        run('type_text', { text: textInput });
      };

      // 快捷按键：点击 = key_press；按住超过 HOLD_DELAY_MS = key_down，松开补 key_up。
      // 注意：这些按钮故意不加 disabled={busy}。若按下后按钮被 disabled，浏览器可能不再派发
      // pointerup，导致 key_up 丢失、系统里残留按住的键。这里改为在 window 上监听释放。
      const beginQuickKey = (key) => (event) => {
        if (event && typeof event.button === 'number' && event.button !== 0) return;
        if (busyRef.current) return;
        if (holdRef.current.has(key)) return;
        const entry = { held: false, timer: null, detach: null };
        const finish = (mode) => {
          if (holdRef.current.get(key) !== entry) return;
          holdRef.current.delete(key);
          if (entry.timer) clearTimeout(entry.timer);
          if (entry.detach) entry.detach();
          if (mode === 'cancel') {
            if (entry.held) run('key_up', { key });
            return;
          }
          if (entry.held) run('key_up', { key });
          else run('key_press', { key });
        };
        const onUp = () => finish('up');
        const onCancel = () => finish('cancel');
        entry.detach = () => {
          window.removeEventListener('pointerup', onUp, true);
          window.removeEventListener('pointercancel', onCancel, true);
        };
        entry.timer = setTimeout(() => {
          if (holdRef.current.get(key) !== entry) return;
          entry.held = true;
          run('key_down', { key });
        }, HOLD_DELAY_MS);
        window.addEventListener('pointerup', onUp, true);
        window.addEventListener('pointercancel', onCancel, true);
        holdRef.current.set(key, entry);
      };

      /* ---------------- 截图 ---------------- */

      const takeScreenshot = async (withRegion) => {
        const params = { format: 'png', base64: Boolean(wantBase64) };
        if (withRegion) {
          const x = readInt(region.x);
          const y = readInt(region.y);
          const width = readInt(region.width);
          const height = readInt(region.height);
          if (x === null || y === null || width === null || height === null || width <= 0 || height <= 0) {
            return reject('screenshot', t('needRegion'));
          }
          params.region = { x, y, width, height };
        }
        setShot(null);
        setCopyNote('');
        const envelope = await run('screenshot', params, { timeoutMs: 90000 });
        if (envelope && envelope.result) setShot(envelope.result);
      };

      // 复制文本：优先 Clipboard API，失败时退回「选中只读输入框 + execCommand」
      const copyText = async (text, inputRef, setNote) => {
        if (!text) return;
        try {
          if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
            await navigator.clipboard.writeText(text);
            setNote(t('copied'));
            return;
          }
        } catch {
          // 剪贴板不可用时走下面的手动选中复制
        }
        try {
          if (inputRef.current) {
            inputRef.current.focus();
            inputRef.current.select();
            const copied = typeof document.execCommand === 'function' ? document.execCommand('copy') : false;
            setNote(copied ? t('copied') : t('copyFailed'));
            return;
          }
        } catch {
          // 忽略，统一落到失败提示
        }
        setNote(t('copyFailed'));
      };

      const copyShotPath = () => copyText(
        shot ? String(shot.absolutePath || shot.path || '') : '',
        pathRef,
        setCopyNote,
      );

      const copyWindowPath = () => copyText(
        windowShot ? String(windowShot.absolutePath || windowShot.path || '') : '',
        windowPathRef,
        setWindowCopyNote,
      );

      /* ---------------- 窗口截图（v0.2：list_windows / capture_window） ---------------- */

      // 刷新窗口列表：POST /exec {action:'list_windows', params:{includeInvisible:false}}
      const refreshWindows = async () => {
        setWindowsBusy(true);
        setWindowError('');
        try {
          const envelope = await run(
            'list_windows',
            { includeInvisible: false },
            { timeoutMs: 25000, onError: setWindowError },
          );
          if (!envelope) return;
          const list = envelope.result && Array.isArray(envelope.result.windows)
            ? envelope.result.windows
            : [];
          setWindows(list);
          setSelectedHwnd((current) => {
            const stillExists = current !== '' && list.some((item) => String(item.hwnd) === current);
            return stillExists ? current : pickDefaultWindowHwnd(list);
          });
        } catch (error) {
          // run 内部已兜底，这里只防御非预期异常，确保不会把卡片打崩
          setWindowError(messageOf(error));
        } finally {
          setWindowsBusy(false);
        }
      };

      // 抓取选中窗口：base64 用于预览；engine 默认 auto，由后端按回退链选择实际路径
      const captureWindow = async () => {
        const hwnd = readInt(selectedHwnd);
        if (hwnd === null) return reject('capture_window', t('needWindow'));
        setWindowError('');
        setWindowCopyNote('');
        setCapturingWindow(true);
        try {
          const envelope = await run(
            'capture_window',
            { hwnd, engine: engine || 'auto', base64: true, timeoutMs: 5000 },
            { timeoutMs: 45000, onError: setWindowError },
          );
          if (envelope && envelope.result) setWindowShot(envelope.result);
        } catch (error) {
          setWindowError(messageOf(error));
        } finally {
          setCapturingWindow(false);
        }
      };

      // 激活/前置选中窗口：POST /exec {action:'activate_window', params:{hwnd}}
      const activateSelectedWindow = async () => {
        const hwnd = readInt(selectedHwnd);
        if (hwnd === null) return reject('activate_window', t('needSelectedWindow'));
        setWindowError('');
        setWindowActivation(null);
        setActivatingWindow(true);
        try {
          const envelope = await run(
            'activate_window',
            { hwnd },
            { timeoutMs: 15000, onError: setWindowError },
          );
          if (!envelope || !envelope.result) return;
          const result = envelope.result;
          setWindowActivation(result);
          if (result.foreground) {
            // 本地同步前台标记，避免再拉一次窗口列表
            setWindows((current) => (current
              ? current.map((item) => ({ ...item, foreground: String(item.hwnd) === String(hwnd) }))
              : current));
          }
        } catch (error) {
          setWindowError(messageOf(error));
        } finally {
          setActivatingWindow(false);
        }
      };

      /* ---------------- 圆形轨迹（v0.3：mouse_circle） ---------------- */

      const setCircleField = (name) => (value) => setCircle((current) => ({ ...current, [name]: value }));

      // 用当前选中窗口的 clientRect 填圆心/半径（半径 = round(width/4)，即直径 = 窗口宽度的一半）
      const fillCircleFromWindow = () => {
        if (!selectedWindow) return reject('mouse_circle', t('circleNeedWindow'));
        const rect = selectedWindow.clientRect || selectedWindow.rect;
        if (!rect) return reject('mouse_circle', t('circleNeedWindow'));
        const x = Number(rect.x);
        const y = Number(rect.y);
        const width = Number(rect.width);
        const height = Number(rect.height);
        if (![x, y, width, height].every(Number.isFinite) || width <= 0 || height <= 0) {
          return reject('mouse_circle', t('circleNeedWindow'));
        }
        const radius = Math.max(1, Math.min(5000, Math.round(width / 4)));
        setCircle((current) => ({
          ...current,
          centerX: String(Math.round(x + width / 2)),
          centerY: String(Math.round(y + height / 2)),
          radius: String(radius),
        }));
        setCircleError('');
      };

      const runCircle = async (forceDryRun) => {
        const centerX = readInt(circle.centerX);
        const centerY = readInt(circle.centerY);
        const radius = readInt(circle.radius);
        if (centerX === null || centerY === null || radius === null || radius < 1) {
          return reject('mouse_circle', t('circleNeedParams'));
        }
        const numberOr = (raw, fallback) => {
          const value = readInt(raw);
          return value === null ? fallback : value;
        };
        const durationMs = numberOr(circle.durationMs, 30000);
        const params = {
          centerX,
          centerY,
          radius,
          periodMs: numberOr(circle.periodMs, 2500),
          durationMs,
          direction: circle.direction === 'ccw' ? 'ccw' : 'cw',
          samplesPerSecond: numberOr(circle.samplesPerSecond, 120),
          button: ['left', 'right', 'middle'].includes(circle.button) ? circle.button : 'none',
          abortKey: String(circle.abortKey || '').trim() === '' ? 'none' : String(circle.abortKey).trim(),
        };
        if (forceDryRun) params.dryRun = true;
        // 选中窗口时传 hwnd：画圆期间前台变化会自动停止（SPEC 8.2.2 安全项）
        const guardHwnd = readInt(selectedHwnd);
        if (guardHwnd !== null) params.hwnd = guardHwnd;
        setCircleError('');
        setCircleResult(null);
        setCircleRunning(true);
        try {
          const envelope = await run(
            'mouse_circle',
            params,
            {
              // Host 预算 min(180000, 15000 + leadInMs + durationMs + 5000)，这里留缓冲
              timeoutMs: Math.min(200000, durationMs + 60000),
              onError: setCircleError,
            },
          );
          if (envelope && envelope.result) setCircleResult(envelope.result);
        } catch (error) {
          setCircleError(messageOf(error));
        } finally {
          setCircleRunning(false);
        }
      };

      /* ---------------- 模板找图（v0.3：find_image） ---------------- */

      const setFindField = (name) => (value) => setFind((current) => ({ ...current, [name]: value }));

      const runFindImage = async () => {
        const template = String(find.template || '').trim();
        if (!template) return reject('find_image', t('findNeedTemplate'));
        const threshold = Number(String(find.threshold || '').trim());
        if (!Number.isFinite(threshold) || threshold <= 0 || threshold > 1) {
          return reject('find_image', t('findInvalidThreshold'));
        }
        const params = { template, threshold, maxResults: 5 };
        if (find.regionMode === 'window') {
          const hwnd = readInt(selectedHwnd);
          if (hwnd === null) return reject('find_image', t('findNeedWindow'));
          params.hwnd = hwnd;
        }
        setFindError('');
        setFindResult(null);
        setFindBusy(true);
        try {
          const envelope = await run('find_image', params, { timeoutMs: 40000, onError: setFindError });
          if (envelope && envelope.result) setFindResult(envelope.result);
        } catch (error) {
          setFindError(messageOf(error));
        } finally {
          setFindBusy(false);
        }
      };

      const clickMatch = (match) => {
        if (!match || !match.center) return;
        const x = readInt(match.center.x);
        const y = readInt(match.center.y);
        if (x === null || y === null) return reject('mouse_click', t('needPoint'));
        run('mouse_click', { x, y, button: 'left', clicks: 1 });
      };

      /* ---------------- 技能库（v0.3/v0.4：skill_*，整份 JSONC 配置编辑） ---------------- */

      // 编辑器只保存「完整配置文件文本 + 只读步骤列表 + 摘要」，标题/说明/步骤都在 JSONC 里编辑
      const setSkillContent = (value) => setSkillEditor((current) => (
        current ? { ...current, content: value } : current
      ));

      const loadSkillVersions = async (id) => {
        const targetId = id || (skillEditor && skillEditor.id) || '';
        if (!targetId) return;
        try {
          const envelope = await run('skill_versions', { id: targetId }, { timeoutMs: 25000 });
          if (!envelope || !envelope.result) return;
          setSkillVersions(envelope.result);
          setSkillVersionPick(envelope.result.count > 0 ? 'latest' : '');
        } catch (error) {
          // run 已记录错误；版本列表失败不阻塞编辑
        }
      };

      const loadSkill = async (id) => {
        if (!id) return;
        setSkillError('');
        setSkillDetailBusy(true);
        setSkillSaveNote('');
        setSkillDeleteNote('');
        setSkillDeleteArmed(false);
        try {
          const envelope = await run('skill_get', { id }, { timeoutMs: 25000, onError: setSkillError });
          if (!envelope || !envelope.result || !envelope.result.skill) return;
          const result = envelope.result;
          const skill = result.skill;
          const steps = Array.isArray(skill.steps) ? skill.steps : [];
          const meta = {
            title: String(skill.title || ''),
            description: String(skill.description || ''),
            game: String(skill.game || ''),
            tags: Array.isArray(skill.tags) ? skill.tags.slice() : [],
            markdown: String(skill.markdown || ''),
          };
          // v0.4：content 是磁盘上带中文注释的 JSONC 原文；stepsView 给只读步骤列表
          // v0.5：meta + steps 供「结构化步骤表格」编辑，保存时用结构化字段回写
          setSkillEditor({
            id: String(skill.id || id),
            content: String(result.content === undefined || result.content === null ? '' : result.content),
            stepsView: Array.isArray(result.stepsView) ? result.stepsView : [],
            summary: {
              title: String(skill.title || ''),
              game: String(skill.game || ''),
              stepCount: steps.length,
              updatedAt: String(skill.updatedAt || ''),
            },
            meta,
            steps: steps.map(editableStepFromSkill),
          });
          setSkillVersions(null);
          setSkillVersionPick('latest');
          await loadSkillVersions(String(skill.id || id));
        } catch (error) {
          setSkillError(messageOf(error));
        } finally {
          setSkillDetailBusy(false);
        }
      };

      const refreshSkills = async () => {
        setSkillsBusy(true);
        setSkillError('');
        try {
          const envelope = await run('skill_list', {}, { timeoutMs: 25000, onError: setSkillError });
          if (!envelope) return;
          const list = envelope.result && Array.isArray(envelope.result.skills) ? envelope.result.skills : [];
          setSkills(list);
          // 当前编辑的技能若还在列表里就保留，否则清空编辑器避免误导
          setSkillEditor((current) => {
            if (!current || !current.id) return current;
            return list.some((skill) => String(skill.id) === current.id) ? current : null;
          });
        } catch (error) {
          setSkillError(messageOf(error));
        } finally {
          setSkillsBusy(false);
        }
      };

      const newSkill = () => {
        // 新建：生成一份带中文注释的 JSONC 模板 + 一个默认 wait 步骤供表格编辑
        setSkillEditor({
          id: '',
          content: defaultSkillTemplate(),
          stepsView: [],
          summary: { title: '新技能', game: '', stepCount: 1, updatedAt: '' },
          meta: { title: '新技能', description: '', game: '', tags: [], markdown: '' },
          steps: [editableStepFromSkill({ action: 'wait', params: { durationMs: 1000 }, waitMs: 0, continueOnError: false })],
        });
        setSkillVersions(null);
        setSkillVersionPick('');
        setSkillError('');
        setSkillSaveNote(t('skillNewTemplateNote'));
        setSkillRestoreNote('');
        setSkillDeleteNote('');
        setSkillDeleteArmed(false);
        setSkillRunResult(null);
      };

      /* ---- 结构化步骤表格（v0.5：SPEC 10.3） ---- */

      const updateSkillStep = (index, patch) => setSkillEditor((current) => (current ? {
        ...current,
        steps: (Array.isArray(current.steps) ? current.steps : []).map((step, i) => (
          i === index ? { ...step, ...patch } : step
        )),
      } : current));

      const updateSkillStepParam = (index, key, rawValue) => setSkillEditor((current) => {
        if (!current) return current;
        return {
          ...current,
          steps: (Array.isArray(current.steps) ? current.steps : []).map((step, i) => {
            if (i !== index) return step;
            const params = { ...(step.params && typeof step.params === 'object' ? step.params : {}) };
            if (rawValue === '') delete params[key];
            else params[key] = rawValue;
            // template 与 templates 二选一：编辑一个就清掉另一个，避免后端报参数冲突
            if (key === 'templates' && rawValue !== '') delete params.template;
            if (key === 'template' && rawValue !== '') delete params.templates;
            return { ...step, params, paramsText: undefined };
          }),
        };
      });

      const updateSkillStepAction = (index, action) => setSkillEditor((current) => (current ? {
        ...current,
        steps: (Array.isArray(current.steps) ? current.steps : []).map((step, i) => (
          // 切换 action 时重置 params，避免残留不属于新动作的参数
          i === index ? { ...step, action, params: {}, paramsText: undefined } : step
        )),
      } : current));

      const moveSkillStep = (index, delta) => setSkillEditor((current) => {
        if (!current) return current;
        const steps = (Array.isArray(current.steps) ? current.steps : []).slice();
        const target = index + delta;
        if (target < 0 || target >= steps.length) return current;
        const [item] = steps.splice(index, 1);
        steps.splice(target, 0, item);
        return { ...current, steps };
      });

      const removeSkillStep = (index) => setSkillEditor((current) => (current ? {
        ...current,
        steps: (Array.isArray(current.steps) ? current.steps : []).filter((step, i) => i !== index),
      } : current));

      const addSkillStep = () => setSkillEditor((current) => (current ? {
        ...current,
        steps: [
          ...(Array.isArray(current.steps) ? current.steps : []),
          editableStepFromSkill({ action: 'wait', params: { durationMs: 1000 }, waitMs: 0, continueOnError: false }),
        ],
      } : current));

      // 表格 → 结构化 steps（前端只做基础校验；字段校验/归一化交给后端）
      const buildStructuredSteps = () => {
        const rows = skillEditor && Array.isArray(skillEditor.steps) ? skillEditor.steps : [];
        if (rows.length === 0) {
          reject('skill_save', t('skillNeedSteps'));
          return null;
        }
        const steps = [];
        for (let index = 0; index < rows.length; index += 1) {
          const row = rows[index] || {};
          const action = String(row.action || '').trim();
          if (!action) {
            reject('skill_save', `${t('skillStepActionRequired')} (#${index + 1})`);
            return null;
          }
          let params;
          if (row.paramsText !== undefined) {
            try {
              params = JSON.parse(row.paramsText);
            } catch (error) {
              reject('skill_save', `${t('skillStepJsonInvalid')} (#${index + 1}): ${String(error && error.message ? error.message : error)}`);
              return null;
            }
            if (!params || typeof params !== 'object' || Array.isArray(params)) {
              reject('skill_save', `${t('skillStepJsonInvalid')} (#${index + 1})`);
              return null;
            }
          } else {
            params = coerceSkillParams(row.params || {});
          }
          const waitRaw = Number(row.waitMs);
          const step = {
            action,
            params,
            waitMs: Number.isFinite(waitRaw) ? Math.max(0, Math.min(60000, Math.round(waitRaw))) : 0,
            continueOnError: Boolean(row.continueOnError),
          };
          if (row.saveAs) step.saveAs = String(row.saveAs);
          if (row.note) step.note = String(row.note).slice(0, 200);
          steps.push(step);
        }
        return steps;
      };

      // 结构化保存：title/description/game/tags/markdown 取自 skill_get.skill，steps 取自表格
      const saveSkill = async () => {
        if (!skillEditor) return reject('skill_save', t('skillNeedId'));
        const meta = skillEditor.meta || {};
        const title = String(meta.title || '').trim();
        if (!title) return reject('skill_save', t('skillNeedTitle'));
        const steps = buildStructuredSteps();
        if (steps === null) return;
        const params = { title, steps };
        if (String(meta.description || '').trim()) params.description = String(meta.description).trim();
        if (String(meta.game || '').trim()) params.game = String(meta.game).trim();
        if (Array.isArray(meta.tags) && meta.tags.length) params.tags = meta.tags;
        if (String(meta.markdown || '').trim()) params.markdown = String(meta.markdown);
        if (skillEditor.id) params.id = skillEditor.id;
        setSkillError('');
        setSkillSaveNote('');
        setSkillRestoreNote('');
        try {
          const envelope = await run('skill_save', params, { timeoutMs: 30000, onError: setSkillError });
          if (!envelope || !envelope.result) return;
          const result = envelope.result;
          const savedId = String(result.id || skillEditor.id || '');
          const notes = [
            result.created ? t('skillCreated') : t('skillSaved'),
            result.backupPath ? `${t('skillBackupPath')}: ${result.backupPath}` : '',
            result.backupCount === undefined ? '' : `${t('skillBackupCount')}: ${result.backupCount}`,
          ].filter(Boolean);
          setSkillSaveNote(notes.join(' · '));
          await refreshSkills();
          // 重新加载：用后端归一化后的 steps/content/stepsView 刷新表格与 JSONC 高级编辑器
          if (savedId) await loadSkill(savedId);
        } catch (error) {
          setSkillError(messageOf(error));
        }
      };

      // 高级（content 模式）保存：整份 JSONC 文本发给后端，成功后重载
      const saveSkillContent = async () => {
        if (!skillEditor) return reject('skill_save', t('skillNeedId'));
        const content = String(skillEditor.content || '');
        // 前端只做基础校验；JSONC 合法性由后端解析（失败返回 INVALID_SKILL 中文红字）
        if (!content.trim()) return reject('skill_save', t('skillContentEmpty'));
        if (content.length > 1000000) return reject('skill_save', t('skillContentTooLong'));
        const params = { content };
        if (skillEditor.id) params.id = skillEditor.id;
        setSkillError('');
        setSkillSaveNote('');
        setSkillRestoreNote('');
        try {
          const envelope = await run('skill_save', params, { timeoutMs: 30000, onError: setSkillError });
          if (!envelope || !envelope.result) return;
          const result = envelope.result;
          const savedId = String(result.id || skillEditor.id || '');
          const notes = [
            result.created ? t('skillCreated') : t('skillSaved'),
            result.backupPath ? `${t('skillBackupPath')}: ${result.backupPath}` : '',
            result.backupCount === undefined ? '' : `${t('skillBackupCount')}: ${result.backupCount}`,
          ].filter(Boolean);
          setSkillSaveNote(notes.join(' · '));
          await refreshSkills();
          if (savedId) await loadSkill(savedId);
        } catch (error) {
          setSkillError(messageOf(error));
        }
      };

      /* ---- 循环（v0.6：loop_start / loop_status / loop_stop，Host 直接处理，不进 worker） ---- */

      const stopLoopPolling = () => {
        loopPollSessionRef.current = null;
        if (loopPollTimerRef.current) {
          clearTimeout(loopPollTimerRef.current);
          loopPollTimerRef.current = 0;
        }
      };

      const pollLoopStatus = async (session) => {
        if (loopPollSessionRef.current !== session) return;
        let keepPolling = true;
        try {
          const envelope = await exec('loop_status', {});
          if (loopPollSessionRef.current !== session) return;
          const status = envelope && envelope.result ? envelope.result : envelope;
          if (status && typeof status === 'object') {
            setLoopState(status);
            // 当前轮变化时重置本地「当前轮已等待」计时
            if (status.currentRound !== undefined && status.currentRound !== loopLastRoundRef.current) {
              loopLastRoundRef.current = status.currentRound || 0;
              loopRoundSinceRef.current = Date.now();
            }
            if (status.running === false) keepPolling = false;
          }
        } catch (error) {
          if (loopPollSessionRef.current !== session) return;
          stopLoopPolling();
          setLoopError(messageOf(error));
          setConnError(t('skillConnectionLost'));
          return;
        }
        if (loopPollSessionRef.current !== session) return;
        if (!keepPolling) {
          stopLoopPolling();
          setLoopError('');
          return;
        }
        loopPollTimerRef.current = setTimeout(() => { pollLoopStatus(session); }, 700);
      };

      const startLoopPolling = () => {
        stopLoopPolling();
        const session = {};
        loopPollSessionRef.current = session;
        loopPollTimerRef.current = 0;
        pollLoopStatus(session);
      };

      // 开始运行：loop_start 只负责启动；之后由 loop_status 轮询状态
      const startLoop = async () => {
        const id = skillEditor && skillEditor.id;
        if (!id) return reject('loop_start', t('skillNeedId'));
        const rounds = readInt(loopConfig.rounds);
        if (rounds === null || rounds < 1 || rounds > 10000) {
          return reject('loop_start', t('skillLoopNeedRounds'));
        }
        const intervalMs = readInt(loopConfig.intervalMs);
        if (intervalMs === null || intervalMs < 0 || intervalMs > 60000) {
          return reject('loop_start', t('skillLoopNeedInterval'));
        }
        setLoopError('');
        setConnError('');
        setLoopCopyNote('');
        setLoopState(null);
        setLoopBusy(true);
        try {
          const envelope = await run('loop_start', {
            skillId: id,
            rounds,
            intervalMs,
            // 「失败后继续」勾选 → stopOnError=false（偶发失败轮不中断长循环）
            stopOnError: !loopConfig.continueOnError,
            abortKey: 'esc',
          }, { timeoutMs: 30000, onError: setLoopError });
          if (!envelope || !envelope.result) return;
          const result = envelope.result;
          loopLastRoundRef.current = 0;
          loopRoundSinceRef.current = 0;
          setLoopState({ ...result, running: result.running !== false });
          startLoopPolling();
        } catch (error) {
          setLoopError(messageOf(error));
        } finally {
          setLoopBusy(false);
        }
      };

      // 停止循环：immediate 勾选时终止当前轮（后端记为失败）
      const stopLoop = async () => {
        if (!loopState || loopState.running !== true) return;
        setLoopBusy(true);
        setLoopError('');
        try {
          const envelope = await run(
            'loop_stop',
            { immediate: Boolean(loopConfig.immediate) },
            { timeoutMs: 30000, onError: setLoopError },
          );
          if (envelope && envelope.result) {
            setLoopState((current) => (current ? { ...current, ...envelope.result } : current));
          }
          // running 变 false 由轮询确认；immediate 时状态可能已直接结束
        } catch (error) {
          setLoopError(messageOf(error));
        } finally {
          setLoopBusy(false);
        }
      };

      // 连接中断后重置本地运行状态（不停止后台循环，仅恢复面板按钮）
      const resetLocalRunState = () => {
        stopLoopPolling();
        setSkillRunResult(null);
        setConnError('');
        setSkillError('');
        setLoopError('');
        setLoopCopyNote('');
        setLoopBusy(false);
        setLoopState(null);
        loopLastRoundRef.current = 0;
        loopRoundSinceRef.current = 0;
      };

      // 复制失败截图路径（loop_status.results[].failedStep.screenshotPath）
      const copyFailedScreenshot = async (path) => {
        const text = String(path || '');
        if (!text) return;
        try {
          if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
            await navigator.clipboard.writeText(text);
            setLoopCopyNote(t('copied'));
            return;
          }
        } catch (error) {
          // 落到失败提示
        }
        setLoopCopyNote(t('copyFailed'));
      };

      const restoreSkillVersion = async () => {
        const id = skillEditor && skillEditor.id;
        if (!id) return reject('skill_restore', t('skillNeedId'));
        setSkillError('');
        setSkillRestoreNote('');
        try {
          const envelope = await run(
            'skill_restore',
            { id, backup: skillVersionPick || 'latest' },
            { timeoutMs: 25000, onError: setSkillError },
          );
          if (!envelope || !envelope.result) return;
          const result = envelope.result;
          setSkillRestoreNote(result.backupOfCurrent
            ? `${t('skillRestored')} · ${t('skillBackupPath')}: ${result.backupOfCurrent}`
            : t('skillRestored'));
          setSkillVersionPick('latest');
          await loadSkill(id);
          await refreshSkills();
        } catch (error) {
          setSkillError(messageOf(error));
        }
      };

      // 干跑：单次、不计数；只做静态校验与变量规划（不经过 LoopRunner）
      const runDryRun = async () => {
        const id = skillEditor && skillEditor.id;
        if (!id) return reject('skill_run', t('skillNeedId'));
        setSkillError('');
        setSkillRunResult(null);
        try {
          const envelope = await run(
            'skill_run',
            { id, dryRun: true },
            { timeoutMs: 60000, onError: setSkillError },
          );
          if (envelope && envelope.result) setSkillRunResult(envelope.result);
        } catch (error) {
          setSkillError(messageOf(error));
        }
      };

      const deleteSkill = async () => {
        const id = skillEditor && skillEditor.id;
        if (!id) return reject('skill_delete', t('skillNeedId'));
        if (!skillDeleteArmed) {
          setSkillDeleteArmed(true);
          return;
        }
        setSkillDeleteArmed(false);
        setSkillDeleteNote('');
        setSkillError('');
        try {
          const envelope = await run('skill_delete', { id }, { timeoutMs: 25000, onError: setSkillError });
          if (!envelope || !envelope.result) return;
          const result = envelope.result;
          setSkillDeleteNote(`${t('skillDeleted')} · ${t('skillTrashPath')}: ${result.trashPath || '—'}`);
          setSkillEditor(null);
          setSkillVersions(null);
          setSkillVersionPick('');
          setSkillSaveNote('');
          setSkillRestoreNote('');
          setSkillRunResult(null);
          await refreshSkills();
        } catch (error) {
          setSkillError(messageOf(error));
        }
      };

      /* ---------------- effects ---------------- */

      // busy 的同步镜像，供 pointerdown 这类同步事件里判断
      useEffect(() => {
        busyRef.current = busy;
      }, [busy]);

      // 只在挂载时 GET 一次状态；不做轮询（用户可点「刷新」）
      useEffect(() => {
        refreshStatus();
        // eslint-disable-next-line react-hooks/exhaustive-deps
      }, []);

      // 卸载清理：清掉循环轮询、长按定时器与 window 监听；若还有按住的键，尽力补发 key_up 避免卡键
      useEffect(() => () => {
        stopLoopPolling();
        for (const [key, entry] of holdRef.current) {
          if (entry.timer) clearTimeout(entry.timer);
          if (entry.detach) entry.detach();
          if (entry.held) {
            fetch(API_BASE + '/exec', {
              method: 'POST',
              credentials: 'same-origin',
              headers: { 'content-type': 'application/json' },
              body: JSON.stringify({ action: 'key_up', params: { key } }),
            }).catch(() => {});
          }
        }
        holdRef.current.clear();
      }, []);

      /* ---------------- 派生状态 ---------------- */

      const bridge = status && status.bridge ? status.bridge : null;
      const backendEnvelope = status && status.backend ? status.backend : null;
      const backendInfo = backendEnvelope && backendEnvelope.ok === true && backendEnvelope.result
        ? backendEnvelope.result
        : null;
      const ready = Boolean(bridge && bridge.ready);
      // 错误显示优先级：GET 失败 > 后端信封里的详细错误 > 桥接层短错误码（如 PYTHON_NOT_FOUND）
      const backendErrorText = backendEnvelope && backendEnvelope.ok === false
        ? formatErrorInfo(backendEnvelope.error)
        : '';
      const bridgeErrorText = bridge && bridge.lastError ? String(bridge.lastError) : '';
      const lastError = statusError || backendErrorText || bridgeErrorText;
      // 后端不可用时附带 stderr 尾部，方便排查 Python 启动/依赖问题
      const stderrTail = !ready && bridge && bridge.stderrTail ? String(bridge.stderrTail).slice(-600) : '';

      const quickKeys = [
        { key: 'w', label: 'W' },
        { key: 'a', label: 'A' },
        { key: 's', label: 'S' },
        { key: 'd', label: 'D' },
        { key: 'space', label: t('keySpace') },
        { key: 'shift', label: 'Shift' },
        { key: 'esc', label: 'Esc' },
        { key: 'enter', label: 'Enter' },
        { key: 'f', label: 'F' },
        { key: 'q', label: 'Q' },
        { key: 'e', label: 'E' },
        { key: 'r', label: 'R' },
        { key: 'tab', label: 'Tab' },
        { key: '1', label: '1' },
        { key: '2', label: '2' },
        { key: '3', label: '3' },
        { key: '4', label: '4' },
        { key: '5', label: '5' },
      ];

      const selectedWindow = windows && selectedHwnd !== ''
        ? windows.find((item) => String(item.hwnd) === selectedHwnd) || null
        : null;

      const engineOptions = [
        { value: 'auto', label: t('engineAuto') },
        { value: 'wgc', label: t('engineWgc') },
        { value: 'printwindow', label: t('enginePrintwindow') },
        { value: 'screen', label: t('engineScreen') },
      ];

      // 非 Windows 平台：后端会返回 UNSUPPORTED_PLATFORM，先在卡片里给出提示
      const windowPlatformUnsupported = Boolean(
        backendInfo && backendInfo.platform && !/^win/i.test(String(backendInfo.platform)),
      );

      // 下拉框显示文本：title — 进程名 (hwnd=0x…, W×H) · 前台/最小化
      const windowLabelOf = (item) => {
        if (!item) return '';
        const rawTitle = String(item.title || '').trim() || '—';
        const title = rawTitle.length > 80 ? `${rawTitle.slice(0, 79)}…` : rawTitle;
        const process = String(item.process || '').trim() || t('unknown');
        const rect = item.rect || {};
        const width = Number(rect.width);
        const height = Number(rect.height);
        const size = Number.isFinite(width) && Number.isFinite(height) ? `${width}×${height}` : '';
        const hwnd = item.hwnd === undefined || item.hwnd === null ? '' : `hwnd=0x${Number(item.hwnd).toString(16)}`;
        const meta = [hwnd, size].filter(Boolean).join(', ');
        const tags = [
          item.foreground ? t('foregroundTag') : '',
          item.minimized ? t('minimizedTag') : '',
        ].filter(Boolean);
        return `${title} — ${process} (${meta})${tags.length ? ` · ${tags.join(' · ')}` : ''}`;
      };

      // ---- v0.3 下拉选项 / 技能列表派生 ----
      const circleDirectionOptions = [
        { value: 'cw', label: t('circleCw') },
        { value: 'ccw', label: t('circleCcw') },
      ];
      const circleButtonOptions = [
        { value: 'none', label: t('circleBtnNone') },
        { value: 'left', label: t('circleBtnLeft') },
        { value: 'right', label: t('circleBtnRight') },
        { value: 'middle', label: t('circleBtnMiddle') },
      ];
      const findRegionOptions = [
        { value: 'window', label: t('findRegionWindow') },
        { value: 'screen', label: t('findRegionScreen') },
      ];
      const skillOptions = [
        { value: '', label: t('skillPickPlaceholder') },
        ...((skills || []).map((skill) => ({
          value: String(skill.id),
          label: `${String(skill.title || skill.id)} — ${String(skill.game || '—')}`
            + ` (${t('skillStepCount')} ${skill.stepCount === undefined ? 0 : skill.stepCount}`
            + ` · ${t('skillBackupCount')} ${skill.backupCount === undefined ? 0 : skill.backupCount}`
            + ` · ${t('skillUpdatedAt')} ${formatTime(skill.updatedAt) || '—'})`,
        }))),
      ];
      const selectedSkillRow = skills && skillEditor && skillEditor.id
        ? skills.find((skill) => String(skill.id) === skillEditor.id) || null
        : null;
      // v0.4：编辑器摘要（优先用列表行，其次用 skill_get 的 skill 摘要）与只读步骤列表
      const skillSummary = selectedSkillRow || (skillEditor && skillEditor.summary) || null;
      const skillStepsView = skillEditor && Array.isArray(skillEditor.stepsView) ? skillEditor.stepsView : [];
      // v0.5：结构化步骤（表格编辑数据）
      const skillSteps = skillEditor && Array.isArray(skillEditor.steps) ? skillEditor.steps : [];
      // v0.6：循环状态派生（运行中禁用编辑类操作；当前轮已等待秒数本地换算）
      const loopActive = Boolean(loopState && loopState.running === true);
      const loopCurrentStep = loopActive && loopState && loopState.currentStep && typeof loopState.currentStep === 'object'
        ? loopState.currentStep
        : null;
      const loopStopRequested = Boolean(loopState && loopState.stopRequested);
      const loopResults = Array.isArray(loopState && loopState.results) ? loopState.results.slice(-20) : [];
      const loopCurrentElapsedSec = loopActive && loopRoundSinceRef.current
        ? Math.max(0, Math.round((Date.now() - loopRoundSinceRef.current) / 1000))
        : 0;
      const loopCounts = {
        current: loopState && loopState.currentRound !== undefined ? loopState.currentRound : 0,
        rounds: loopState && loopState.rounds !== undefined
          ? loopState.rounds
          : (readInt(loopConfig.rounds) === null ? 0 : readInt(loopConfig.rounds)),
        completed: loopState && loopState.completedRounds !== undefined ? loopState.completedRounds : 0,
        failed: loopState && loopState.failedRounds !== undefined ? loopState.failedRounds : 0,
      };
      const versionOptions = [
        { value: 'latest', label: t('skillVersionLatest') },
        ...((skillVersions && Array.isArray(skillVersions.versions) ? skillVersions.versions : []).map((version) => ({
          value: String(version.name),
          label: `${formatTime(version.savedAt) || String(version.name)} (${formatBytes(version.bytes)})`,
        }))),
      ];

      /* ---------------- 渲染 ---------------- */

      const statusBadge = status
        ? (ready
          ? h('span', { className: 'dgc-badge', 'data-tone': 'ok' }, t('ready'))
          : h('span', { className: 'dgc-badge', 'data-tone': 'warn' }, t('notReady')))
        : (statusError
          ? h('span', { className: 'dgc-badge', 'data-tone': 'error' }, t('error'))
          : h('span', { className: 'dgc-badge' }, t('loading')));

      const shotBlock = shot
        ? h('div', { className: 'dgc-shot' },
          h('div', { className: 'dgc-row' },
            h('span', { className: 'dgc-field-label' }, t('shotPath')),
            h('input', {
              ref: pathRef,
              className: 'dgc-input dgc-mono dgc-grow',
              readOnly: true,
              value: shot.absolutePath || shot.path || '',
              'aria-label': t('shotPath'),
              onFocus: (event) => event.target.select(),
            }),
            h(ActionButton, { onClick: copyShotPath }, t('copy')),
            copyNote ? h('span', { className: 'dgc-note' }, copyNote) : null),
          h('p', { className: 'dgc-note' },
            `${t('shotMeta')}: ${shot.width} × ${shot.height} · ${formatBytes(shot.bytes)} · ${shot.format || 'png'}`),
          shot.base64
            ? h('img', {
              className: 'dgc-shot-img',
              alt: t('shotPreview'),
              src: `data:${mimeOf(shot.format)};base64,${shot.base64}`,
            })
            : h('p', { className: 'dgc-note' }, t('shotNoPreview')))
        : h('p', { className: 'dgc-note' }, t('noShot'));

      // 窗口激活结果（foreground / attempts）
      const activationBlock = windowActivation
        ? h('p', { className: 'dgc-note' },
          `${t('activateSuccess')} · ${t('activationAttempts')}: `
          + `${windowActivation.attempts === undefined ? '—' : windowActivation.attempts}`
          + `${windowActivation.window && windowActivation.window.title ? ` · ${windowActivation.window.title}` : ''}`)
        : null;

      // 圆形轨迹结果
      const circleResultBlock = circleResult
        ? h('div', { className: 'dgc-shot' },
          h('div', { className: 'dgc-meta' },
            circleResult.aborted
              ? h('span', { className: 'dgc-badge', 'data-tone': 'warn' },
                `${t('circleAborted')}${circleResult.abortReason ? ` (${circleResult.abortReason})` : ''}`)
              : h('span', { className: 'dgc-badge', 'data-tone': 'ok' }, t('done')),
            h('span', null, `${t('circleSamplesResult')}: ${circleResult.samples}`),
            h('span', null, `${t('circleLoops')}: ${circleResult.loops}`),
            h('span', null, `${t('circleActualDuration')}: ${circleResult.actualDurationMs} ms`)),
          circleResult.aborted
            ? h('p', { className: 'dgc-warn' }, `${t('circleAbortReason')}: ${circleResult.abortReason || '—'}`)
            : null)
        : null;

      // 模板找图结果（找不到不是错误，显示 found:false）
      const findResultBlock = findResult
        ? h('div', { className: 'dgc-shot' },
          findResult.found
            ? h('div', { className: 'dgc-meta' },
              h('span', { className: 'dgc-badge', 'data-tone': 'ok' }, t('findFound')),
              h('span', null, `${t('findMatches')}: ${Array.isArray(findResult.matches) ? findResult.matches.length : 0}`),
              findResult.elapsedMs !== undefined ? h('span', null, `${t('findElapsed')}: ${findResult.elapsedMs} ms`) : null,
              findResult.searchRegion
                ? h('span', { className: 'dgc-mono' },
                  `region=${findResult.searchRegion.x},${findResult.searchRegion.y},`
                  + `${findResult.searchRegion.width}×${findResult.searchRegion.height}`)
                : null)
            : h('p', { className: 'dgc-note' }, t('findNotFound')),
          findResult.found && Array.isArray(findResult.matches) && findResult.matches.length
            ? h('div', { className: 'dgc-step-list' }, findResult.matches.map((match, index) => h('div', {
              key: `match-${index}`,
              className: 'dgc-step',
            },
            h('span', { className: 'dgc-mono' }, `#${index + 1}`),
            h('span', { className: 'dgc-mono' },
              `(${match.center ? match.center.x : '?'}, ${match.center ? match.center.y : '?'})`),
            h('span', { className: 'dgc-note' }, `${t('findScore')} ${Number(match.score).toFixed(3)}`),
            h('span', { className: 'dgc-note' }, `${t('findScale')} ${match.scale}`),
            h(ActionButton, { onClick: () => clickMatch(match) }, t('findClickPoint')))))
            : null)
        : null;

      // 技能运行结果（总结果 + 逐步结果）
      const skillRunBlock = skillRunResult
        ? h('div', { className: 'dgc-shot' },
          h('div', { className: 'dgc-meta' },
            h('span', {
              className: 'dgc-badge',
              'data-tone': skillRunResult.success ? 'ok' : 'error',
            }, `${t('skillRunOverall')}: ${skillRunResult.success ? t('skillRunSuccess') : t('skillRunFailed')}`),
            skillRunResult.aborted
              ? h('span', { className: 'dgc-badge', 'data-tone': 'warn' },
                `${t('skillRunAborted')}${skillRunResult.abortReason ? ` (${skillRunResult.abortReason})` : ''}`)
              : null,
            skillRunResult.dryRun ? h('span', { className: 'dgc-badge' }, 'dryRun') : null,
            h('span', null, `${t('skillRunDuration')}: ${formatDurationMs(skillRunResult.durationMs)}`),
            skillRunResult.estimatedDurationMs === undefined
              ? null
              : h('span', null, `${t('skillEstimate')}: ${formatDurationMs(skillRunResult.estimatedDurationMs)}`)),
          skillRunResult.failedStep
            ? h('p', { className: 'dgc-error' },
              `${t('skillFailedStep')}: #${skillRunResult.failedStep.index} `
              + `${skillRunResult.failedStep.action} [${skillRunResult.failedStep.code || 'ERROR'}] `
              + `${skillRunResult.failedStep.message || ''}`)
            : null,
          h('p', { className: 'dgc-subhead' }, t('skillRunSteps')),
          Array.isArray(skillRunResult.steps) && skillRunResult.steps.length
            ? h('div', { className: 'dgc-step-list' }, skillRunResult.steps.map((step, index) => h('div', {
              key: `step-${index}`,
              className: 'dgc-step',
              'data-ok': step.ok ? '1' : '0',
            },
            h('span', { className: 'dgc-mono' }, `#${step.index === undefined ? index + 1 : step.index}`),
            h('span', { className: 'dgc-mono' }, String(step.action || '')),
            h('span', null, step.ok ? 'OK' : (step.code || 'ERROR')),
            step.durationMs === undefined
              ? null
              : h('span', { className: 'dgc-note' }, formatDurationMs(step.durationMs)),
            step.message ? h('span', { className: 'dgc-note' }, String(step.message)) : null)))
            : h('p', { className: 'dgc-note' }, t('skillRunNoSteps')),
          skillRunResult.dryRun ? h('p', { className: 'dgc-hint' }, t('skillDryRunNote')) : null)
        : null;

      // 结构化步骤表格的一行：# / action / 关键参数 / waitMs / note / 上移下移删除
      const renderSkillStepRow = (step, index) => {
        const locked = Boolean(busy || loopActive);
        const defs = STEP_PARAM_FIELDS[step.action];
        let paramsCell;
        if (defs) {
          paramsCell = h('div', { className: 'dgc-step-cells' }, defs.map((def) => {
            const raw = step.params ? step.params[def.key] : undefined;
            const variable = typeof raw === 'string' && raw.trim().startsWith('$');
            if (def.type === 'list') {
              const text = Array.isArray(raw) ? raw.join(', ') : (raw === undefined || raw === null ? '' : String(raw));
              return h('input', {
                key: def.key,
                type: 'text',
                className: 'dgc-input dgc-cell dgc-cell-list',
                value: text,
                placeholder: def.key,
                title: t('skillTemplatesHint'),
                disabled: locked,
                'aria-label': def.key,
                onChange: (event) => updateSkillStepParam(index, def.key, event.target.value),
              });
            }
            if (def.type === 'number' && !variable) {
              return h('input', {
                key: def.key,
                type: 'number',
                className: 'dgc-input dgc-mono dgc-cell dgc-cell-num',
                value: raw === undefined || raw === null ? '' : String(raw),
                placeholder: def.key,
                disabled: locked,
                'aria-label': def.key,
                onChange: (event) => updateSkillStepParam(index, def.key, event.target.value),
              });
            }
            const text = raw === undefined || raw === null ? '' : String(raw);
            const isText = def.key === 'text';
            return h('input', {
              key: def.key,
              type: 'text',
              className: `dgc-input dgc-cell ${isText ? 'dgc-mono dgc-cell-text' : 'dgc-cell-sm'}`,
              value: text,
              placeholder: def.key,
              title: isText ? fillTemplate(t('skillChars'), { n: text.length }) : def.key,
              disabled: locked,
              'aria-label': def.key,
              onChange: (event) => updateSkillStepParam(index, def.key, event.target.value),
            });
          }));
        } else {
          // 其它动作：通用 params JSON 文本
          const text = step.paramsText === undefined ? JSON.stringify(step.params || {}) : step.paramsText;
          paramsCell = h('div', { className: 'dgc-step-cells' },
            h('input', {
              type: 'text',
              className: 'dgc-input dgc-mono dgc-cell dgc-cell-wide',
              value: text,
              placeholder: '{"x":100,"y":200}',
              title: t('skillStepJsonInvalid'),
              disabled: locked,
              'aria-label': 'params',
              onChange: (event) => updateSkillStep(index, { paramsText: event.target.value }),
            }));
        }
        return h('div', { key: `step-${index}`, className: 'dgc-step-row' },
          h('span', { className: 'dgc-mono' }, String(index + 1)),
          h('input', {
            type: 'text',
            className: 'dgc-input dgc-mono dgc-cell dgc-cell-action',
            value: String(step.action || ''),
            placeholder: 'wait',
            disabled: locked,
            'aria-label': t('skillStepAction'),
            onChange: (event) => updateSkillStepAction(index, event.target.value),
          }),
          paramsCell,
          h('input', {
            type: 'number',
            className: 'dgc-input dgc-mono dgc-cell dgc-cell-num',
            value: step.waitMs === undefined || step.waitMs === null ? '0' : String(step.waitMs),
            min: 0,
            max: 60000,
            placeholder: '0',
            disabled: locked,
            'aria-label': t('skillStepWaitCol'),
            onChange: (event) => updateSkillStep(index, { waitMs: event.target.value }),
          }),
          h('input', {
            type: 'text',
            className: 'dgc-input dgc-cell',
            value: step.note === undefined || step.note === null ? '' : String(step.note),
            maxLength: 200,
            placeholder: t('skillStepNoteCol'),
            disabled: locked,
            'aria-label': t('skillStepNoteCol'),
            onChange: (event) => updateSkillStep(index, { note: event.target.value }),
          }),
          h('div', { className: 'dgc-step-ops' },
            h('button', {
              type: 'button',
              className: 'dgc-quick-btn',
              title: t('skillStepUp'),
              disabled: locked || index === 0,
              onClick: () => moveSkillStep(index, -1),
            }, '↑'),
            h('button', {
              type: 'button',
              className: 'dgc-quick-btn',
              title: t('skillStepDown'),
              disabled: locked || index === skillSteps.length - 1,
              onClick: () => moveSkillStep(index, 1),
            }, '↓'),
            h('button', {
              type: 'button',
              className: 'dgc-quick-btn',
              title: t('skillStepDelete'),
              disabled: locked,
              onClick: () => removeSkillStep(index),
            }, '×')));
      };

      // 失败步骤详情（loop_status.results[].failedStep）：第 N 步 / action / code / message / 失败截图
      const renderFailedStepDetail = (failedStep) => {
        if (!failedStep || typeof failedStep !== 'object') return null;
        const index = failedStep.index === undefined || failedStep.index === null ? '?' : failedStep.index;
        return h('div', { className: 'dgc-step-fail' },
          h('span', { className: 'dgc-mono' }, fillTemplate(t('skillStepLabel'), { n: index })),
          h('span', { className: 'dgc-mono' }, String(failedStep.action || '')),
          h('span', { className: 'dgc-mono' }, `${t('skillErrorCode')}: ${failedStep.code || 'ERROR'}`),
          failedStep.message ? h('span', { className: 'dgc-note' }, String(failedStep.message)) : null,
          failedStep.screenshotPath
            ? h('span', { className: 'dgc-note dgc-mono' }, `${t('skillScreenshot')}: ${failedStep.screenshotPath}`)
            : null,
          failedStep.screenshotPath
            ? h(ActionButton, { onClick: () => copyFailedScreenshot(failedStep.screenshotPath) }, t('copy'))
            : null);
      };

      // 结束汇总里最近一个失败轮次的 failedStep
      const lastFailedRound = (() => {
        for (let index = loopResults.length - 1; index >= 0; index -= 1) {
          if (loopResults[index] && loopResults[index].failedStep) return loopResults[index];
        }
        return null;
      })();

      return h('div', { className: 'dgc-wrap' },
        // ---- 头部 ----
        h('header', { className: 'dgc-header' },
          h('div', null,
            h('h2', { className: 'dgc-title' }, t('title')),
            h('p', { className: 'dgc-sub' }, t('subtitle')),
            h('p', { className: 'dgc-hint' }, t('safety'))),
          h('div', { className: 'dgc-header-side' },
            statusBadge,
            busy ? h('span', { className: 'dgc-badge', 'data-tone': 'warn' }, `${t('busy')}…`) : null)),

        // ---- 最近一次结果（成功绿色 / 失败红色由 data-ok + 错误 token 控制） ----
        result
          ? h('div', {
            className: 'dgc-result',
            'data-ok': result.ok ? '1' : '0',
            role: 'status',
            'aria-live': 'polite',
          },
          h('span', { className: 'dgc-mono' }, result.action),
          h('span', { className: 'dgc-result-text' }, result.text || t('done')))
          : null,

        // ---- 1. 状态区 ----
        h(Card, {
          title: t('statusTitle'),
          actions: h(ActionButton, {
            key: 'refresh',
            disabled: statusBusy || busy,
            onClick: refreshStatus,
          }, statusBusy ? t('refreshing') : t('refresh')),
        },
        !status && !statusError && statusBusy ? h('p', { className: 'dgc-note' }, t('loading')) : null,
        status
          ? h('div', { className: 'dgc-status-grid' },
            h(Kv, { label: t('statusBridge'), value: bridge ? (bridge.ready ? t('ready') : t('notReady')) : t('unknown') }),
            h(Kv, { label: t('statusPythonPath'), value: bridge && bridge.pythonPath, mono: true }),
            h(Kv, { label: t('statusPythonVersion'), value: backendInfo && backendInfo.pythonVersion }),
            h(Kv, { label: t('statusPlatform'), value: backendInfo && backendInfo.platform }),
            h(Kv, { label: t('statusBackend'), value: backendInfo && backendInfo.backend, mono: true }),
            h(Kv, { label: t('statusDpi'), value: backendInfo ? (backendInfo.dpiAware ? t('yes') : t('no')) : null }),
            h(Kv, { label: t('statusScreen'), value: formatScreen(backendInfo && backendInfo.virtualScreen) }),
            h(Kv, { label: t('statusPrimary'), value: formatScreen(backendInfo && backendInfo.primary) }),
            h(Kv, {
              label: t('statusPointer'),
              value: backendInfo && backendInfo.pointer ? `${backendInfo.pointer.x}, ${backendInfo.pointer.y}` : null,
              mono: true,
            }),
            h(Kv, { label: t('statusShotDir'), value: backendInfo && backendInfo.shotDir, mono: true }),
            h(Kv, { label: t('statusLogDir'), value: backendInfo && backendInfo.logDir, mono: true }))
          : null,
        !backendInfo && status ? h('p', { className: 'dgc-note' }, t('statusBackendHint')) : null,
        lastError ? h('p', { className: 'dgc-error' }, lastError) : null,
        stderrTail ? h('pre', { className: 'dgc-pre' }, stderrTail) : null),

        // ---- 2. 技能库（v0.5：结构化步骤表格 + 高级 JSONC + 运行进度） ----
        h(Card, {
          title: t('skillTitle'),
          actions: h('div', { className: 'dgc-row' },
            h(ActionButton, {
              key: 'refresh',
              disabled: busy || skillsBusy || loopActive,
              onClick: refreshSkills,
            }, skillsBusy ? t('refreshing') : t('skillRefresh')),
            h(ActionButton, {
              key: 'new',
              disabled: busy || loopActive,
              onClick: newSkill,
            }, t('skillNew'))),
        },
        // 列表 + 已加载数量
        h('div', { className: 'dgc-row' },
          h(SelectField, {
            label: t('skillListLabel'),
            value: skillEditor ? skillEditor.id : '',
            onChange: (value) => { if (value) loadSkill(value); },
            options: skillOptions,
            disabled: busy || loopActive || !skills || skills.length === 0,
            wide: true,
          }),
          skills
            ? h('span', { className: 'dgc-note' }, `${t('skillListLabel')}: ${skills.length}`)
            : h('span', { className: 'dgc-note' }, t('skillNotLoaded'))),
        skills && skills.length === 0 ? h('p', { className: 'dgc-note' }, t('skillListEmpty')) : null,
        skillSummary
          ? h('div', { className: 'dgc-meta' },
            h('span', null, `${t('skillFieldTitle')}: ${skillSummary.title || '—'}`),
            h('span', null, `${t('skillFieldGame')}: ${skillSummary.game || '—'}`),
            h('span', null, `${t('skillStepCount')}: ${skillSummary.stepCount === undefined ? 0 : skillSummary.stepCount}`),
            skillSummary.backupCount === undefined
              ? null
              : h('span', null, `${t('skillBackupCount')}: ${skillSummary.backupCount}`),
            skillSummary.updatedAt
              ? h('span', null, `${t('skillUpdatedAt')}: ${formatTime(skillSummary.updatedAt) || '—'}`)
              : null,
            h('span', { className: 'dgc-note dgc-mono' }, skillEditor && skillEditor.id ? `id=${skillEditor.id}` : 'id=(new)'))
          : null,
        skillError ? h('p', { className: 'dgc-error' }, skillError) : null,
        // v0.6：连接中断 / stale 提示 + 重置本地运行状态（恢复按钮，不停止后台循环）
        connError
          ? h('div', { className: 'dgc-result', 'data-ok': '0' },
            h('span', { className: 'dgc-result-text' }, connError),
            h(ActionButton, { key: 'reset-state', onClick: resetLocalRunState }, t('skillResetState')))
          : null,
        connError ? h('p', { className: 'dgc-hint' }, t('skillResetHint')) : null,
        skillDeleteNote ? h('p', { className: 'dgc-note' }, skillDeleteNote) : null,
        skillEditor
          ? h('div', { className: 'dgc-subblock' },
            h('h4', { className: 'dgc-subhead' }, t('skillEditorTitle')),
            skillDetailBusy ? h('p', { className: 'dgc-note' }, t('loading')) : null,
            // 只读步骤摘要（skill_get.stepsView 生成的中文注释），折叠区
            skillStepsView.length
              ? h('details', { className: 'dgc-details' },
                h('summary', null, t('skillStepsView')),
                h('div', { className: 'dgc-details-body' },
                  h('div', { className: 'dgc-step-list dgc-step-list-compact' }, skillStepsView.map((step) => h('div', {
                    key: `view-${step.index === undefined ? step.action : step.index}-${step.action || ''}`,
                    className: 'dgc-step',
                  },
                  h('span', { className: 'dgc-mono' },
                    fillTemplate(t('skillStepLabel'), { n: step.index === undefined ? '?' : step.index })),
                  h('span', { className: 'dgc-mono' }, String(step.action || '')),
                  h('span', { className: 'dgc-step-comment' }, String(step.comment || '')),
                  h('span', { className: 'dgc-note dgc-mono' },
                    `waitMs=${step.waitMs === undefined || step.waitMs === null ? 0 : step.waitMs}`),
                  step.saveAs ? h('span', { className: 'dgc-note dgc-mono' }, `saveAs=${step.saveAs}`) : null)))))
              : null,
            // 结构化步骤表格（默认展示；保存为结构化字段，后端归一化 + 生成中文注释 JSONC）
            h('h4', { className: 'dgc-subhead' }, t('skillStructuredTitle')),
            skillSteps.length
              ? h('div', { className: 'dgc-step-table' },
                h('div', { className: 'dgc-step-row dgc-step-row-head' },
                  h('span', null, '#'),
                  h('span', null, t('skillStepAction')),
                  h('span', null, t('skillStepParams')),
                  h('span', null, t('skillStepWaitCol')),
                  h('span', null, t('skillStepNoteCol')),
                  h('span', null, '')),
                skillSteps.map((step, index) => renderSkillStepRow(step, index)))
              : h('p', { className: 'dgc-note' }, t('skillStepsViewEmpty')),
            h('div', { className: 'dgc-row' },
              h(ActionButton, {
                disabled: busy || loopActive,
                onClick: addSkillStep,
              }, t('skillAddStep')),
              h(ActionButton, {
                primary: true,
                disabled: busy || loopActive,
                onClick: saveSkill,
              }, busy ? t('skillSaving') : t('skillSave')),
              skillSaveNote ? h('span', { className: 'dgc-note' }, skillSaveNote) : null),
            h('p', { className: 'dgc-hint' }, t('skillStructuredHint')),
            // 高级：完整配置文件（JSONC）折叠区（content 模式保存）
            h('details', { className: 'dgc-details' },
              h('summary', null, t('skillAdvancedTitle')),
              h('div', { className: 'dgc-details-body' },
                h('label', { className: 'dgc-field dgc-field-full' },
                  h('span', { className: 'dgc-field-label' }, t('skillConfigTitle')),
                  h('textarea', {
                    className: 'dgc-textarea dgc-code dgc-code-tall',
                    rows: 20,
                    value: skillEditor.content,
                    placeholder: t('skillConfigTitle'),
                    spellCheck: false,
                    disabled: busy || loopActive,
                    'aria-label': t('skillConfigTitle'),
                    onChange: (event) => setSkillContent(event.target.value),
                  })),
                h('div', { className: 'dgc-row' },
                  h(ActionButton, {
                    primary: true,
                    disabled: busy || loopActive,
                    onClick: saveSkillContent,
                  }, busy ? t('skillSaving') : t('skillSaveContent'))),
                h('p', { className: 'dgc-hint' }, t('skillConfigHint')),
                h('p', { className: 'dgc-hint' }, t('skillJsoncHint')))),
            // 历史版本：下拉 + 恢复
            h('div', { className: 'dgc-subblock' },
              h('h4', { className: 'dgc-subhead' }, t('skillVersions')),
              h('div', { className: 'dgc-row' },
                h(SelectField, {
                  label: t('skillVersionPick'),
                  value: skillVersionPick,
                  onChange: setSkillVersionPick,
                  options: versionOptions,
                  disabled: busy || loopActive || !skillVersions || skillVersions.count === 0,
                  wide: true,
                }),
                h(ActionButton, {
                  disabled: busy || loopActive || !skillVersions || skillVersions.count === 0,
                  onClick: restoreSkillVersion,
                }, busy ? t('skillRestoring') : t('skillRestore')),
                skillVersions && skillVersions.count === 0
                  ? h('span', { className: 'dgc-note' }, t('skillVersionsEmpty'))
                  : null,
                skillRestoreNote ? h('span', { className: 'dgc-note' }, skillRestoreNote) : null)),
            // 运行：唯一「轮数」控制（rounds=1 单次、>1 循环，全部走 loop_start）+ 干跑
            h('div', { className: 'dgc-subblock' },
              h('h4', { className: 'dgc-subhead' }, t('skillRunConfig')),
              h('div', { className: 'dgc-row' },
                h(Field, {
                  label: t('skillLoopRounds'),
                  value: loopConfig.rounds,
                  placeholder: '1',
                  numeric: true,
                  onChange: (value) => setLoopConfig((current) => ({ ...current, rounds: value })),
                }),
                h(Field, {
                  label: t('skillLoopInterval'),
                  value: loopConfig.intervalMs,
                  placeholder: '500',
                  numeric: true,
                  onChange: (value) => setLoopConfig((current) => ({ ...current, intervalMs: value })),
                }),
                h('label', { className: 'dgc-check' },
                  h('input', {
                    type: 'checkbox',
                    checked: loopConfig.immediate,
                    disabled: loopBusy,
                    onChange: (event) => setLoopConfig((current) => ({ ...current, immediate: event.target.checked })),
                  }),
                  h('span', null, t('skillLoopImmediate'))),
                h('label', { className: 'dgc-check' },
                  h('input', {
                    type: 'checkbox',
                    checked: loopConfig.continueOnError,
                    disabled: loopBusy,
                    onChange: (event) => setLoopConfig((current) => ({ ...current, continueOnError: event.target.checked })),
                  }),
                  h('span', null, t('skillLoopContinueOnError'))),
                h(ActionButton, {
                  primary: true,
                  disabled: busy || loopActive || loopBusy || !(skillEditor && skillEditor.id),
                  onClick: startLoop,
                }, t('skillRun')),
                h(ActionButton, {
                  disabled: !loopActive || loopBusy,
                  onClick: stopLoop,
                }, loopBusy && loopActive ? t('skillLoopStopping') : t('skillLoopStop')),
                h(ActionButton, {
                  disabled: busy || loopActive || !(skillEditor && skillEditor.id),
                  onClick: runDryRun,
                }, t('dryRun'))),
              h('p', { className: 'dgc-hint' }, t('skillLoopRoundsHint')),
              h('p', { className: 'dgc-hint' }, t('skillLoopIntervalHint')),
              h('p', { className: 'dgc-hint' }, t('skillLoopFailureHint')),
              loopConfig.immediate ? h('p', { className: 'dgc-hint' }, t('skillLoopImmediateHint')) : null,
              loopActive ? h('p', { className: 'dgc-hint' }, t('skillRunLocked')) : null,
              loopError ? h('p', { className: 'dgc-error' }, loopError) : null,
              // 进度：统一 loop_status 轮询（700ms）
              loopActive
                ? h('div', { className: 'dgc-result', 'data-ok': '1' },
                  h('span', { className: 'dgc-mono' },
                    fillTemplate(t('skillLoopProgressText'), {
                      current: loopCounts.current,
                      rounds: loopCounts.rounds,
                      completed: loopCounts.completed,
                      failed: loopCounts.failed,
                      sec: loopCurrentElapsedSec,
                    })),
                  loopCurrentStep
                    ? h('span', { className: 'dgc-mono' },
                      fillTemplate(t('skillCurrentStepText'), {
                        index: loopCurrentStep.index,
                        total: loopCurrentStep.total,
                        action: loopCurrentStep.action,
                      }))
                    : null,
                  loopStopRequested
                    ? h('span', { className: 'dgc-badge', 'data-tone': 'warn' }, t('skillLoopRequested'))
                    : null)
                : null,
              // 循环结束汇总（含最近失败轮次的 failedStep / 失败截图）
              !loopActive && loopState && loopState.finishedAt
                ? h('div', {
                  className: 'dgc-result',
                  'data-ok': loopState.failedRounds ? '0' : '1',
                },
                h('span', null,
                  `${t('skillLoopSummary')} · ${t('skillLoopCompleted')}: ${loopCounts.completed}`
                  + ` · ${t('skillLoopFailed')}: ${loopCounts.failed}`),
                loopState.lastError
                  ? h('span', { className: 'dgc-error' }, `${t('skillLoopLastError')}: ${loopState.lastError}`)
                  : null,
                lastFailedRound ? renderFailedStepDetail(lastFailedRound.failedStep) : null)
                : null,
              // 每轮结果列表（最近 20 轮，含每轮 failedStep / 中止原因 / 失败截图）
              loopResults.length
                ? h('div', null,
                  h('p', { className: 'dgc-subhead' }, t('skillLoopResults')),
                  h('div', { className: 'dgc-step-list' }, loopResults.map((round) => h('div', {
                    key: `loop-round-${round.round}`,
                    className: 'dgc-step',
                    'data-ok': round.ok ? '1' : '0',
                  },
                  h('span', { className: 'dgc-mono' }, fillTemplate(t('skillLoopRound'), { n: round.round })),
                  h('span', null, round.aborted
                    ? t('skillRunAborted')
                    : (round.ok ? t('skillRunSuccess') : t('skillRunFailed'))),
                  round.aborted && round.abortReason
                    ? h('span', { className: 'dgc-note dgc-mono' }, `(${round.abortReason})`)
                    : null,
                  round.errorCode ? h('span', { className: 'dgc-mono' }, String(round.errorCode)) : null,
                  round.durationMs === undefined
                    ? null
                    : h('span', { className: 'dgc-note' }, formatDurationMs(round.durationMs)),
                  round.message ? h('span', { className: 'dgc-note' }, String(round.message)) : null,
                  renderFailedStepDetail(round.failedStep)))))
                : (loopActive ? h('p', { className: 'dgc-note' }, t('skillLoopNoResults')) : null),
              loopCopyNote ? h('p', { className: 'dgc-note' }, loopCopyNote) : null,
              skillRunBlock),
            // 删除（二次点击确认，软删到 .trash/）
            h('div', { className: 'dgc-subblock' },
              h('div', { className: 'dgc-row' },
                h(ActionButton, {
                  disabled: busy || loopActive,
                  onClick: deleteSkill,
                }, skillDeleteArmed ? t('skillDeleteConfirm') : t('skillDelete')),
                skillDeleteArmed
                  ? h(ActionButton, { onClick: () => setSkillDeleteArmed(false) }, t('cancel'))
                  : null)))
          : h('p', { className: 'dgc-note' }, t('skillNoEditor')),
        h('p', { className: 'dgc-hint' }, t('skillHint'))),

        // ---- 3. 鼠标区 ----
        h(Card, { title: t('mouseTitle') },
          h('div', { className: 'dgc-row' },
            h(Field, { label: t('x'), value: pos.x, onChange: setPosField('x'), placeholder: '0', numeric: true }),
            h(Field, { label: t('y'), value: pos.y, onChange: setPosField('y'), placeholder: '0', numeric: true }),
            h(ActionButton, { primary: true, disabled: busy, onClick: onMove }, t('move')),
            h(ActionButton, { disabled: busy, onClick: onClickButton('left') }, t('leftClick')),
            h(ActionButton, { disabled: busy, onClick: onClickButton('right') }, t('rightClick')),
            h(ActionButton, { disabled: busy, onClick: onDoubleClick }, t('doubleClick')),
            h(ActionButton, { disabled: busy, onClick: onMouseButton('mouse_down') }, t('mouseDown')),
            h(ActionButton, { disabled: busy, onClick: onMouseButton('mouse_up') }, t('mouseUp'))),
          h('div', { className: 'dgc-row' },
            h(Field, { label: t('dragToX'), value: pos.x2, onChange: setPosField('x2'), placeholder: '0', numeric: true }),
            h(Field, { label: t('dragToY'), value: pos.y2, onChange: setPosField('y2'), placeholder: '0', numeric: true }),
            h(ActionButton, { disabled: busy, onClick: onDrag }, t('drag')),
            h(ActionButton, { disabled: busy, onClick: onScroll(3) }, t('scrollUp')),
            h(ActionButton, { disabled: busy, onClick: onScroll(-3) }, t('scrollDown')),
            h(ActionButton, { disabled: busy, onClick: onReadPosition }, t('position'))),
          h('p', { className: 'dgc-hint' }, t('mouseHint'))),

        // ---- 4. 键盘区 ----
        h(Card, { title: t('keyboardTitle') },
          h('div', { className: 'dgc-row' },
            h(Field, {
              label: t('keyLabel'),
              value: keyInput,
              onChange: setKeyInput,
              placeholder: t('keyPlaceholder'),
            }),
            h(ActionButton, { primary: true, disabled: busy, onClick: onPressKey }, t('pressKey')),
            h(Field, {
              label: t('comboLabel'),
              value: comboInput,
              onChange: setComboInput,
              placeholder: t('comboPlaceholder'),
            }),
            h(ActionButton, { disabled: busy, onClick: onSendCombo }, t('sendCombo'))),
          h('div', { className: 'dgc-row' },
            h('textarea', {
              className: 'dgc-textarea',
              rows: 2,
              value: textInput,
              placeholder: t('textPlaceholder'),
              'aria-label': t('textLabel'),
              spellCheck: false,
              onChange: (event) => setTextInput(event.target.value),
            }),
            h(ActionButton, { primary: true, disabled: busy, onClick: onTypeText }, t('typeText'))),
          h('div', { className: 'dgc-row' },
            h('span', { className: 'dgc-field-label' }, t('quickKeys')),
            h('div', { className: 'dgc-quick' }, quickKeys.map((item) => h('button', {
              key: item.key,
              type: 'button',
              className: 'dgc-quick-btn',
              title: `${t('quickKeys')}: ${item.label}`,
              'aria-label': `${t('quickKeys')}: ${item.label}`,
              onPointerDown: beginQuickKey(item.key),
            }, item.label)))),
          h('p', { className: 'dgc-hint' }, t('quickHint'))),

        // ---- 5. 截图区 ----
        h(Card, { title: t('shotTitle') },
          h('div', { className: 'dgc-row' },
            h(ActionButton, { primary: true, disabled: busy, onClick: () => takeScreenshot(false) }, t('shotFull')),
            h(ActionButton, { disabled: busy, onClick: () => takeScreenshot(true) }, t('shotRegion')),
            h('label', { className: 'dgc-check' },
              h('input', {
                type: 'checkbox',
                checked: wantBase64,
                disabled: busy,
                onChange: (event) => setWantBase64(event.target.checked),
              }),
              h('span', null, t('wantBase64')))),
          h('div', { className: 'dgc-row' },
            h(Field, { label: t('regionX'), value: region.x, onChange: (value) => setRegion((current) => ({ ...current, x: value })), placeholder: '0', numeric: true }),
            h(Field, { label: t('regionY'), value: region.y, onChange: (value) => setRegion((current) => ({ ...current, y: value })), placeholder: '0', numeric: true }),
            h(Field, { label: t('regionWidth'), value: region.width, onChange: (value) => setRegion((current) => ({ ...current, width: value })), placeholder: '200', numeric: true }),
            h(Field, { label: t('regionHeight'), value: region.height, onChange: (value) => setRegion((current) => ({ ...current, height: value })), placeholder: '120', numeric: true })),
          shotBlock,
          h('p', { className: 'dgc-hint' }, t('shotHint'))),

        // ---- 6. 窗口截图区（v0.2：list_windows + capture_window） ----
        h(Card, { title: t('windowTitle') },
          windowPlatformUnsupported ? h('p', { className: 'dgc-hint' }, t('windowUnsupported')) : null,
          // 第一行：刷新窗口列表 + 已加载数量
          h('div', { className: 'dgc-row' },
            h(ActionButton, {
              disabled: busy || windowsBusy,
              onClick: refreshWindows,
            }, windowsBusy ? t('windowsRefreshing') : t('windowsRefresh')),
            windows
              ? h('span', { className: 'dgc-note' }, `${t('windowsCount')}: ${windows.length}`)
              : h('span', { className: 'dgc-note' }, t('windowsNotLoaded'))),
          // 第二行：窗口下拉 + 引擎下拉 + 抓取按钮
          h('div', { className: 'dgc-row' },
            h('label', { className: 'dgc-field dgc-window-select' },
              h('span', { className: 'dgc-field-label' }, t('windowPick')),
              h('select', {
                className: 'dgc-input',
                value: selectedHwnd,
                disabled: busy || !windows || windows.length === 0,
                title: selectedWindow ? windowLabelOf(selectedWindow) : '',
                'aria-label': t('windowPick'),
                onChange: (event) => {
                  setSelectedHwnd(event.target.value);
                  setWindowError('');
                },
              },
              !windows || windows.length === 0
                ? h('option', { value: '' }, windows ? t('windowsEmpty') : t('windowsNotLoaded'))
                : windows.map((item) => h('option', {
                  key: String(item.hwnd),
                  value: String(item.hwnd),
                }, windowLabelOf(item))))),
            h('label', { className: 'dgc-field' },
              h('span', { className: 'dgc-field-label' }, t('windowEngine')),
              h('select', {
                className: 'dgc-input',
                value: engine,
                disabled: busy,
                'aria-label': t('windowEngine'),
                onChange: (event) => setEngine(event.target.value),
              }, engineOptions.map((item) => h('option', { key: item.value, value: item.value }, item.label)))),
            h(ActionButton, {
              primary: true,
              disabled: busy || selectedHwnd === '',
              onClick: captureWindow,
            }, capturingWindow ? t('windowCapturing') : t('windowCapture')),
            h(ActionButton, {
              disabled: busy || selectedHwnd === '',
              onClick: activateSelectedWindow,
            }, activatingWindow ? t('activatingWindow') : t('activateWindow'))),
          windowError ? h('p', { className: 'dgc-error' }, windowError) : null,
          activationBlock,
          // 抓取结果：预览 + 路径 + 尺寸/字节 + 实际引擎 + 回退原因/警告
          windowShot
            ? h('div', { className: 'dgc-shot' },
              h('div', { className: 'dgc-row' },
                h('span', { className: 'dgc-field-label' }, t('shotPath')),
                h('input', {
                  ref: windowPathRef,
                  className: 'dgc-input dgc-mono dgc-grow',
                  readOnly: true,
                  value: windowShot.absolutePath || windowShot.path || '',
                  'aria-label': t('shotPath'),
                  onFocus: (event) => event.target.select(),
                }),
                h(ActionButton, { onClick: copyWindowPath }, t('copy')),
                windowCopyNote ? h('span', { className: 'dgc-note' }, windowCopyNote) : null),
              h('div', { className: 'dgc-row' },
                h('span', {
                  className: 'dgc-badge',
                  'data-tone': windowShot.engine === 'wgc'
                    ? 'ok'
                    : windowShot.engine === 'screen' ? 'warn' : undefined,
                }, `${t('windowActualEngine')}: ${windowShot.engine || 'auto'}`),
                h('span', { className: 'dgc-note' },
                  `${windowShot.width} × ${windowShot.height} · ${formatBytes(windowShot.bytes)}`
                  + ` · ${windowShot.format || 'png'}`
                  + `${windowShot.window && windowShot.window.title ? ` · ${windowShot.window.title}` : ''}`)),
              windowShot.fallbackReason
                ? h('p', { className: 'dgc-warn' }, `${t('windowFallback')}: ${windowShot.fallbackReason}`)
                : null,
              Array.isArray(windowShot.warnings) && windowShot.warnings.length
                ? h('p', { className: 'dgc-warn' }, `${t('windowWarnings')}: ${windowShot.warnings.map(String).join('; ')}`)
                : null,
              windowShot.base64
                ? h('img', {
                  className: 'dgc-shot-img',
                  alt: t('shotPreview'),
                  src: `data:${mimeOf(windowShot.format)};base64,${windowShot.base64}`,
                })
                : h('p', { className: 'dgc-note' }, t('shotNoPreview')))
            : h('p', { className: 'dgc-note' }, t('windowNoShot')),
          h('p', { className: 'dgc-hint' }, t('windowHint'))),

        // ---- 7. 圆形轨迹（v0.3：mouse_circle） ----
        h(Card, { title: t('circleTitle') },
          h('div', { className: 'dgc-row' },
            h(Field, { label: t('circleCenterX'), value: circle.centerX, onChange: setCircleField('centerX'), placeholder: '1920', numeric: true }),
            h(Field, { label: t('circleCenterY'), value: circle.centerY, onChange: setCircleField('centerY'), placeholder: '1080', numeric: true }),
            h(Field, { label: t('circleRadius'), value: circle.radius, onChange: setCircleField('radius'), placeholder: '480', numeric: true }),
            h(Field, { label: t('circlePeriod'), value: circle.periodMs, onChange: setCircleField('periodMs'), placeholder: '2500', numeric: true }),
            h(Field, { label: t('circleDuration'), value: circle.durationMs, onChange: setCircleField('durationMs'), placeholder: '30000', numeric: true })),
          h('div', { className: 'dgc-row' },
            h(SelectField, {
              label: t('circleDirection'),
              value: circle.direction,
              onChange: setCircleField('direction'),
              options: circleDirectionOptions,
              disabled: busy || circleRunning,
            }),
            h(Field, { label: t('circleSamples'), value: circle.samplesPerSecond, onChange: setCircleField('samplesPerSecond'), placeholder: '120', numeric: true }),
            h(SelectField, {
              label: t('circleButton'),
              value: circle.button,
              onChange: setCircleField('button'),
              options: circleButtonOptions,
              disabled: busy || circleRunning,
            }),
            h(Field, { label: t('abortKey'), value: circle.abortKey, onChange: setCircleField('abortKey'), placeholder: 'esc' }),
            h(ActionButton, {
              primary: true,
              disabled: busy || circleRunning,
              onClick: () => runCircle(false),
            }, circleRunning ? t('circleStarting') : t('circleStart')),
            h(ActionButton, {
              disabled: busy || circleRunning,
              onClick: () => runCircle(true),
            }, t('dryRun')),
            h(ActionButton, {
              disabled: busy || circleRunning,
              onClick: fillCircleFromWindow,
            }, t('circleFillFromWindow'))),
          circleError ? h('p', { className: 'dgc-error' }, circleError) : null,
          circleResultBlock,
          h('p', { className: 'dgc-hint' }, t('circleBlockHint'))),

        // ---- 8. 模板找图（v0.3：find_image） ----
        h(Card, { title: t('findTitle') },
          h('div', { className: 'dgc-row' },
            h(Field, {
              label: t('findTemplate'),
              value: find.template,
              onChange: setFindField('template'),
              placeholder: t('findTemplatePlaceholder'),
              wide: true,
            }),
            h(Field, { label: t('findThreshold'), value: find.threshold, onChange: setFindField('threshold'), placeholder: '0.85', numeric: true }),
            h(SelectField, {
              label: t('findRegion'),
              value: find.regionMode,
              onChange: setFindField('regionMode'),
              options: findRegionOptions,
              disabled: busy,
            }),
            h(ActionButton, {
              primary: true,
              disabled: busy,
              onClick: runFindImage,
            }, findBusy ? t('findRunning') : t('findRun'))),
          findError ? h('p', { className: 'dgc-error' }, findError) : null,
          findResultBlock,
          h('p', { className: 'dgc-hint' }, t('findHint'))),

        // ---- 9. 最近操作日志 ----
        h(Card, {
          title: t('logTitle'),
          actions: h(ActionButton, {
            key: 'clear',
            disabled: !logs.length,
            onClick: () => setLogs([]),
          }, t('clear')),
        },
        h('p', { className: 'dgc-note' }, t('logLimit')),
        logs.length
          ? h('div', { className: 'dgc-log' }, logs.map((entry) => h('div', {
            key: entry.id,
            className: 'dgc-log-item',
            'data-ok': entry.ok ? '1' : '0',
          },
          h('span', { className: 'dgc-log-time' }, entry.time),
          h('span', { className: 'dgc-log-action dgc-mono' }, entry.action),
          h('span', { className: 'dgc-log-msg' }, entry.message))))
          : h('p', { className: 'dgc-note' }, t('logEmpty'))));
    }

    /* ------------------------------------------------------------------ *
     * 插件入口：locale / 样式 / 侧栏入口 / 主面板
     * ------------------------------------------------------------------ */
    function apply(ctx) {
      // locale：注册字典并 bind 出 t（与参考实现一致，返回清理函数交给 ctx.effect）
      ctx.effect(() => ctx.locale.register(NS, dictionaries), 'game-control: locale');
      const t = typeof ctx.locale.bind === 'function'
        ? ctx.locale.bind(NS)
        : (key) => (dictionaries.zh && dictionaries.zh[key]) || key;

      // 样式：注入 <style>，卸载时由 ctx.effect 清理
      const style = document.createElement('style');
      style.setAttribute('data-plugin-css', 'dsh-game-control');
      style.textContent = CSS;
      document.head.appendChild(style);
      ctx.effect(() => () => style.remove(), 'game-control: style');

      // 槽位服务（兼容通过 ctx.get 取的宿主）
      const slots = (typeof ctx.get === 'function' ? ctx.get('slots') : null) || ctx.slots;

      // 左侧栏入口：ownerProps 为 {size, active}
      slots.inject('sidebar.panellist', () => slots.register(
        { name: 'sidebar.panellist', id: ID, order: 72, label: () => t('nav') },
        SidebarIcon,
      ));

      // 主面板：key 必须与侧栏 id 相同，owner 按 id 派发；用包装组件把 t 传进去
      slots.inject('main', () => slots.register(
        { name: 'main', key: ID },
        () => h(Panel, { t }),
      ));
    }

    return { inject: ['slots', 'locale'], apply };
  },
});
