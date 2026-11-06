<script setup lang="ts">
import { NButton, NCard, NInput, NRadioButton, NRadioGroup } from 'naive-ui'
import type { ThemePreference } from '~/types/agent'
import { accentPresets, isAccentColor } from '~/utils/appearance'

const ui = useUiStore()
const colorText = ref(ui.accent.toUpperCase())
const candidate = computed(() => {
  const value = colorText.value.trim()
  return value.startsWith('#') ? value : `#${value}`
})
const valid = computed(() => isAccentColor(candidate.value))
watch(
  () => ui.accent,
  (value) => {
    colorText.value = value.toUpperCase()
  },
)

function applyColor() {
  if (valid.value) ui.setAccent(candidate.value)
}

function pickColor(event: Event) {
  ui.setAccent((event.target as HTMLInputElement).value)
}
</script>

<template>
  <NCard
    title="外观"
    class="appearance-card"
  >
    <div class="preference-row">
      <div>
        <strong>明暗，随你喜欢</strong>
        <p>跟随系统，或选择熟悉的明亮与深邃。</p>
      </div>
      <NRadioGroup
        :value="ui.theme"
        size="small"
        aria-label="主题模式"
        @update:value="ui.setTheme($event as ThemePreference)"
      >
        <NRadioButton value="system">系统</NRadioButton>
        <NRadioButton value="light">浅色</NRadioButton>
        <NRadioButton value="dark">深色</NRadioButton>
      </NRadioGroup>
    </div>
    <div class="preference-row material-row">
      <div>
        <strong>表面材质</strong>
        <p>在磨砂透光与清晰纯色之间切换。</p>
      </div>
      <NRadioGroup
        :value="ui.material"
        size="small"
        aria-label="表面材质"
        @update:value="ui.setMaterial($event as 'glass' | 'solid')"
      >
        <NRadioButton value="glass">磨砂玻璃</NRadioButton>
        <NRadioButton value="solid">纯色表面</NRadioButton>
      </NRadioGroup>
    </div>
    <div class="preference-row accent-row">
      <div>
        <strong>你的强调色</strong>
        <p>从预设开始，或用十六进制颜色定义自己的风格。</p>
      </div>
      <div class="accent-controls">
        <div
          class="color-presets"
          role="group"
          aria-label="预设强调色"
        >
          <button
            v-for="preset in accentPresets"
            :key="preset.color"
            class="color-swatch"
            :style="{ '--swatch': preset.color }"
            :aria-label="preset.name"
            :aria-pressed="ui.accent === preset.color"
            :title="preset.name"
            @click="ui.setAccent(preset.color)"
          >
            <span />
          </button>
          <label
            class="custom-color"
            title="自选颜色"
          >
            <input
              type="color"
              aria-label="自选强调色"
              :value="ui.accent"
              @input="pickColor"
            />
            <span>＋</span>
          </label>
        </div>
        <div class="hex-input-row">
          <NInput
            v-model:value="colorText"
            :input-props="{ 'aria-label': '强调色十六进制编码' }"
            placeholder="#D53543"
            :status="valid ? undefined : 'error'"
            :maxlength="7"
            spellcheck="false"
            @keydown.enter.prevent="applyColor"
          />
          <NButton
            secondary
            :disabled="!valid"
            @click="applyColor"
          >
            应用
          </NButton>
        </div>
        <p
          v-if="!valid"
          class="color-error"
          role="status"
        >
          请输入 6 位十六进制颜色，例如 #D53543。
        </p>
      </div>
    </div>
    <p class="appearance-note">苹方优先 · 外观保存在此设备 · 强调色自动适配明暗背景</p>
  </NCard>
</template>

<style scoped>
.preference-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 8px 0 22px;
}

.preference-row strong {
  font-size: 13px;
  font-weight: 550;
}

.preference-row p {
  margin: 6px 0 0;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.8;
}

.accent-row,
.material-row {
  align-items: flex-start;
  padding-top: 22px;
  border-top: 1px solid var(--border);
}

.accent-controls {
  width: 220px;
  max-width: 100%;
  flex: 0 0 auto;
}

.color-presets {
  display: flex;
  gap: 10px;
  margin-bottom: 14px;
}

.color-swatch,
.custom-color {
  position: relative;
  display: grid;
  width: 32px;
  height: 32px;
  place-items: center;
  padding: 3px;
  border: 1px solid transparent;
  border-radius: 50%;
  background: transparent;
  cursor: pointer;
}

.color-swatch span {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--swatch);
  transition: transform 150ms ease;
}

.color-swatch:hover span {
  transform: scale(1.12);
}

.color-swatch[aria-pressed='true'] {
  border-color: var(--text-muted);
}

.custom-color {
  border: 1px dashed var(--text-muted);
  color: var(--text-muted);
}

.custom-color input {
  position: absolute;
  width: 100%;
  height: 100%;
  opacity: 0;
  cursor: pointer;
}

.custom-color:focus-within {
  outline: 3px solid var(--accent);
  outline-offset: 3px;
}

.custom-color span {
  pointer-events: none;
}

.hex-input-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.hex-input-row :deep(input) {
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.03em;
}

.preference-row .color-error {
  color: var(--danger);
  font-size: 11px;
}

.appearance-note {
  margin: 2px 0 0;
  color: var(--text-muted);
  font-size: 10px;
  line-height: 1.8;
}

@media (max-width: 680px) {
  .preference-row {
    align-items: flex-start;
    flex-direction: column;
    gap: 16px;
  }
}
</style>
