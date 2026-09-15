// Tauri IPC 封装：统一 invoke 命令与事件订阅入口；非 Tauri 环境（浏览器开发）下降级。
import { invoke } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'

const inTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window

export function useIpc() {
  /** 调用 Rust 命令（非 Tauri 环境返回 null）。 */
  async function call<T>(cmd: string, args?: Record<string, unknown>): Promise<T | null> {
    if (!inTauri) return null
    try {
      return await invoke<T>(cmd, args)
    } catch (e) {
      console.error(`[ipc] ${cmd} 失败`, e)
      return null
    }
  }

  /** 订阅 Tauri 事件（非 Tauri 环境返回空 unlisten）。 */
  async function on<T>(event: string, handler: (payload: T) => void): Promise<UnlistenFn> {
    if (!inTauri) return () => {}
    return await listen<T>(event, (e) => handler(e.payload))
  }

  return { call, on, inTauri }
}
