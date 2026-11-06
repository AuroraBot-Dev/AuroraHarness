import type { PromptCollection, PromptInput } from '~/types/prompt'

export const promptStorageKey = 'aurora:prompts:v1'
export const maxPromptLength = 50000
const variablePattern = /\{\{\s*([^{}\n]+?)\s*\}\}/g

export function promptVariables(content: string): string[] {
  return [...new Set([...content.matchAll(variablePattern)].map((match) => match[1]!.trim()))]
}

export function renderPrompt(content: string, values: Record<string, string>): string {
  return content.replace(variablePattern, (token, name: string) => {
    const key = name.trim()
    const value = values[key]
    return Object.hasOwn(values, key) && typeof value === 'string' && value.trim()
      ? value.trim()
      : token
  })
}

export function validatePrompt(input: PromptInput): PromptInput {
  const name = input.name.trim()
  if (!name || name.length > 100) throw new Error('名称需为 1–100 个字符')
  if (!input.content.trim() || input.content.length > maxPromptLength) {
    throw new Error('提示词正文需为 1–50,000 个字符')
  }
  if (input.description.length > 500) throw new Error('简介不能超过 500 个字符')
  return { name, description: input.description.trim(), content: input.content }
}

export function parsePromptCollection(text: string): PromptInput[] {
  if (new TextEncoder().encode(text).length > 2000000) throw new Error('导入文件不能超过 2 MB')
  let data: Partial<PromptCollection>
  try {
    data = JSON.parse(text)
  } catch {
    throw new Error('无法读取 JSON 文件，请选择导出的提示词文件')
  }
  if (!data || data.version !== 1 || !Array.isArray(data.items) || data.items.length > 100) {
    throw new Error('文件格式不正确，需要 version: 1 和 items 数组，单次最多导入 100 条')
  }
  return data.items.map((item) => {
    if (
      !item ||
      typeof item.name !== 'string' ||
      typeof item.description !== 'string' ||
      typeof item.content !== 'string'
    ) {
      throw new Error('每条提示词都需要 name、description 和 content 文本字段')
    }
    return validatePrompt(item)
  })
}

export const starterPrompts: PromptInput[] = [
  {
    name: '读懂一个项目',
    description: '从目录、依赖和数据流出发，建立对项目的理解。',
    content:
      '请分析当前工作区的项目架构，重点关注 {{关注方向}}。\n\n先阅读目录和入口文件，再梳理模块职责、依赖关系和关键数据流。所有结论引用实际文件路径，区分已验证事实与推测。最后给出按优先级排列的改进建议。',
  },
  {
    name: '认真做一次审查',
    description: '聚焦真实缺陷，给出可复现、可执行的修改建议。',
    content:
      '请审查 {{审查范围}}，重点关注正确性、边界情况和可读性。\n\n先理解上下文，再报告有证据的问题；每条包含文件位置、触发条件、影响和修改建议。按严重程度排序。若未发现问题，请直接说明，并列出验证范围。',
  },
  {
    name: '把想法变成计划',
    description: '明确目标和约束，把模糊的想法拆成可交付的步骤。',
    content:
      '我想实现 {{目标}}。\n\n请结合当前项目，先确认已有能力与约束，再给出最小可交付方案。按顺序列出实施步骤、涉及模块和验收方式。优先复用已有实现，明确需要我补充的信息。',
  },
]
