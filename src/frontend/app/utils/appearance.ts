export const accentPresets = [
  { name: '樱桃红', color: '#d53543' },
  { name: '海洋蓝', color: '#0066cc' },
  { name: '鸢尾紫', color: '#7955c7' },
  { name: '松叶绿', color: '#278268' },
]

export function isAccentColor(value: string): boolean {
  return /^#[\da-f]{6}$/i.test(value)
}

function channels(hex: string): number[] {
  return [1, 3, 5].map((offset) => Number.parseInt(hex.slice(offset, offset + 2), 16))
}

function mix(color: string, target: number, ratio: number): string {
  return `#${channels(color)
    .map((value) =>
      Math.round(value + (target - value) * ratio)
        .toString(16)
        .padStart(2, '0'),
    )
    .join('')}`
}

function luminance(color: string): number {
  const linear = channels(color).map((channel) => {
    const value = channel / 255
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  })
  return linear[0]! * 0.2126 + linear[1]! * 0.7152 + linear[2]! * 0.0722
}

export function contrastRatio(first: string, second: string): number {
  const values = [luminance(first), luminance(second)].sort((a, b) => b - a)
  return (values[0]! + 0.05) / (values[1]! + 0.05)
}

export function accentPalette(color: string, dark: boolean) {
  let primary = isAccentColor(color) ? color : accentPresets[0]!.color
  const background = dark ? '#222327' : '#f5f5f7'
  for (let attempt = 0; attempt < 30 && contrastRatio(primary, background) < 4.5; attempt++) {
    primary = mix(primary, dark ? 255 : 0, 0.08)
  }
  return {
    primary,
    hover: mix(primary, dark ? 255 : 0, 0.12),
    pressed: mix(primary, dark ? 255 : 0, 0.2),
    soft: mix(primary, dark ? 25 : 255, dark ? 0.82 : 0.91),
    foreground: contrastRatio(primary, '#ffffff') >= 4.5 ? '#ffffff' : '#17181b',
  }
}
