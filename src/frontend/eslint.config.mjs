import { createConfigForNuxt } from '@nuxt/eslint-config'

export default createConfigForNuxt().append(
  { ignores: ['coverage/**'] },
  {
    rules: {
      '@typescript-eslint/no-explicit-any': 'warn',
      'vue/html-self-closing': ['warn', {
        html: { void: 'always', normal: 'always', component: 'always' },
        svg: 'always',
        math: 'always',
      }],
    },
  },
  {
    files: ['app/components/MarkdownContent/MarkdownContent.vue'],
    rules: { 'vue/no-v-html': 'off' },
  },
)
