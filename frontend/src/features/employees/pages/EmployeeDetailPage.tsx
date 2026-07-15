import { Alert, Button, Card, Descriptions, Flex, Input, Modal, Space, Spin, Tag, Typography } from 'antd'
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { useCreateRun } from '../../runs/api/queries'
import { uploadFile } from '../../runs/api/runs'
import { ResourceAccessError } from '../../system/components/ResourceAccessError'
import { getPlatformAdapter, type PlatformFile } from '../../../platform'
import { isEmployeeConfigurationAvailable } from '../api/employees'
import { getEmployeeApiErrorMessage } from '../api/errors'
import { useEmployee, usePublishEmployee } from '../api/queries'
import './employees.css'


const modeLabels = {
  autonomous: '自主执行',
  workflow: '固定流程',
  hybrid: '混合协作',
} as const

export function EmployeeDetailPage({
  canManageEmployees,
  canExecuteRuns,
}: {
  canManageEmployees: boolean
  canExecuteRuns: boolean
}) {
  const { employeeId } = useParams()
  const employee = useEmployee(employeeId)
  const publish = usePublishEmployee(employeeId ?? '')
  const navigate = useNavigate()
  const createRun = useCreateRun(employeeId ?? '')
  const [runModalOpen, setRunModalOpen] = useState(false)
  const [task, setTask] = useState('')
  const [selectedFile, setSelectedFile] = useState<PlatformFile | null>(null)
  const [selectingFile, setSelectingFile] = useState(false)

  if (employee.isPending) {
    return <Flex className="employee-loading" justify="center"><Spin /></Flex>
  }
  if (employee.isError || !employee.data) {
    return <ResourceAccessError error={employee.error} resourceName="数字员工" />
  }

  const data = employee.data
  const published = data.status === 'published'
  const configurationAvailable = isEmployeeConfigurationAvailable(data.definition)

  return (
    <section>
      <Flex align="center" justify="space-between" gap={16}>
        <div>
          <Space align="center">
            <Typography.Title level={2}>{data.name}</Typography.Title>
            <Tag color={published ? 'success' : 'default'}>{published ? '已发布' : '草稿'}</Tag>
          </Space>
          <Typography.Text type="secondary">{data.definition.role_description}</Typography.Text>
        </div>
        <Space>
          {canExecuteRuns && published && configurationAvailable && (
            <Button onClick={() => setRunModalOpen(true)}>发起任务</Button>
          )}
          {canManageEmployees && (
            <>
              <Button onClick={() => navigate(`/employees/${data.id}/edit`)}>编辑</Button>
              <Button
                type="primary"
                loading={publish.isPending}
                disabled={!configurationAvailable}
                onClick={() => canManageEmployees && publish.mutate()}
              >
                发布员工
              </Button>
            </>
          )}
        </Space>
      </Flex>

      {!configurationAvailable && (
        <Alert
          className="employee-detail-card"
          type="warning"
          showIcon
          title={published
            ? '已发布版本包含尚未开放的配置，当前不能发起任务'
            : '当前员工包含尚未开放的配置，不能直接发布'}
          description={canManageEmployees
            ? '请进入编辑器，显式切换为自主执行并关闭未接通能力。'
            : '请联系工作区所有者，将配置修正为自主执行并关闭未接通能力。'}
          action={canManageEmployees ? (
            <Button onClick={() => navigate(`/employees/${data.id}/edit`)}>编辑并修正</Button>
          ) : undefined}
        />
      )}
      {canManageEmployees && publish.isError && (
        <Alert
          className="employee-detail-card"
          type="error"
          showIcon
          title={getEmployeeApiErrorMessage(publish.error, '发布失败，请稍后重试')}
        />
      )}

      <Card className="employee-detail-card" title="员工配置">
        <Descriptions column={2}>
          <Descriptions.Item label="工作模式">
            {modeLabels[data.definition.work_mode]}
          </Descriptions.Item>
          <Descriptions.Item label="发布版本">
            {data.published_version ? `版本 ${data.published_version}` : '尚未发布'}
          </Descriptions.Item>
          <Descriptions.Item label="网关模型">
            {data.definition.model.alias}
          </Descriptions.Item>
          <Descriptions.Item label="可见范围">企业成员</Descriptions.Item>
        </Descriptions>
        <Typography.Title level={4}>系统指令</Typography.Title>
        <Typography.Paragraph className="employee-prompt">
          {data.definition.system_prompt}
        </Typography.Paragraph>
      </Card>
      <Modal
        title="发起任务"
        open={runModalOpen}
        okText="确认发起"
        cancelText="取消"
        okButtonProps={{ disabled: !task.trim(), loading: createRun.isPending }}
        onCancel={() => {
          setRunModalOpen(false)
          setTask('')
          setSelectedFile(null)
          createRun.reset()
        }}
        onOk={async () => {
          try {
            const attachmentIds = selectedFile
              ? [(await uploadFile(data.tenant_id, selectedFile)).id]
              : []
            const run = await createRun.mutateAsync({
              input: { message: task.trim() },
              attachmentIds,
            })
            setRunModalOpen(false)
            setTask('')
            setSelectedFile(null)
            navigate(`/runs/${run.id}`)
          } catch {
            // Mutation 错误在弹窗内统一渲染，避免 Modal onOk 泄漏 rejection。
          }
        }}
      >
        {createRun.isError && (
          <Alert
            className="employee-form-error"
            type="error"
            showIcon
            title={getEmployeeApiErrorMessage(createRun.error, '任务发起失败，请稍后重试')}
          />
        )}
        <Typography.Paragraph type="secondary">
          输入希望数字员工完成的任务，本次执行将固定使用已发布版本。
        </Typography.Paragraph>
        <Input.TextArea
          aria-label="任务内容"
          value={task}
          rows={5}
          placeholder="例如：整理本周项目进展并输出摘要"
          onChange={(event) => setTask(event.target.value)}
        />
        {data.definition.capabilities.file_upload && (
          <Space className="employee-run-attachment" orientation="vertical" size={4}>
            <Button
              loading={selectingFile}
              onClick={async () => {
                setSelectingFile(true)
                try {
                  const file = await getPlatformAdapter().selectFile({
                    extensions: ['txt', 'md', 'json', 'csv', 'pdf', 'png', 'jpg', 'jpeg', 'docx'],
                  })
                  if (file) setSelectedFile(file)
                } finally {
                  setSelectingFile(false)
                }
              }}
            >
              选择文件
            </Button>
            {selectedFile && <Typography.Text>{selectedFile.name}</Typography.Text>}
          </Space>
        )}
      </Modal>
    </section>
  )
}
