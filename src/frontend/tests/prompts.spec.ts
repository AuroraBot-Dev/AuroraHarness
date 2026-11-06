import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { usePromptStore } from '~/stores/promptStore'
import {
  parsePromptCollection,
  promptStorageKey,
  promptVariables,
  renderPrompt,
} from '~/utils/prompts'

const input = {
  name: '审查',
  description: '检查代码',
  content: '检查 {{范围}}，关注 {{ 重点 }}。再次核对 {{范围}}。',
}

function collection(items: unknown[]) {
  return JSON.stringify({ version: 1, items })
}

describe('prompt library', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    const values = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    })
  })

  it('finds unique variables and replaces values without reinterpreting their content', () => {
    expect(promptVariables(input.content)).toEqual(['范围', '重点'])
    expect(renderPrompt(input.content, { 范围: '$& {{重点}}', 重点: '边界' })).toBe(
      '检查 $& {{重点}}，关注 边界。再次核对 $& {{重点}}。',
    )
    expect(renderPrompt('{{范围}}', {})).toBe('{{范围}}')
    expect(renderPrompt('{{toString}} {{__proto__}}', {})).toBe('{{toString}} {{__proto__}}')
  })

  it('persists edits, duplicates and deletions across reloads with stable IDs', () => {
    const store = usePromptStore()
    store.hydrate()
    const item = store.save(null, input)
    const copy = store.duplicate(item.id)
    store.save(item.id, { ...input, name: '新名称' })
    store.remove(copy.id)
    setActivePinia(createPinia())
    const reloaded = usePromptStore()
    reloaded.hydrate()
    expect(reloaded.items.find((entry) => entry.id === item.id)?.name).toBe('新名称')
    expect(reloaded.items.some((entry) => entry.id === copy.id)).toBe(false)
  })

  it('imports additively with new IDs and round trips exported content', () => {
    const store = usePromptStore()
    store.hydrate()
    const original = store.items[0]!
    const exported = store.exportCollection()
    expect(store.importCollection(exported)).toBe(3)
    expect(store.items).toHaveLength(6)
    expect(new Set(store.items.map((item) => item.id)).size).toBe(6)
    expect(store.items.find((item) => item.id === original.id)).toEqual(original)
    expect(parsePromptCollection(store.exportCollection())).toHaveLength(6)
  })

  it('rejects an invalid collection atomically and leaves saved data intact', () => {
    const store = usePromptStore()
    store.hydrate()
    const before = localStorage.getItem(promptStorageKey)
    expect(() => store.importCollection(collection([input, { name: 'broken' }]))).toThrow('字段')
    expect(localStorage.getItem(promptStorageKey)).toBe(before)
    expect(store.items).toHaveLength(3)
    expect(() => parsePromptCollection('{bad')).toThrow('JSON')
    expect(() => parsePromptCollection('{"version":2,"items":[]}')).toThrow('格式')
  })

  it('keeps an intentionally empty library empty after reloading', () => {
    localStorage.setItem(promptStorageKey, collection([]))
    const store = usePromptStore()
    store.hydrate()
    expect(store.items).toEqual([])
    expect(store.hydrated).toBe(true)
  })

  it('preserves corrupt local storage and refuses to overwrite it', () => {
    localStorage.setItem(promptStorageKey, '{broken')
    const store = usePromptStore()
    store.hydrate()
    expect(store.hydrated).toBe(false)
    expect(store.error).toContain('读取失败')
    expect(() => store.save(null, input)).toThrow('恢复')
    expect(localStorage.getItem(promptStorageKey)).toBe('{broken')
  })

  it('does not change memory or disk when storage is full', () => {
    const store = usePromptStore()
    store.hydrate()
    const before = store.exportCollection()
    const spy = vi.spyOn(localStorage, 'setItem').mockImplementation(() => {
      throw new Error('quota')
    })
    expect(() => store.save(null, input)).toThrow('存储')
    expect(store.exportCollection()).toBe(before)
    spy.mockRestore()
  })

  it('enforces the collection capacity before changing existing content', () => {
    const store = usePromptStore()
    store.hydrate()
    store.importCollection(collection(Array.from({ length: 97 }, () => input)))
    const before = store.exportCollection()
    expect(() => store.save(null, input)).toThrow('100')
    expect(store.exportCollection()).toBe(before)
  })
})
