import {
  Alert,
  Button,
  Card,
  Empty,
  Flex,
  Form,
  Input,
  InputNumber,
  Modal,
  Radio,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from 'antd'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import type { Employee } from '../../employees/api/employees'
import { useEmployees } from '../../employees/api/queries'
import {
  useCreateScheduledTask,
  useDeleteScheduledTask,
  usePauseScheduledTask,
  useResumeScheduledTask,
  useScheduledTasks,
  useUpdateScheduledTask,
} from '../api/queries'
import type {
  ConcurrencyPolicy,
  MisfirePolicy,
  ScheduledTask,
  ScheduleRequest,
} from '../api/scheduled-tasks'
import {
  describeSchedule,
  describeScheduledTaskError,
  isHighFrequencyCron,
  pauseReasonLabels,
} from './schedule-display'
import {
  formatInstantInTimezone,
  supportedTimezones,
  utcToZonedWallClock,
  zonedWallClockToUtc,
} from './zoned-time'


const misfireOptions: { value: MisfirePolicy; label: string }[] = [
  { value: 'skip', label: '跳过错过的触发点' },
  { value: 'run_once', label: '补跑一次' },
  { value: 'run_all', label: '在补跑窗口内逐个补跑' },
]

const concurrencyOptions: { value: ConcurrencyPolicy; label: string }[] = [
  { value: 'skip', label: '上一轮未结束则跳过' },
  { value: 'queue', label: '排队等待上一轮结束' },
  { value: 'allow', label: '允许并发执行' },
]

interface FormValues {
  employeeId: string
  name: string
  scheduleKind: 'cron' | 'once'
  cronExpression: string
  runAt: string
  timezone: string
  inputText: string
  misfirePolicy: MisfirePolicy
  concurrencyPolicy: ConcurrencyPolicy
  maxRetries: number
  retryBackoffSeconds: number
}

const defaultValues: FormValues = {
  employeeId: '',
  name: '',
  scheduleKind: 'cron',
  cronExpression: '0 9 * * 1-5',
  runAt: '',
  timezone: 'Asia/Shanghai',
  inputText: '{}',
  misfirePolicy: 'skip',
  concurrencyPolicy: 'skip',
  maxRetries: 3,
  retryBackoffSeconds: 60,
}

/** 只有已发布、且发布版本开启了定时任务能力的员工才能被调度（后端会再次校验）。 */
function schedulableEmployees(employees: Employee[]): Employee[] {
  return employees.filter((employee) => (
    employee.published_version !== null && employee.definition.capabilities.scheduled_tasks
  ))
}

function parseInputObject(text: string): { ok: true; value: Record<string, unknown> }
  | { ok: false } {
  try {
    const parsed: unknown = JSON.parse(text.trim() === '' ? '{}' : text)
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { ok: false }
    }
    return { ok: true, value: parsed as Record<string, unknown> }
  } catch {
    return { ok: false }
  }
}

export function ScheduledTasksPage() {
  const tasks = useScheduledTasks()
  const employees = useEmployees()
  const [editing, setEditing] = useState<ScheduledTask | undefined>()
  const [isCreating, setIsCreating] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<ScheduledTask | undefined>()
  const pause = usePauseScheduledTask()
  const resume = useResumeScheduledTask()
  const remove = useDeleteScheduledTask()
  const [rowError, setRowError] = useState<string>()

  const employeeNames = new Map(
    (employees.data ?? []).map((employee) => [employee.id, employee.name]),
  )
  const isFormOpen = isCreating || editing !== undefined

  const closeForm = () => {
    setIsCreating(false)
    setEditing(undefined)
  }

  return (
    <section>
      <Flex align="center" justify="space-between" gap={16}>
        <div>
          <Typography.Title level={2}>定时任务中心</Typography.Title>
          <Typography.Text type="secondary">
            按 Cron 或单次预约让数字员工自动发起任务，并查看每次调度的执行记录
          </Typography.Text>
        </div>
        <Button type="primary" onClick={() => setIsCreating(true)}>创建定时任务</Button>
      </Flex>
      {rowError && (
        <Alert
          type="error"
          showIcon
          closable
          title={rowError}
          onClose={() => setRowError(undefined)}
        />
      )}
      {tasks.isPending ? (
        <Flex justify="center" aria-label="正在加载定时任务"><Spin /></Flex>
      ) : tasks.isError || tasks.data === undefined ? (
        <Alert
          type="error"
          showIcon
          title="定时任务加载失败"
          action={<Button onClick={() => void tasks.refetch()}>重新加载</Button>}
        />
      ) : tasks.data.items.length === 0 ? (
        <Card><Empty description="还没有定时任务" /></Card>
      ) : (
        <Table<ScheduledTask>
          rowKey="id"
          dataSource={tasks.data.items}
          pagination={false}
          columns={[
            {
              title: '名称',
              key: 'name',
              render: (_, task) => (
                <Link to={`/scheduled-tasks/${task.id}`}>{task.name}</Link>
              ),
            },
            {
              title: '数字员工',
              key: 'employee',
              render: (_, task) => employeeNames.get(task.employee_id) ?? task.employee_id,
            },
            {
              title: '调度',
              key: 'schedule',
              render: (_, task) => describeSchedule(task.schedule),
            },
            {
              title: '下次执行时间',
              key: 'next_run_at',
              // 按任务自己的时区渲染，与用户填写的 Cron/预约时间自洽。
              render: (_, task) =>
                formatInstantInTimezone(task.next_run_at, task.schedule.timezone),
            },
            {
              title: '状态',
              key: 'status',
              render: (_, task) => (
                <Space orientation="vertical" size={2}>
                  <Tag color={task.enabled ? 'success' : 'default'}>
                    {task.enabled ? '启用中' : '已暂停'}
                  </Tag>
                  {!task.enabled && task.pause_reason && (
                    <Typography.Text type="warning">
                      {pauseReasonLabels[task.pause_reason] ?? task.pause_reason}
                    </Typography.Text>
                  )}
                </Space>
              ),
            },
            {
              title: '操作',
              key: 'actions',
              render: (_, task) => (
                <Space wrap>
                  <Link to={`/scheduled-tasks/${task.id}`}>执行记录</Link>
                  <Button size="small" onClick={() => setEditing(task)}>编辑</Button>
                  {task.enabled ? (
                    <Button
                      size="small"
                      loading={pause.isPending}
                      onClick={() => pause.mutate(task.id, {
                        onError: (error) => setRowError(describeScheduledTaskError(error)),
                      })}
                    >
                      暂停
                    </Button>
                  ) : (
                    <Button
                      size="small"
                      loading={resume.isPending}
                      onClick={() => resume.mutate(task.id, {
                        onError: (error) => setRowError(describeScheduledTaskError(error)),
                      })}
                    >
                      恢复
                    </Button>
                  )}
                  <Button size="small" danger onClick={() => setPendingDelete(task)}>
                    删除
                  </Button>
                </Space>
              ),
            },
          ]}
        />
      )}
      {isFormOpen && (
        <ScheduledTaskFormModal
          task={editing}
          employees={schedulableEmployees(employees.data ?? [])}
          onClose={closeForm}
        />
      )}
      <Modal
        open={pendingDelete !== undefined}
        title="删除定时任务"
        okText="确认删除"
        cancelText="取消"
        okButtonProps={{ danger: true, loading: remove.isPending }}
        onCancel={() => setPendingDelete(undefined)}
        onOk={() => {
          if (pendingDelete === undefined) return
          remove.mutate(pendingDelete.id, {
            onSuccess: () => setPendingDelete(undefined),
            onError: (error) => {
              setRowError(describeScheduledTaskError(error))
              setPendingDelete(undefined)
            },
          })
        }}
      >
        <Typography.Paragraph>
          删除后该任务不再触发，已有执行记录一并移除。此操作不可撤销。
        </Typography.Paragraph>
      </Modal>
    </section>
  )
}

function ScheduledTaskFormModal({
  task,
  employees,
  onClose,
}: {
  task: ScheduledTask | undefined
  employees: Employee[]
  onClose: () => void
}) {
  const isEditing = task !== undefined
  const [form] = Form.useForm<FormValues>()
  const [submitError, setSubmitError] = useState<string>()
  const create = useCreateScheduledTask()
  const update = useUpdateScheduledTask(task?.id ?? '')
  const scheduleKind = Form.useWatch('scheduleKind', form) ?? 'cron'
  const cronExpression = Form.useWatch('cronExpression', form) ?? ''
  const concurrencyPolicy = Form.useWatch('concurrencyPolicy', form) ?? 'skip'

  const initialValues: FormValues = isEditing
    ? {
      employeeId: task.employee_id,
      name: task.name,
      scheduleKind: task.schedule.kind,
      cronExpression: task.schedule.cron_expression ?? defaultValues.cronExpression,
      // `datetime-local` 只接受当地时间；直接回填 UTC ISO 会让输入框变空。
      runAt: task.schedule.kind === 'once'
        ? utcToZonedWallClock(task.schedule.run_at, task.schedule.timezone)
        : '',
      timezone: task.schedule.timezone,
      inputText: JSON.stringify(task.input, null, 2),
      misfirePolicy: task.misfire_policy,
      concurrencyPolicy: task.concurrency_policy,
      maxRetries: task.max_retries,
      retryBackoffSeconds: task.retry_backoff_seconds,
    }
    : defaultValues

  // C16 配额落地前平台侧对高频 + 并发无节流，只提示不拦截（频率治理归 C16 阶段三）。
  const showBurnRateWarning = scheduleKind === 'cron'
    && concurrencyPolicy === 'allow'
    && isHighFrequencyCron(cronExpression)

  const submit = async () => {
    let values: FormValues
    try {
      values = await form.validateFields()
    } catch {
      return // 校验失败：错误由表单内联展示
    }
    const input = parseInputObject(values.inputText)
    if (!input.ok) {
      form.setFields([{ name: 'inputText', errors: ['任务输入必须是合法的 JSON 对象'] }])
      return
    }
    let schedule: ScheduleRequest
    if (values.scheduleKind === 'cron') {
      schedule = {
        kind: 'cron',
        cron_expression: values.cronExpression.trim(),
        timezone: values.timezone,
      }
    } else {
      // 用户没动过预约时间与时区时，原样复用原 run_at，**不做 UTC→当地→UTC 回环**：
      // 当地时间不携带 fold 标识，落在秋季重复小时第二次出现的任务一旦回环就会被
      // 静默提前一个 DST 偏移量。只有用户真的改了时间/时区，才需要重新换算。
      const original = isEditing && task.schedule.kind === 'once' ? task.schedule : undefined
      const timeUntouched = original !== undefined
        && values.runAt === initialValues.runAt
        && values.timezone === initialValues.timezone
      if (original !== undefined && timeUntouched) {
        schedule = { kind: 'once', run_at: original.run_at, timezone: values.timezone }
      } else {
        const runAt = zonedWallClockToUtc(values.runAt, values.timezone)
        if (runAt === null) {
          form.setFields([{ name: 'runAt', errors: ['请填写有效的预约时间'] }])
          return
        }
        schedule = { kind: 'once', run_at: runAt, timezone: values.timezone }
      }
    }
    const payload = {
      name: values.name,
      schedule,
      input: input.value,
      misfire_policy: values.misfirePolicy,
      concurrency_policy: values.concurrencyPolicy,
      max_retries: values.maxRetries,
      retry_backoff_seconds: values.retryBackoffSeconds,
    }
    const options = {
      onSuccess: onClose,
      onError: (error: unknown) => setSubmitError(describeScheduledTaskError(error)),
    }
    if (isEditing) {
      update.mutate(payload, options)
    } else {
      create.mutate({ ...payload, employee_id: values.employeeId }, options)
    }
  }

  return (
    <Modal
      open
      title={isEditing ? '编辑定时任务' : '创建定时任务'}
      onCancel={onClose}
      footer={null}
      destroyOnHidden
      width={640}
    >
      {submitError && <Alert type="error" showIcon title={submitError} />}
      <Form<FormValues> form={form} layout="vertical" initialValues={initialValues} preserve={false}>
        <Form.Item
          name="employeeId"
          label="数字员工"
          rules={[{ required: true, message: '请选择数字员工' }]}
          extra={isEditing ? '定时任务创建后不能改绑数字员工' : undefined}
        >
          <Select
            disabled={isEditing}
            placeholder="选择一个已发布且开启定时任务能力的数字员工"
            options={employees.map((employee) => ({
              value: employee.id,
              label: employee.name,
            }))}
            notFoundContent="没有可调度的数字员工：请先发布员工并在编辑页开启定时任务能力"
          />
        </Form.Item>
        <Form.Item
          name="name"
          label="任务名称"
          rules={[{ required: true, message: '请填写任务名称' }, { max: 200, message: '任务名称最长 200 个字符' }]}
        >
          <Input placeholder="例如：每个工作日早上九点巡检" />
        </Form.Item>
        <Form.Item name="scheduleKind" label="调度类型">
          <Radio.Group>
            <Radio value="cron">Cron 周期</Radio>
            <Radio value="once">单次预约</Radio>
          </Radio.Group>
        </Form.Item>
        {scheduleKind === 'cron' ? (
          <Form.Item
            name="cronExpression"
            label="Cron 表达式"
            // 表达式合法性由后端（cronsim）判定，前端只做与后端一致的必填与长度校验，
            // 不自建解析器——放宽会漏、收紧会误伤合法表达式。
            rules={[
              { required: true, message: '请填写 Cron 表达式' },
              { max: 200, message: 'Cron 表达式最长 200 个字符' },
            ]}
            extra="按所选时区的当地时间解释，例如 0 9 * * 1-5 表示工作日 09:00"
          >
            <Input placeholder="0 9 * * 1-5" />
          </Form.Item>
        ) : (
          <Form.Item
            name="runAt"
            label="预约时间"
            rules={[{ required: true, message: '请选择预约时间' }]}
            extra="按所选时区的当地时间填写，提交时会换算为平台的 UTC 时间"
          >
            <Input type="datetime-local" />
          </Form.Item>
        )}
        <Form.Item
          name="timezone"
          label="时区"
          rules={[{ required: true, message: '请选择时区' }]}
        >
          <Select
            showSearch
            options={supportedTimezones().map((zone) => ({ value: zone, label: zone }))}
          />
        </Form.Item>
        <Form.Item name="inputText" label="任务输入" extra="必须符合该员工发布版本的输入 Schema">
          <Input.TextArea rows={4} placeholder="{}" />
        </Form.Item>
        <Form.Item name="misfirePolicy" label="错过策略">
          <Select options={misfireOptions} />
        </Form.Item>
        <Form.Item name="concurrencyPolicy" label="并发策略">
          <Select options={concurrencyOptions} />
        </Form.Item>
        {showBurnRateWarning && (
          <Alert
            type="warning"
            showIcon
            title="高频调度叠加并发执行会持续消耗模型额度"
            description="该表达式至少每 5 分钟触发一次，且允许并发执行；平台当前不对调度频率做限制，请确认这是预期行为。"
          />
        )}
        <Form.Item name="maxRetries" label="最大重试次数" rules={[{ required: true }]}>
          <InputNumber min={0} max={10} />
        </Form.Item>
        <Form.Item name="retryBackoffSeconds" label="重试退避（秒）" rules={[{ required: true }]}>
          <InputNumber min={1} max={86_400} />
        </Form.Item>
        <Flex justify="flex-end" gap={8}>
          <Button onClick={onClose}>取消</Button>
          <Button
            type="primary"
            loading={create.isPending || update.isPending}
            onClick={() => void submit()}
          >
            {isEditing ? '保存' : '创建'}
          </Button>
        </Flex>
      </Form>
    </Modal>
  )
}
