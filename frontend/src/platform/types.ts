export type PlatformKind = 'web' | 'tauri'

export type PlatformCapability =
  | 'fileSelection'
  | 'fileSave'
  | 'externalLinks'
  | 'notifications'
  | 'secureCredentials'
  | 'rememberedLogin'
  | 'localExecution'

export interface PlatformCapabilities {
  platform: PlatformKind
  fileSelection: boolean
  fileSave: boolean
  externalLinks: boolean
  notifications: boolean
  secureCredentials: boolean
  rememberedLogin: boolean
  localExecution: boolean
}

export interface FileSelectionOptions {
  extensions?: string[]
}

export interface PlatformFile {
  name: string
  path?: string
  bytes: Uint8Array
}

export interface SaveFileOptions {
  suggestedName: string
  bytes: Uint8Array
}

export interface SaveFileResult {
  path?: string
}

export interface PlatformNotification {
  title: string
  body?: string
}

export interface PlatformRuntimeConfig {
  apiBaseUrl: string | null
  webUrl: string | null
}

export interface SecureCredentialStore {
  get(key: string): Promise<string | null>
  set(key: string, secret: string): Promise<void>
  delete(key: string): Promise<void>
}

export interface RememberedLoginStore {
  get(): Promise<string | null>
  set(value: string): Promise<void>
  delete(): Promise<void>
}

export interface LocalExecutorStatus {
  running: boolean
  protocolVersion: '1.0'
  capabilityId: 'social-operations'
}

export interface LocalExecutorBridge {
  start(): Promise<LocalExecutorStatus>
  invoke(request: Record<string, unknown>): Promise<Record<string, unknown>>
  status(): Promise<LocalExecutorStatus>
  stop(): Promise<LocalExecutorStatus>
}

export interface PlatformAdapter {
  capabilities(): PlatformCapabilities
  runtimeConfig(): Promise<PlatformRuntimeConfig>
  selectFile(options?: FileSelectionOptions): Promise<PlatformFile | null>
  saveFile(options: SaveFileOptions): Promise<SaveFileResult | null>
  openExternal(url: string): Promise<void>
  notify(notification: PlatformNotification): Promise<boolean>
  credentials: SecureCredentialStore
  rememberedLogin: RememberedLoginStore
  localExecutor: LocalExecutorBridge
}

export type PlatformErrorCode =
  | 'capability_unavailable'
  | 'invalid_input'
  | 'operation_failed'

export class PlatformOperationError extends Error {
  readonly code: PlatformErrorCode
  readonly cause?: unknown

  constructor(code: PlatformErrorCode, message: string, cause?: unknown) {
    super(message)
    this.name = 'PlatformOperationError'
    this.code = code
    this.cause = cause
  }
}

export class PlatformCapabilityError extends PlatformOperationError {
  readonly capability: PlatformCapability

  constructor(capability: PlatformCapability) {
    super('capability_unavailable', `当前平台不支持能力：${capability}`)
    this.name = 'PlatformCapabilityError'
    this.capability = capability
  }
}

export function normalizeExternalUrl(value: string): string {
  let url: URL
  try {
    url = new URL(value)
  } catch (error) {
    throw new PlatformOperationError('invalid_input', '外部链接格式无效', error)
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new PlatformOperationError('invalid_input', '只允许打开 HTTP(S) 外部链接')
  }
  return url.toString()
}

export function operationFailed(message: string, cause: unknown): PlatformOperationError {
  if (cause instanceof PlatformOperationError) return cause
  return new PlatformOperationError('operation_failed', message, cause)
}
