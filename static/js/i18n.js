/**
 * i18n.js - 中英文翻译系统
 * 使用方式: t('key') 或 t('key', 'en') 强制指定语言
 * 语言切换: switchLang('en' | 'zh')
 */

const translations = {
  zh: {
    // 通用
    appTitle: 'Codex 历史记录管理器',
    appSubtitle: '历史 · 同步 · 备份',
    loading: '加载中...',
    noData: '暂无数据',
    confirm: '确认',
    cancel: '取消',
    save: '保存',
    delete: '删除',
    refresh: '刷新',
    close: '关闭',
    error: '错误',
    success: '成功',
    warning: '警告',
    yes: '是',
    no: '否',

    // 侧边栏
    navStats: '统计面板',
    navSessions: '会话浏览',
    navSync: '账户同步',
    navBackup: '备份管理',
    navSettings: '设置',

    // 统计面板
    statsTitle: 'Token 用量统计',
    totalTokens: '总 Token 数',
    totalSessions: '总会话数',
    todayUsage: '今日使用',
    weekUsage: '本周使用',
    monthUsage: '本月使用',
    dailyTrend: '每日 Token 趋势',
    modelDist: '模型分布',
    providerDist: '提供商分布',
    hourlyDist: '每小时使用分布',
    topSessions: 'Token 用量排行',
    rank: '排名',
    title: '标题',
    model: '模型',
    tokensUsed: '已用 Token',
    lastUpdated: '最后更新',
    date: '日期',
    hour: '小时',
    sessions: '会话数',
    tokens: 'Token 数',

    // 时间筛选
    timeRange: '时间范围',
    last7Days: '最近 7 天',
    last30Days: '最近 30 天',
    last90Days: '最近 90 天',
    thisMonth: '本月',
    customRange: '自定义范围',
    startDate: '开始日期',
    endDate: '结束日期',
    applyFilter: '应用筛选',

    // 秒表
    stopwatch: 'Token 秒表',
    stopwatchDesc: '点击开始，记录时间段内的 Token 用量',
    start: '开始记录',
    stop: '停止记录',
    reset: '重置',
    recording: '记录中...',
    elapsed: '已用时',
    sessionCount: '会话数',
    tokenDiff: 'Token 增量',
    cacheHits: '缓存命中',
    modelsUsed: '使用模型',
    reportTitle: '秒表报告',
    noDataYet: '暂无数据，请点击"开始记录"',

    // 会话浏览
    sessionBrowser: '会话浏览器',
    searchPlaceholder: '搜索会话...',
    filterAll: '全部',
    filterActive: '活跃',
    filterArchived: '已归档',
    filterSourceAll: '所有来源',
    filterSourceUser: '用户',
    filterSourceAgent: '代理',
    filterModelAll: '所有模型',
    filterProviderAll: '所有提供商',
    export: '导出',
    archive: '归档',
    unarchive: '取消归档',
    viewDetails: '查看详情',
    noSessions: '未找到会话',
    page: '第',
    pageOf: '/',
    prevPage: '上一页',
    nextPage: '下一页',
    sessionDetails: '会话详情',
    messages: '消息',
    created: '创建时间',
    updated: '更新时间',
    source: '来源',
    provider: '提供商',
    workDir: '工作目录',

    // 同步
    accountSync: '账户同步',
    currentConfig: '当前配置',
    dbProviderDist: '数据库 Provider 分布',
    previewChanges: '预览变更',
    executeSync: '执行同步',
    syncOnly: '仅同步',
    oneClickRestart: '一键同步重启',
    codexStatus: 'Codex 进程状态',
    running: '运行中',
    notRunning: '未运行',
    killCodex: '关闭 Codex',
    startCodex: '启动 Codex',
    startCodexPP: '启动 Codex++',
    useCodexPP: '使用 Codex++',
    changesToApply: '待应用变更',
    noChanges: '无需变更',
    syncCompleted: '同步完成',
    syncFailed: '同步失败',
    confirmAction: '确认操作？',
    warningCodexRunning: '警告：Codex 正在运行！',

    // 备份
    backupManager: '备份管理',
    createBackup: '创建备份',
    incrementalBackup: '增量备份',
    restore: '还原',
    backupSize: '大小',
    backupTime: '时间',
    backupType: '类型',
    fullBackup: '完整',
    incrementalBackupShort: '增量',
    noBackups: '暂无备份',
    backupCreated: '备份已创建',
    restoreCompleted: '还原完成',

    // 设置
    settings: '设置',
    autoDetectedPaths: '自动检测路径',
    reDetect: '重新检测',
    detectResults: '检测结果',
    configuration: '配置',
    dbPath: '数据库路径',
    sessionsDir: '会话目录',
    archivedDir: '归档目录',
    backupDir: '备份目录',
    codexCliPath: 'Codex CLI 路径',
    codexPPPath: 'Codex++ 路径',
    pageSize: '每页条数',
    backupInterval: '备份间隔（小时）',
    maxBackups: '最大备份数',
    largeFileThreshold: '大文件阈值（MB）',
    maxMessages: '大文件最大消息数',
    autoBackup: '自动备份',
    useCodexPPSetting: '使用 Codex++',
    darkMode: '暗色模式',
    saveSettings: '保存设置',
    resetDefaults: '恢复默认',
    language: '语言',
    english: 'English',
    chinese: '中文',
  },

  en: {
    // General
    appTitle: 'Codex History Manager',
    appSubtitle: 'History · Sync · Backup',
    loading: 'Loading...',
    noData: 'No data available',
    confirm: 'Confirm',
    cancel: 'Cancel',
    save: 'Save',
    delete: 'Delete',
    refresh: 'Refresh',
    close: 'Close',
    error: 'Error',
    success: 'Success',
    warning: 'Warning',
    yes: 'Yes',
    no: 'No',

    // Sidebar
    navStats: 'Statistics',
    navSessions: 'Sessions',
    navSync: 'Sync',
    navBackup: 'Backup',
    navSettings: 'Settings',

    // Stats
    statsTitle: 'Token Usage Statistics',
    totalTokens: 'Total Tokens',
    totalSessions: 'Total Sessions',
    todayUsage: "Today's Usage",
    weekUsage: "This Week",
    monthUsage: "This Month",
    dailyTrend: 'Daily Token Trend',
    modelDist: 'Model Distribution',
    providerDist: 'Provider Distribution',
    hourlyDist: 'Hourly Distribution',
    topSessions: 'Top Token-Heavy Sessions',
    rank: 'Rank',
    title: 'Title',
    model: 'Model',
    tokensUsed: 'Tokens Used',
    lastUpdated: 'Last Updated',
    date: 'Date',
    hour: 'Hour',
    sessions: 'Sessions',
    tokens: 'Tokens',

    // Time filter
    timeRange: 'Time Range',
    last7Days: 'Last 7 Days',
    last30Days: 'Last 30 Days',
    last90Days: 'Last 90 Days',
    thisMonth: 'This Month',
    customRange: 'Custom Range',
    startDate: 'Start Date',
    endDate: 'End Date',
    applyFilter: 'Apply Filter',

    // Stopwatch
    stopwatch: 'Token Stopwatch',
    stopwatchDesc: 'Click start to record token usage during a time period',
    start: 'Start',
    stop: 'Stop',
    reset: 'Reset',
    recording: 'Recording...',
    elapsed: 'Elapsed',
    sessionCount: 'Session Count',
    tokenDiff: 'Token Delta',
    cacheHits: 'Cache Hits',
    modelsUsed: 'Models Used',
    reportTitle: 'Stopwatch Report',
    noDataYet: 'No data yet. Click "Start" to begin.',

    // Sessions
    sessionBrowser: 'Session Browser',
    searchPlaceholder: 'Search sessions...',
    filterAll: 'All',
    filterActive: 'Active',
    filterArchived: 'Archived',
    filterSourceAll: 'All Sources',
    filterSourceUser: 'User',
    filterSourceAgent: 'Agent',
    filterModelAll: 'All Models',
    filterProviderAll: 'All Providers',
    export: 'Export',
    archive: 'Archive',
    unarchive: 'Unarchive',
    viewDetails: 'View Details',
    noSessions: 'No sessions found',
    page: 'Page',
    pageOf: '/',
    prevPage: 'Previous',
    nextPage: 'Next',
    sessionDetails: 'Session Details',
    messages: 'Messages',
    created: 'Created',
    updated: 'Updated',
    source: 'Source',
    provider: 'Provider',
    workDir: 'Working Dir',

    // Sync
    accountSync: 'Account Sync',
    currentConfig: 'Current Config',
    dbProviderDist: 'DB Provider Distribution',
    previewChanges: 'Preview Changes',
    executeSync: 'Execute Sync',
    syncOnly: 'Sync Only',
    oneClickRestart: 'One-Click Sync & Restart',
    codexStatus: 'Codex Process Status',
    running: 'Running',
    notRunning: 'Not Running',
    killCodex: 'Kill Codex',
    startCodex: 'Start Codex',
    startCodexPP: 'Start Codex++',
    useCodexPP: 'Use Codex++',
    changesToApply: 'Changes to Apply',
    noChanges: 'No changes needed',
    syncCompleted: 'Sync completed',
    syncFailed: 'Sync failed',
    confirmAction: 'Are you sure?',
    warningCodexRunning: 'Warning: Codex is running!',

    // Backup
    backupManager: 'Backup Manager',
    createBackup: 'Create Backup',
    incrementalBackup: 'Incremental Backup',
    restore: 'Restore',
    backupSize: 'Size',
    backupTime: 'Time',
    backupType: 'Type',
    fullBackup: 'Full',
    incrementalBackupShort: 'Incr',
    noBackups: 'No backups found',
    backupCreated: 'Backup created',
    restoreCompleted: 'Restore completed',

    // Settings
    settings: 'Settings',
    autoDetectedPaths: 'Auto-Detected Paths',
    reDetect: 'Re-Detect',
    detectResults: 'Detection Results',
    configuration: 'Configuration',
    dbPath: 'DB Path',
    sessionsDir: 'Sessions Dir',
    archivedDir: 'Archived Dir',
    backupDir: 'Backup Dir',
    codexCliPath: 'Codex CLI Path',
    codexPPPath: 'Codex++ Path',
    pageSize: 'Page Size',
    backupInterval: 'Backup Interval (hrs)',
    maxBackups: 'Max Backups',
    largeFileThreshold: 'Large File Threshold (MB)',
    maxMessages: 'Max Msgs (Large Files)',
    autoBackup: 'Auto Backup',
    useCodexPPSetting: 'Use Codex++',
    darkMode: 'Dark Mode',
    saveSettings: 'Save Settings',
    resetDefaults: 'Reset to Defaults',
    language: 'Language',
    english: 'English',
    chinese: '中文',
  },
};

// 当前语言
let currentLang = localStorage.getItem('codex_gui_lang') || 'zh';

function t(key) {
  return (translations[currentLang] && translations[currentLang][key]) || key;
}

function switchLang(lang) {
  currentLang = lang;
  localStorage.setItem('codex_gui_lang', lang);
  location.reload();
}

function applyI18n() {
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    const text = t(key);
    if (text !== key) {
      if (el.tagName === 'INPUT' && el.placeholder) {
        el.placeholder = text;
      } else {
        el.textContent = text;
      }
    }
  });
}

// 页面加载后自动应用
document.addEventListener('DOMContentLoaded', applyI18n);
