import {
  Alert,
  Button,
  Card,
  Descriptions,
  Divider,
  Flex,
  Form,
  Input,
  Select,
  Space,
  Tag,
  Typography,
} from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { z } from 'zod'

import {
  getPlatformAdapter,
  type JsonValue,
  type PlatformAdapter,
  type PlatformFile,
  type SocialAccountSnapshot,
  type SocialPlatform,
  type SocialSidecarManifest,
} from '../../../platform'
import {
  listSocialDevices,
  registerSocialDevice,
  type RegisterSocialDeviceInput,
  type SocialDevice,
  type SocialDevicePlatform,
} from '../api/device-accounts'
import { createSocialOperationsRuntime } from '../runtime'
import './social-operations.css'

const uuidSchema = z.uuid()
const manifestSchema = z.object({
  version: z.string().min(1).max(128),
  platform: z.string().min(1).max(64),
  arch: z.string().min(1).max(64),
  sha256: z.string().regex(/^[0-9a-f]{64}$/),
  package_size: z.number().int().positive(),
}).strict()

const accountStatusLabels: Record<SocialAccountSnapshot['state'], string> = {
  logged_out: '已注销',
  awaiting_scan: '等待扫码',
  awaiting_confirmation: '等待确认',
  healthy: '健康',
  human_handoff: '等待人工接管',
}

const deviceStatusLabels: Record<SocialDevice['status'], string> = {
  online: '在线',
  offline: '离线',
  emergency_stopped: '已紧急停止',
}

const platformOptions: { label: string; value: SocialPlatform }[] = [
  { label: '抖音', value: 'douyin' },
  { label: '小红书', value: 'xiaohongshu' },
  { label: '快手', value: 'kuaishou' },
  { label: '视频号', value: 'wechat_channels' },
  { label: '微信', value: 'wechat' },
]

interface SocialOperationsPageProps {
  workspaceId: string
  platform?: PlatformAdapter
}

type Notice = { type: 'error' | 'success' | 'warning'; message: string }

export function SocialOperationsPage({
  workspaceId,
  platform = getPlatformAdapter(),
}: SocialOperationsPageProps) {
  const supported = platform.capabilities().socialOperations
  const runtime = useMemo(
    () => supported ? createSocialOperationsRuntime(platform) : null,
    [platform, supported],
  )
  const [notice, setNotice] = useState<Notice>()
  const [busyAction, setBusyAction] = useState<string>()
  const [devices, setDevices] = useState<SocialDevice[]>([])
  const [deviceId, setDeviceId] = useState('')
  const [deviceName, setDeviceName] = useState('')
  const [devicePlatform, setDevicePlatform] = useState<SocialDevicePlatform>('macos')
  const [appVersion, setAppVersion] = useState('0.1.0')
  const [executorVersion, setExecutorVersion] = useState('1.0.0')
  const [accountId, setAccountId] = useState('')
  const [socialPlatform, setSocialPlatform] = useState<SocialPlatform>('douyin')
  const [accountSnapshot, setAccountSnapshot] = useState<SocialAccountSnapshot>()
  const [executorRunning, setExecutorRunning] = useState(false)
  const [cookieData, setCookieData] = useState('')
  const [hasCookies, setHasCookies] = useState<boolean>()
  const [diagnostics, setDiagnostics] = useState<string[]>([])
  const [manifestJson, setManifestJson] = useState('')
  const [signatureBase64, setSignatureBase64] = useState('')
  const [downloadUrl, setDownloadUrl] = useState('')
  const [localPackage, setLocalPackage] = useState<PlatformFile | null>(null)

  useEffect(() => {
    if (!supported) return
    let active = true
    void listSocialDevices(workspaceId)
      .then((items) => {
        if (active) setDevices(items)
      })
      .catch(() => {
        if (active) {
          setNotice({
            type: 'warning',
            message: '设备服务尚未接入当前 Core 宿主，本地账号安全操作仍可使用。',
          })
        }
      })
    return () => { active = false }
  }, [supported, workspaceId])

  const perform = async <Result,>(
    action: string,
    operation: () => Promise<Result>,
    onSuccess?: (result: Result) => void,
    successMessage?: string,
  ) => {
    if (busyAction !== undefined) return
    setBusyAction(action)
    setNotice(undefined)
    try {
      const result = await operation()
      onSuccess?.(result)
      if (successMessage !== undefined) {
        setNotice({ type: 'success', message: successMessage })
      }
    } catch {
      setNotice({
        type: 'error',
        message: '账号操作失败，请检查执行器、登录态和网络后重试。',
      })
    } finally {
      setBusyAction(undefined)
    }
  }

  const requireRuntime = () => {
    if (runtime === null) throw new Error('social operations unavailable')
    return runtime
  }

  const requireAccount = () => {
    const parsed = uuidSchema.parse(accountId)
    return requireRuntime().account(socialPlatform, parsed)
  }

  const parseInstallerInput = (): {
    manifest: SocialSidecarManifest
    signature: Uint8Array
  } => ({
    manifest: manifestSchema.parse(JSON.parse(manifestJson)),
    signature: Uint8Array.from(atob(signatureBase64), (character) => character.charCodeAt(0)),
  })

  const registerDevice = () => perform(
    'register-device',
    async () => {
      const input: RegisterSocialDeviceInput = {
        device_id: uuidSchema.parse(deviceId),
        display_name: deviceName.trim(),
        platform: devicePlatform,
        app_version: appVersion.trim(),
        executor_version: executorVersion.trim(),
      }
      if (!input.display_name || !input.app_version || !input.executor_version) {
        throw new Error('invalid device input')
      }
      return registerSocialDevice(workspaceId, input)
    },
    (registered) => setDevices((current) => [
      ...current.filter((item) => item.device_id !== registered.device_id),
      registered,
    ]),
    '本机设备已注册。',
  )

  const accountAction = (
    action: string,
    operation: () => Promise<SocialAccountSnapshot>,
    successMessage: string,
  ) => perform(action, operation, setAccountSnapshot, successMessage)

  if (!supported) {
    return (
      <section>
        <Typography.Title level={2}>设备与平台账号中心</Typography.Title>
        <Alert
          type="info"
          showIcon
          title="当前 Web 环境不支持本地执行设备"
          description="请在 Tauri 桌面客户端中管理 Sidecar、平台账号和紧急停止；Web 端不会伪造本地能力成功。"
        />
      </section>
    )
  }

  return (
    <section className="social-operations-page">
      <Typography.Title level={2}>设备与平台账号中心</Typography.Title>
      <Typography.Paragraph type="secondary">
        管理已授权本机执行器和平台账号。验证码、风控和登录失效只会转人工接管，不会尝试绕过。
      </Typography.Paragraph>
      {notice && <Alert type={notice.type} showIcon title={notice.message} />}

      <Card title="设备中心">
        <Form layout="vertical">
          <Flex gap={16} wrap>
            <Form.Item label="设备 ID" required>
              <Input aria-label="设备 ID" value={deviceId} onChange={(event) => setDeviceId(event.target.value)} />
            </Form.Item>
            <Form.Item label="设备名称" required>
              <Input aria-label="设备名称" value={deviceName} onChange={(event) => setDeviceName(event.target.value)} />
            </Form.Item>
            <Form.Item label="设备平台">
              <Select
                value={devicePlatform}
                options={[
                  { label: 'macOS', value: 'macos' },
                  { label: 'Windows', value: 'windows' },
                  { label: 'Linux', value: 'linux' },
                ]}
                onChange={setDevicePlatform}
              />
            </Form.Item>
            <Form.Item label="App 版本">
              <Input value={appVersion} onChange={(event) => setAppVersion(event.target.value)} />
            </Form.Item>
            <Form.Item label="执行器版本">
              <Input
                value={executorVersion}
                onChange={(event) => setExecutorVersion(event.target.value)}
              />
            </Form.Item>
          </Flex>
          <Button
            type="primary"
            loading={busyAction === 'register-device'}
            onClick={() => void registerDevice()}
          >
            注册本机设备
          </Button>
        </Form>
        {devices.length === 0 ? (
          <Typography.Paragraph className="social-device-list" type="secondary">
            尚无已注册设备
          </Typography.Paragraph>
        ) : (
          <ul className="social-device-list">
            {devices.map((device) => (
              <li key={device.device_id}>
                <span>
                  <Typography.Text strong>{device.display_name}</Typography.Text>
                  <Typography.Text type="secondary">
                    {` ${device.platform} · App ${device.app_version} · 执行器 ${device.executor_version}`}
                  </Typography.Text>
                </span>
                <Tag color={device.status === 'online' ? 'success' : 'warning'}>
                  {deviceStatusLabels[device.status]}
                </Tag>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="执行器安装" role="region" aria-label="执行器安装">
        <Alert
          type="warning"
          showIcon
          title="仅安装经发布链签名的 Sidecar"
          description="Manifest 会校验版本、平台、架构、SHA-256 与防降级信息。"
        />
        <Form layout="vertical">
          <Form.Item label="Manifest JSON" required>
            <Input.TextArea aria-label="Manifest JSON" rows={5} value={manifestJson} onChange={(event) => setManifestJson(event.target.value)} />
          </Form.Item>
          <Form.Item label="签名 Base64" required>
            <Input.TextArea aria-label="签名 Base64" rows={2} value={signatureBase64} onChange={(event) => setSignatureBase64(event.target.value)} />
          </Form.Item>
          <Form.Item label="安全下载地址">
            <Input aria-label="安全下载地址" value={downloadUrl} onChange={(event) => setDownloadUrl(event.target.value)} />
          </Form.Item>
          <Space wrap>
            <Button
              loading={busyAction === 'download-sidecar'}
              onClick={() => void perform(
                'download-sidecar',
                () => {
                  const { manifest, signature } = parseInstallerInput()
                  return requireRuntime().downloadSidecar({
                    downloadUrl: new URL(downloadUrl).toString(),
                    manifest,
                    signature,
                  })
                },
                undefined,
                '执行器已下载并安装。',
              )}
            >
              下载并安装
            </Button>
            <Button
              onClick={() => void perform(
                'select-sidecar',
                () => platform.selectFile({ extensions: ['zip', 'tar', 'gz', 'bin'] }),
                setLocalPackage,
              )}
            >
              选择本地安装包
            </Button>
            <Button
              disabled={localPackage === null}
              loading={busyAction === 'install-sidecar'}
              onClick={() => void perform(
                'install-sidecar',
                () => {
                  if (localPackage === null) throw new Error('package required')
                  const { manifest, signature } = parseInstallerInput()
                  return requireRuntime().installSidecar({
                    manifest,
                    package: localPackage.bytes,
                    signature,
                  })
                },
                undefined,
                '本地执行器包已验签并安装。',
              )}
            >
              验证并安装本地包
            </Button>
            {localPackage && <Typography.Text>{localPackage.name}</Typography.Text>}
          </Space>
        </Form>
      </Card>

      <Card title="平台账号中心">
        <Form layout="vertical">
          <Flex gap={16} wrap>
            <Form.Item label="运营平台">
              <Select value={socialPlatform} options={platformOptions} onChange={setSocialPlatform} />
            </Form.Item>
            <Form.Item label="平台账号 ID" required>
              <Input aria-label="平台账号 ID" value={accountId} onChange={(event) => setAccountId(event.target.value)} />
            </Form.Item>
          </Flex>
          <Space wrap>
            <Button onClick={() => void accountAction(
              'prepare-account',
              () => requireAccount().prepare(),
              '账号私有目录已准备。',
            )}>准备账号环境</Button>
            <Button onClick={() => void accountAction(
              'begin-qr',
              () => requireAccount().signalLogin('begin_qr'),
              '已进入扫码等待。',
            )} disabled={accountSnapshot?.state !== 'logged_out'}>开始扫码</Button>
            <Button onClick={() => void accountAction(
              'qr-scanned',
              () => requireAccount().signalLogin('qr_scanned'),
              '已识别扫码，等待平台确认登录。',
            )} disabled={accountSnapshot?.state !== 'awaiting_scan'}>确认已完成扫码</Button>
            <Button onClick={() => void accountAction(
              'authenticated',
              () => requireAccount().signalLogin('authenticated'),
              '已记录受控登录结果。',
            )} disabled={accountSnapshot?.state !== 'awaiting_confirmation'}>确认已登录</Button>
            <Button danger onClick={() => void accountAction(
              'captcha',
              () => requireAccount().signalLogin('captcha_required'),
              '验证码已转人工接管。',
            )}>上报验证码</Button>
            <Button danger onClick={() => void accountAction(
              'risk-control',
              () => requireAccount().signalLogin('risk_control'),
              '风控已触发熔断和人工接管。',
            )}>上报风控</Button>
          </Space>
        </Form>

        {accountSnapshot && (
          <Descriptions className="social-account-status" bordered size="small" column={3}>
            <Descriptions.Item label="登录状态">
              {accountStatusLabels[accountSnapshot.state]}
            </Descriptions.Item>
            <Descriptions.Item label="熔断">
              {accountSnapshot.circuit_open ? '已打开' : '已关闭'}
            </Descriptions.Item>
            <Descriptions.Item label="Session 修订">
              {accountSnapshot.session_revision}
            </Descriptions.Item>
          </Descriptions>
        )}

        <Divider />
        <Form layout="vertical">
          <Form.Item label="Cookie 数据">
            <Input.TextArea
              aria-label="Cookie 数据"
              rows={3}
              value={cookieData}
              onChange={(event) => setCookieData(event.target.value)}
              placeholder="仅写入 App 私有加密存储，不进入 Git 或日志"
            />
          </Form.Item>
          <Space wrap>
            <Button onClick={() => void perform(
              'store-cookies',
              () => requireAccount().storeCookies(new TextEncoder().encode(cookieData)),
              undefined,
              'Cookie 已加密保存。',
            )}>加密保存 Cookie</Button>
            <Button onClick={() => void perform(
              'has-cookies',
              () => requireAccount().hasCookies(),
              setHasCookies,
            )}>检查 Cookie</Button>
            {hasCookies !== undefined && (
              <Tag color={hasCookies ? 'success' : 'default'}>
                {hasCookies ? '已保存 Cookie' : '未保存 Cookie'}
              </Tag>
            )}
          </Space>
        </Form>

        <Divider />
        <Space wrap>
          <Button type="primary" onClick={() => void perform(
            'start-account',
            () => requireAccount().start(),
            (status) => setExecutorRunning(status.running),
            '本地执行器已启动。',
          )}>启动本地执行器</Button>
          <Button onClick={() => void perform(
            'health-check',
            () => requireAccount().invoke(createHealthCheckRequest(
              workspaceId,
              uuidSchema.parse(deviceId),
            )),
            undefined,
            '无副作用健康检查已接受。',
          )}>执行无副作用健康检查</Button>
          <Button danger onClick={() => void perform(
            'emergency-stop',
            () => requireAccount().emergencyStop(),
            () => setExecutorRunning(false),
            '紧急停止已生效。',
          )}>紧急停止</Button>
          <Button onClick={() => void perform(
            'logout',
            () => requireAccount().logout(),
            () => {
              setExecutorRunning(false)
              setAccountSnapshot({ state: 'logged_out', circuit_open: true, session_revision: 0 })
            },
            '账号已注销并清理本地登录材料。',
          )}>注销账号</Button>
          <Tag color={executorRunning ? 'success' : 'default'}>
            {executorRunning ? '执行器运行中' : '执行器未运行'}
          </Tag>
        </Space>

        <Divider />
        <Button onClick={() => void perform(
          'diagnostics',
          () => requireRuntime().takeSafeDiagnostics(),
          setDiagnostics,
          '安全诊断已生成。',
        )}>生成安全诊断</Button>
        {diagnostics.length > 0 && (
          <ul aria-label="脱敏诊断" className="social-diagnostics-list">
            {diagnostics.map((item) => (
              <li key={item}><Typography.Text code>{item}</Typography.Text></li>
            ))}
          </ul>
        )}
      </Card>
    </section>
  )
}

function createHealthCheckRequest(tenantId: string, deviceId: string): JsonValue {
  const sentAt = new Date()
  const deadlineAt = new Date(sentAt.getTime() + 60_000)
  const taskId = crypto.randomUUID()
  return {
    protocol_version: '1.0',
    message_type: 'task.request',
    message_id: crypto.randomUUID(),
    sent_at: sentAt.toISOString(),
    identity: {
      task_id: taskId,
      correlation_id: crypto.randomUUID(),
      tenant_id: tenantId,
      capability_id: 'social-operations',
      target_device_id: deviceId,
    },
    governance: {
      audit_correlation_id: crypto.randomUUID(),
      approval_id: null,
    },
    idempotency_key: `social-health:${taskId}:attempt:1`,
    deadline_at: deadlineAt.toISOString(),
    task_type: 'social.account.health_check',
    input: { dry_run: true },
    artifact_refs: [],
    extensions: {},
  }
}
