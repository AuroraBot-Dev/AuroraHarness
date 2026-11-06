import { defineStore } from 'pinia'
import type { PromptCollection, PromptInput, PromptTemplate } from '~/types/prompt'
import {
  parsePromptCollection,
  promptStorageKey,
  starterPrompts,
  validatePrompt,
} from '~/utils/prompts'

function createTemplate(input: PromptInput): PromptTemplate {
  return { ...validatePrompt(input), id: crypto.randomUUID(), updatedAt: new Date().toISOString() }
}

export const usePromptStore = defineStore('prompts', {
  state: () => ({
    items: [] as PromptTemplate[],
    hydrated: false,
    error: '',
    draft: '',
  }),
  actions: {
    hydrate() {
      if (this.hydrated) return
      try {
        const saved = localStorage.getItem(promptStorageKey)
        if (saved) {
          const inputs = parsePromptCollection(saved)
          const collection = JSON.parse(saved) as PromptCollection
          const ids = new Set<string>()
          this.items = inputs.map((input, index) => {
            const original = collection.items[index]!
            const id =
              typeof original.id === 'string' && original.id && !ids.has(original.id)
                ? original.id
                : crypto.randomUUID()
            ids.add(id)
            return {
              ...input,
              id,
              updatedAt: typeof original.updatedAt === 'string' ? original.updatedAt : '',
            }
          })
        } else {
          this.commit(starterPrompts.map(createTemplate))
        }
        this.hydrated = true
        this.error = ''
      } catch (error) {
        this.error = `提示词库读取失败：${error instanceof Error ? error.message : String(error)}`
      }
    },
    commit(items: PromptTemplate[]) {
      const collection: PromptCollection = { version: 1, items }
      const serialized = JSON.stringify(collection, null, 2)
      if (new TextEncoder().encode(serialized).length > 2000000) {
        throw new Error('提示词库总大小不能超过 2 MB，请先导出备份并精简内容')
      }
      try {
        localStorage.setItem(promptStorageKey, serialized)
      } catch {
        throw new Error('无法保存提示词，请检查本地存储空间或浏览器存储权限')
      }
      this.items = items
      this.error = ''
    },
    save(id: string | null, input: PromptInput) {
      if (!this.hydrated) throw new Error('请先恢复提示词库，再保存修改')
      if (!id && this.items.length >= 100)
        throw new Error('提示词库最多保存 100 条，请先导出备份或删除不再使用的提示词')
      const existing = id ? this.items.find((item) => item.id === id) : undefined
      if (id && !existing) throw new Error('提示词不存在')
      const item = existing
        ? { ...existing, ...validatePrompt(input), updatedAt: new Date().toISOString() }
        : createTemplate(input)
      this.commit(
        existing
          ? this.items.map((entry) => (entry.id === id ? item : entry))
          : [...this.items, item],
      )
      return item
    },
    duplicate(id: string) {
      const item = this.items.find((entry) => entry.id === id)
      if (!item) throw new Error('提示词不存在')
      return this.save(null, { ...item, name: `${item.name.slice(0, 95)} 副本` })
    },
    remove(id: string) {
      this.commit(this.items.filter((item) => item.id !== id))
    },
    importCollection(text: string) {
      if (!this.hydrated) throw new Error('请先恢复提示词库，再导入文件')
      const inputs = parsePromptCollection(text)
      if (this.items.length + inputs.length > 100)
        throw new Error('导入后将超过 100 条上限，请先整理提示词库')
      this.commit([...this.items, ...inputs.map(createTemplate)])
      return inputs.length
    },
    exportCollection() {
      const collection: PromptCollection = { version: 1, items: this.items }
      return JSON.stringify(collection, null, 2)
    },
  },
})
