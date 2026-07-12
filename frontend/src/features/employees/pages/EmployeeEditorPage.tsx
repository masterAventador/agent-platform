import { Alert, Button, Card, Checkbox, Form, Input, Select, Space, Typography } from 'antd'
import { useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { getApiErrorMessage } from '../../auth/api/errors'
import { usePublishedSkills } from '../../skills/api/queries'
import type { EmployeeDefinition, WorkMode } from '../api/employees'
import { useCreateEmployee, useEmployee, useUpdateEmployee } from '../api/queries'
import './employees.css'


interface EmployeeFormValues {
  name: string
  roleDescription: string
  workMode: WorkMode
  systemPrompt: string
  modelProvider: string
  modelName: string
  conversation: boolean
  fileUpload: boolean
  scheduledTasks: boolean
  skillIds: string[]
}

const defaultValues: EmployeeFormValues = {
  name: '',
  roleDescription: '',
  workMode: 'autonomous',
  systemPrompt: '',
  modelProvider: 'openai',
  modelName: 'gpt-5',
  conversation: true,
  fileUpload: false,
  scheduledTasks: false,
  skillIds: [],
}

export function EmployeeEditorPage() {
  const { employeeId } = useParams()
  const editingEmployee = useEmployee(employeeId)
  const createEmployee = useCreateEmployee()
  const updateEmployee = useUpdateEmployee(employeeId ?? '')
  const navigate = useNavigate()
  const skills = usePublishedSkills()
  const [form] = Form.useForm<EmployeeFormValues>()
  const mutation = employeeId ? updateEmployee : createEmployee

  useEffect(() => {
    const employee = editingEmployee.data
    if (!employee) return
    form.setFieldsValue({
      name: employee.definition.name,
      roleDescription: employee.definition.role_description,
      workMode: employee.definition.work_mode,
      systemPrompt: employee.definition.system_prompt,
      modelProvider: employee.definition.model.provider,
      modelName: employee.definition.model.name,
      conversation: employee.definition.capabilities.conversation,
      fileUpload: employee.definition.capabilities.file_upload,
      scheduledTasks: employee.definition.capabilities.scheduled_tasks,
      skillIds: employee.definition.skill_ids,
    })
  }, [editingEmployee.data, form])

  const submit = async (values: EmployeeFormValues) => {
    const existing = editingEmployee.data?.definition
    const definition: EmployeeDefinition = {
      name: values.name,
      avatar_url: existing?.avatar_url,
      role_description: values.roleDescription,
      visibility: existing?.visibility ?? 'tenant',
      work_mode: values.workMode,
      system_prompt: values.systemPrompt,
      model: { provider: values.modelProvider, name: values.modelName },
      input_schema: existing?.input_schema ?? { type: 'object' },
      output_schema: existing?.output_schema ?? { type: 'object' },
      capabilities: {
        conversation: values.conversation,
        scheduled_tasks: values.scheduledTasks,
        file_upload: values.fileUpload,
      },
      skill_ids: values.skillIds,
      tool_ids: existing?.tool_ids ?? [],
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
            title={getApiErrorMessage(mutation.error, '保存失败，请稍后重试')}
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
              options={[
                { value: 'autonomous', label: '自主执行' },
                { value: 'workflow', label: '固定流程' },
                { value: 'hybrid', label: '混合协作' },
              ]}
            />
          </Form.Item>
          <Form.Item label="系统指令" name="systemPrompt" rules={[{ required: true }]}>
            <Input.TextArea rows={6} />
          </Form.Item>
          <Space className="employee-model-row" align="start" size="middle">
            <Form.Item label="模型供应商" name="modelProvider" rules={[{ required: true }]}>
              <Input />
            </Form.Item>
            <Form.Item label="模型名称" name="modelName" rules={[{ required: true }]}>
              <Input />
            </Form.Item>
          </Space>
          <Form.Item label="能力">
            <Space wrap>
              <Form.Item name="conversation" valuePropName="checked" noStyle>
                <Checkbox>支持对话</Checkbox>
              </Form.Item>
              <Form.Item name="fileUpload" valuePropName="checked" noStyle>
                <Checkbox>支持文件上传</Checkbox>
              </Form.Item>
              <Form.Item name="scheduledTasks" valuePropName="checked" noStyle>
                <Checkbox>支持定时任务</Checkbox>
              </Form.Item>
            </Space>
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
          <Space>
            <Button type="primary" htmlType="submit" loading={mutation.isPending}>
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
