export const commandNames = {
  compact: '压缩上下文',
  plan: '制定计划',
  default: '执行模式',
  goal: '持续目标',
  model: '模型设置',
  permissions: '权限设置',
  status: '查看状态',
  help: '指令帮助',
};
export function commandInfo(text = '') {
  const match = text.trim().match(/^\/(\w+)(?:\s+([\s\S]*))?$/);
  if (!match || !Object.hasOwn(commandNames, match[1].toLowerCase())) return null;
  const name = match[1].toLowerCase(),
    argument = (match[2] || '').trim();
  const title =
    name === 'goal'
      ? { '': '查看目标', pause: '暂停目标', resume: '继续目标', clear: '清除目标' }[argument] ||
        (argument.startsWith('edit ') ? '编辑目标' : '设置目标')
      : name === 'plan' && !argument
        ? '计划模式'
        : commandNames[name];
  return { name, argument, title };
}
export function operationState(detail) {
  if (detail?.requests?.length) return { label: '等待回复', text: '等待你的批准或回答' };
  if (!['running', 'starting'].includes(detail?.status)) return null;
  const operation = detail.activeOperation;
  const command = operation?.command;
  if (command === 'compact')
    return { label: '压缩中', text: '/compact · 正在压缩上下文，聊天记录会保留' };
  const goalText = {
    'goal-get': '正在读取当前目标',
    'goal-pause': '正在暂停目标',
    'goal-clear': '正在清除目标',
    'goal-set': '正在推进目标',
    'goal-resume': '正在继续目标',
  }[command];
  if (goalText)
    return {
      label:
        command === 'goal-get'
          ? '查询中'
          : command === 'goal-clear'
            ? '清除中'
            : command === 'goal-pause'
              ? '暂停中'
              : '目标进行中',
      text: `/goal · ${goalText}`,
    };
  if (operation?.collaboration === 'plan')
    return { label: '计划中', text: '/plan · 正在分析需求并制定计划' };
  if (detail.goal?.status === 'active')
    return { label: '目标进行中', text: '/goal · 正在推进当前目标' };
  return {
    label: detail.status === 'starting' ? '启动中' : '处理中',
    text: detail.status === 'starting' ? '正在连接会话…' : 'Codex 正在回复…',
  };
}
export function commandProgress(record) {
  const status = record.execution_status || record.status;
  const labels = {
    starting: '准备中',
    queued: '已排队',
    running: '处理中',
    completed: '已完成',
    failed: '失败',
    interrupted: '已中断',
    cancelled: '已取消',
    detached: '待确认',
  };
  const info = commandInfo(record.input);
  let message = record.message;
  if (status === 'failed') message = record.error || message || '指令未完成，请重试。';
  else if (status === 'queued') message = '等待当前一轮结束后执行。';
  else if (status === 'running' || status === 'starting')
    message = `正在${info?.title || '处理指令'}…`;
  else if (status === 'completed' && !message)
    message =
      info?.name === 'plan'
        ? '本轮计划已完成。'
        : info?.name === 'goal' && ['', 'pause', 'clear'].includes(info.argument)
          ? '目标操作已完成。'
          : '本轮处理已结束。';
  else if (status === 'interrupted') message = '此指令已中断。';
  else if (status === 'detached') message = '连接已重启，请查看会话实际状态。';
  return { status, label: labels[status] || status, message };
}

export const COMMANDS = [
  { name: 'goal', hint: '设置或查看持续执行目标', text: '/goal ' },
  { name: 'compact', hint: '压缩上下文，保留聊天记录', text: '/compact' },
  { name: 'plan', hint: '切换计划模式，可追加需求', text: '/plan' },
  { name: 'default', hint: '切换回执行模式', text: '/default' },
  { name: 'permissions', hint: '选择操作权限', text: '/permissions' },
  { name: 'model', hint: '选择模型与推理强度', text: '/model' },
  { name: 'status', hint: '打开执行现场', text: '/status' },
  { name: 'help', hint: '查看所有支持的指令', text: '/help' },
];

// Suggestions apply only while completing a supported command name.
export function commandCandidates(text = '') {
  if (!/^\/[a-z]*$/i.test(text)) return [];
  return COMMANDS.filter((command) => command.name.startsWith(text.slice(1).toLowerCase()));
}
