import { invoke } from '@tauri-apps/api/core'
import { open, save } from '@tauri-apps/plugin-dialog'
import { readFile, writeFile } from '@tauri-apps/plugin-fs'
import {
  isPermissionGranted,
  requestPermission,
  sendNotification,
} from '@tauri-apps/plugin-notification'
import { openUrl } from '@tauri-apps/plugin-opener'

import {
  normalizeExternalUrl,
  operationFailed,
  type PlatformAdapter,
} from './types'

function fileName(path: string): string {
  return path.split(/[\\/]/).at(-1) ?? path
}

export function createTauriPlatformAdapter(): PlatformAdapter {
  return {
    capabilities: () => ({
      platform: 'tauri',
      fileSelection: true,
      fileSave: true,
      externalLinks: true,
      notifications: true,
      secureCredentials: true,
    }),
    selectFile: async (options = {}) => {
      try {
        const path = await open({
          multiple: false,
          directory: false,
          ...(options.extensions?.length
            ? { filters: [{ name: '允许的文件', extensions: options.extensions }] }
            : {}),
        })
        if (path === null) return null
        return {
          name: fileName(path),
          path,
          bytes: await readFile(path),
        }
      } catch (error) {
        throw operationFailed('选择文件失败', error)
      }
    },
    saveFile: async ({ suggestedName, bytes }) => {
      try {
        const path = await save({ defaultPath: suggestedName })
        if (path === null) return null
        await writeFile(path, bytes)
        return { path }
      } catch (error) {
        throw operationFailed('保存文件失败', error)
      }
    },
    openExternal: async (value) => {
      try {
        await openUrl(normalizeExternalUrl(value))
      } catch (error) {
        throw operationFailed('打开外部链接失败', error)
      }
    },
    notify: async (notification) => {
      try {
        const permissionGranted = await isPermissionGranted()
          || await requestPermission() === 'granted'
        if (!permissionGranted) return false
        sendNotification(notification)
        return true
      } catch (error) {
        throw operationFailed('发送系统通知失败', error)
      }
    },
    credentials: {
      get: (key) => invoke<string | null>('credential_get', { key }),
      set: (key, secret) => invoke<void>('credential_set', { key, secret }),
      delete: (key) => invoke<void>('credential_delete', { key }),
    },
  }
}
