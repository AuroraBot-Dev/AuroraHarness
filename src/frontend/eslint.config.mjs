import { createConfigForNuxt } from '@nuxt/eslint-config'

export default createConfigForNuxt().append({
  rules: {
    '@typescript-eslint/no-explicit-any': 'warn',
  },
})
