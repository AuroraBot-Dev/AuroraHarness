<script setup lang="ts">
import MarkdownIt from 'markdown-it'

const props = defineProps<{ content: string }>()
const html = ref('')
const markdown = new MarkdownIt({ html: false, linkify: true, breaks: true })

watch(() => props.content, async (content) => {
  const rendered = markdown.render(content)
  if (import.meta.client) {
    const { default: DOMPurify } = await import('dompurify')
    html.value = DOMPurify.sanitize(rendered, { USE_PROFILES: { html: true } })
  } else {
    html.value = rendered
  }
}, { immediate: true })
</script>

<template>
  <!-- 内容先由 markdown-it 禁用原始 HTML，再经 DOMPurify 清理。 -->
  <!-- eslint-disable-next-line vue/no-v-html -->
  <div class="markdown-content" v-html="html" />
</template>

<style scoped>
.markdown-content { color: var(--text); font-size: 14px; line-height: 1.75; overflow-wrap: anywhere; }
.markdown-content :deep(p) { margin: 0 0 12px; }
.markdown-content :deep(p:last-child) { margin-bottom: 0; }
.markdown-content :deep(ul), .markdown-content :deep(ol) { margin: 8px 0 14px; padding-left: 22px; }
.markdown-content :deep(li) { margin: 5px 0; }
.markdown-content :deep(code) { padding: 2px 6px; border: 1px solid var(--border); border-radius: 5px; background: var(--surface-muted); font-family: "Cascadia Code", "SFMono-Regular", Consolas, monospace; font-size: .88em; }
.markdown-content :deep(pre) { overflow: auto; margin: 12px 0; padding: 14px; border: 1px solid var(--border); border-radius: 9px; background: var(--surface-muted); }
.markdown-content :deep(pre code) { padding: 0; border: 0; background: transparent; }
.markdown-content :deep(a) { color: #2563eb; text-decoration: underline; text-underline-offset: 3px; }
.markdown-content :deep(blockquote) { margin: 12px 0; padding-left: 12px; border-left: 3px solid var(--border); color: var(--text-muted); }
</style>
