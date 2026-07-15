import {
  normalizeExternalUrl,
  PlatformCapabilityError,
  type FileSelectionOptions,
  type PlatformAdapter,
  type PlatformFile,
  type SaveFileOptions,
} from './types'

function selectFile(options: FileSelectionOptions = {}): Promise<PlatformFile | null> {
  return new Promise((resolve, reject) => {
    const input = document.createElement('input')
    input.type = 'file'
    if (options.extensions?.length) {
      input.accept = options.extensions.map((extension) => `.${extension}`).join(',')
    }

    input.addEventListener('cancel', () => resolve(null), { once: true })
    input.addEventListener('change', () => {
      const file = input.files?.item(0)
      if (file === null || file === undefined) {
        resolve(null)
        return
      }
      void file.arrayBuffer()
        .then((buffer) => resolve({
          name: file.name,
          bytes: new Uint8Array(buffer),
        }))
        .catch(reject)
    }, { once: true })
    input.click()
  })
}

async function saveFile({ suggestedName, bytes }: SaveFileOptions) {
  const blob = new Blob([Uint8Array.from(bytes).buffer])
  const objectUrl = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = suggestedName
  link.rel = 'noopener'
  try {
    link.click()
    return {}
  } finally {
    URL.revokeObjectURL(objectUrl)
  }
}

const unsupportedCredentials = {
  get: async () => {
    throw new PlatformCapabilityError('secureCredentials')
  },
  set: async () => {
    throw new PlatformCapabilityError('secureCredentials')
  },
  delete: async () => {
    throw new PlatformCapabilityError('secureCredentials')
  },
}

export function createWebPlatformAdapter(): PlatformAdapter {
  return {
    capabilities: () => ({
      platform: 'web',
      fileSelection: typeof document !== 'undefined',
      fileSave: typeof document !== 'undefined',
      externalLinks: typeof window !== 'undefined',
      notifications: typeof Notification !== 'undefined',
      secureCredentials: false,
      rememberedLogin: false,
      localExecution: false,
      socialOperations: false,
    }),
    runtimeConfig: async () => ({ apiBaseUrl: null, webUrl: null }),
    selectFile,
    saveFile,
    openExternal: async (value) => {
      const url = normalizeExternalUrl(value)
      window.open(url, '_blank', 'noopener,noreferrer')
    },
    notify: async ({ title, body }) => {
      if (typeof Notification === 'undefined') {
        throw new PlatformCapabilityError('notifications')
      }
      const permission = Notification.permission === 'default'
        ? await Notification.requestPermission()
        : Notification.permission
      if (permission !== 'granted') return false
      new Notification(title, { body })
      return true
    },
    credentials: unsupportedCredentials,
    rememberedLogin: {
      get: async () => { throw new PlatformCapabilityError('rememberedLogin') },
      set: async () => { throw new PlatformCapabilityError('rememberedLogin') },
      delete: async () => { throw new PlatformCapabilityError('rememberedLogin') },
    },
    localExecutor: {
      start: async () => { throw new PlatformCapabilityError('localExecution') },
      invoke: async () => { throw new PlatformCapabilityError('localExecution') },
      status: async () => { throw new PlatformCapabilityError('localExecution') },
      stop: async () => { throw new PlatformCapabilityError('localExecution') },
    },
    socialOperations: {
      installSidecar: async () => { throw new PlatformCapabilityError('socialOperations') },
      downloadSidecar: async () => { throw new PlatformCapabilityError('socialOperations') },
      prepareAccount: async () => { throw new PlatformCapabilityError('socialOperations') },
      signalLogin: async () => { throw new PlatformCapabilityError('socialOperations') },
      storeCookies: async () => { throw new PlatformCapabilityError('socialOperations') },
      hasCookies: async () => { throw new PlatformCapabilityError('socialOperations') },
      startAccount: async () => { throw new PlatformCapabilityError('socialOperations') },
      invokeAccount: async () => { throw new PlatformCapabilityError('socialOperations') },
      logoutAccount: async () => { throw new PlatformCapabilityError('socialOperations') },
      emergencyStop: async () => { throw new PlatformCapabilityError('socialOperations') },
      takeSafeDiagnostics: async () => { throw new PlatformCapabilityError('socialOperations') },
    },
  }
}
