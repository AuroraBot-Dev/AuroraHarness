import { describe, expect, it } from 'vitest'
import { accentPalette, accentPresets, contrastRatio, isAccentColor } from '~/utils/appearance'

describe('appearance colors', () => {
  it('accepts only complete RGB hex colors', () => {
    expect(isAccentColor('#D53543')).toBe(true)
    for (const value of ['red', '#fff', '#12345g', '#12345678', ''])
      expect(isAccentColor(value)).toBe(false)
  })

  it('keeps accent text and primary button text readable in both modes', () => {
    for (const color of [
      ...accentPresets.map((preset) => preset.color),
      '#ffffff',
      '#000000',
      '#ffff00',
      '#00ffff',
    ]) {
      for (const dark of [false, true]) {
        const palette = accentPalette(color, dark)
        expect(contrastRatio(palette.primary, dark ? '#222327' : '#f5f5f7')).toBeGreaterThanOrEqual(
          4.5,
        )
        expect(contrastRatio(palette.primary, palette.foreground)).toBeGreaterThanOrEqual(4.5)
        expect(isAccentColor(palette.soft)).toBe(true)
      }
    }
  })

  it('falls back to the default accent for invalid stored colors', () => {
    expect(accentPalette('invalid', false)).toEqual(accentPalette(accentPresets[0]!.color, false))
  })
})
