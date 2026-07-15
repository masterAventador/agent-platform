import { Alert, Button, Card, Checkbox, Form, Input, Select, Space, Typography } from 'antd'
import { useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { usePublishedSkills } from '../../skills/api/queries'
import { useAvailableTools } from '../../tools/api/queries'
import type { EmployeeWriteDefinition, WorkMode } from '../api/employees'
import { getEmployeeApiErrorMessage } from '../api/errors'
import { useCreateEmployee, useEmployee, useUpdateEmployee } from '../api/queries'
import './employees.css'


interface EmployeeFormValues {
  name: string
  roleDescription: string
  workMode: WorkMode
  systemPrompt: string
  modelAlias: 'general-purpose'
  conversation: boolean
  fileUpload: boolean
  scheduledTasks: boolean
  skillIds: string[]
  toolIds: string[]
}

const defaultValues: EmployeeFormValues = {
  name: '',
  roleDescription: '',
  workMode: 'autonomous',
  systemPrompt: '',
  modelAlias: 'general-purpose',
  conversation: true,
  fileUpload: false,
  scheduledTasks: false,
  skillIds: [],
  toolIds: [],
}

export function EmployeeEditorPage() {
  const { employeeId } = useParams()
  const editingEmployee = useEmployee(employeeId)
  const createEmployee = useCreateEmployee()
  const updateEmployee = useUpdateEmployee(employeeId ?? '')
  const navigate = useNavigate()
  const skills = usePublishedSkills()
  const tools = useAvailableTools()
  const [form] = Form.useForm<EmployeeFormValues>()
  const mutation = employeeId ? updateEmployee : createEmployee
  const selectedWorkMode = Form.useWatch('workMode', form) ?? 'autonomous'
  const legacyDefinition = editingEmployee.data?.definition
  const hasLegacyWorkMode = legacyDefinition?.work_mode !== undefined
    && legacyDefinition.work_mode !== 'autonomous'
  const hasLegacyUnavailableCapability = Boolean(
    legacyDefinition?.capabilities.scheduled_tasks,
  )

  useEffect(() => {
    const employee = editingEmployee.data
    if (!employee) return
    form.setFieldsValue({
      name: employee.definition.name,
      roleDescription: employee.definition.role_description,
      workMode: employee.definition.work_mode,
      systemPrompt: employee.definition.system_prompt,
      modelAlias: 'general-purpose',
      conversation: employee.definition.capabilities.conversation,
      fileUpload: employee.definition.capabilities.file_upload,
      scheduledTasks: false,
      skillIds: employee.definition.skill_ids,
      toolIds: employee.definition.tool_ids,
    })
  }, [editingEmployee.data, form])

  const submit = async (values: EmployeeFormValues) => {
    if (values.workMode !== 'autonomous') {
      form.setFields([{
        name: 'workMode',
        errors: ['当前只支持自主执行，请先显式切换工作模式'],
      }])
      return
    }
    const existing = editingEmployee.data?.definition
    const definition: EmployeeWriteDefinition = {
      name: values.name,
      avatar_url: existing?.avatar_url,
      role_description: values.roleDescription,
      visibility: existing?.visibility ?? 'tenant',
      work_mode: 'autonomous',
      system_prompt: values.systemPrompt,
      model: { kind: 'gateway_alias', alias: 'general-purpose' },
      input_schema: existing?.input_schema ?? { type: 'object' },
      output_schema: existing?.output_schema ?? { type: 'object' },
      capabilities: {
        conversation: values.conversation,
        scheduled_tasks: false,
        file_upload: values.fileUpload,
      },
      skill_ids: values.skillIds,
      tool_ids: values.toolIds,
      knowledge_base_ids: existing?.knowledge_base_ids ?? [],
      approval_policy: existing?.approval_policy ?? {},
      release_strategy: existing?.release_strategy ?? { mode: 'all' },
    }
    try {
      const employee = await mutation.mutateAsync(definition)
      navigate(`/employees/${employee.id}`, { replace: true })
    } catch {
      // 错误由 Mutation 状态统一渲染。
    }
  }

  return (
    <section className="employee-editor">
      <Space orientation="vertical" size={4}>
        <Typography.Title level={2}>{employeeId ? '编辑数字员工' : '创建数字员工'}</Typography.Title>
        <Typography.Text type="secondary">先保存为草稿，确认配置后再发布给企业使用</Typography.Text>
      </Space>

      <Card className="employee-form-card">
        {mutation.isError && (
          <Alert
            className="employee-form-error"
            type="error"
            showIcon
            title={getEmployeeApiErrorMessage(mutation.error, '保存失败，请稍后重试')}
          />
        )}
        {hasLegacyWorkMode && selectedWorkMode !== 'autonomous' && (
          <Alert
            className="employee-form-error"
            type="warning"
            showIcon
            title="历史配置使用了尚未开放的工作模式"
            description="该模式当前不能真实执行，也不能继续保存或发布。请显式切换为自主执行。"
            action={(
              <Button onClick={() => form.setFieldValue('workMode', 'autonomous')}>
                切换为自主执行
              </Button>
            )}
          />
        )}
        {hasLegacyUnavailableCapability && (
          <Alert
            className="employee-form-error"
            type="warning"
            showIcon
            title="历史配置使用了尚未接通的定时任务"
            description="定时任务当前并未真实接通，本次保存会将该声明修正为关闭。"
          />
        )}
        <Form<EmployeeFormValues>
          form={form}
          initialValues={defaultValues}
          layout="vertical"
          requiredMark={false}
          onFinish={submit}
        >
          <Form.Item label="员工名称" name="name" rules={[{ required: true }]}>
            <Input maxLength={200} placeholder="例如：市场研究专员" />
          </Form.Item>
          <Form.Item label="岗位说明" name="roleDescription" rules={[{ required: true }]}>
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item label="工作模式" name="workMode" rules={[{ required: true }]}>
            <Select
              virtual={false}
              options={[
                { value: 'autonomous', label: '自主执行' },
                { value: 'workflow', label: '固定流程（尚未开放）', disabled: true },
                { value: 'hybrid', label: '混合协作（尚未开放）', disabled: true },
              ]}
            />
          </Form.Item>
          <Typography.Paragraph type="secondary">
            当前仅自主执行模式已接入真实运行时；固定流程与混合协作尚未开放。
          </Typography.Paragraph>
          <Form.Item label="系统指令" name="systemPrompt" rules={[{ required: true }]}>
            <Input.TextArea rows={6} />
          </Form.Item>
          <Form.Item label="平台模型" name="modelAlias">
            <Select
              disabled
              virtual={false}
              options={[{ value: 'general-purpose', label: 'general-purpose' }]}
            />
          </Form.Item>
          <Typography.Paragraph type="secondary">
            当前仅开放平台默认模型，供应商与实际模型路由由平台统一管理。
          </Typography.Paragraph>
          <Form.Item label="能力">
            <Space wrap>
              <Form.Item name="conversation" valuePropName="checked" noStyle>
                <Checkbox>支持对话</Checkbox>
              </Form.Item>
              <Form.Item name="fileUpload" valuePropName="checked" noStyle>
                <Checkbox>支持文件上传</Checkbox>
              </Form.Item>
              <Form.Item name="scheduledTasks" valuePropName="checked" noStyle>
                <Checkbox disabled>支持定时任务（尚未接通）</Checkbox>
              </Form.Item>
            </Space>
            <Typography.Paragraph type="secondary">
              文件上传已接通，可在任务发起时选择附件；定时任务尚未接通，保存时始终保持关闭。
            </Typography.Paragraph>
          </Form.Item>
          <Form.Item label="Skills" name="skillIds">
            <Select
              mode="multiple"
              loading={skills.isPending}
              placeholder="选择当前企业已发布的 Skill"
              options={skills.data?.map((skill) => ({
                value: skill.id,
                label: `${skill.name}（版本 ${skill.published_version}）`,
              }))}
            />
          </Form.Item>
          <Form.Item label="Tools" name="toolIds">
            <Select
              mode="multiple"
              loading={tools.isPending}
              placeholder="选择当前企业已启用的 Tool"
              options={tools.data?.map((tool) => {
                const server = tools.servers?.find((candidate) => candidate.id === tool.server_id)
                return {
                  value: tool.id,
                  label: `${server?.name ?? tool.server_id} / ${tool.name}`,
                }
              })}
            />
          </Form.Item>
          <Space>
            <Button
              type="primary"
              htmlType="submit"
              loading={mutation.isPending}
              disabled={selectedWorkMode !== 'autonomous'}
            >
              保存草稿
            </Button>
            <Button onClick={() => navigate(employeeId ? `/employees/${employeeId}` : '/employees')}>
              取消
            </Button>
          </Space>
        </Form>
      </Card>
    </section>
  )
}
