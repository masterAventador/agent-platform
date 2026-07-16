import { Alert, Button, Card, Descriptions, Flex, Input, Modal, Space, Spin, Tag, Typography } from 'antd'
import { useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { useCreateConversation } from '../../conversations/api/queries'
import {
  collectDynamicInput,
  dynamicFields,
  hasDynamicInputSchema,
  isFileField,
  type DynamicField,
} from '../../dynamic-io/schema'
import { useCreateRun } from '../../runs/api/queries'
import { deleteUnboundFile, uploadFile } from '../../runs/api/runs'
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
  const createConversation = useCreateConversation()
  const [runModalOpen, setRunModalOpen] = useState(false)
  const [task, setTask] = useState('')
  const [selectedFile, setSelectedFile] = useState<PlatformFile | null>(null)
  const [dynamicValues, setDynamicValues] = useState<Record<string, unknown>>({})
  const [schemaFiles, setSchemaFiles] = useState<Record<string, PlatformFile>>({})
  const [selectingFile, setSelectingFile] = useState(false)
  const [attachmentError, setAttachmentError] = useState<string | null>(null)
  const [schemaError, setSchemaError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const submittingRef = useRef(false)
  const submissionKeyRef = useRef<string | null>(null)
  const retainedFileIdRef = useRef<string | null>(null)
  const retainedSchemaFileIdsRef = useRef<Record<string, string>>({})

  if (employee.isPending) {
    return <Flex className="employee-loading" justify="center"><Spin /></Flex>
  }
  if (employee.isError || !employee.data) {
    return <ResourceAccessError error={employee.error} resourceName="数字员工" />
  }

  const data = employee.data
  const published = data.status === 'published'
  const configurationAvailable = isEmployeeConfigurationAvailable(data.definition)
  const conversationAvailable = data.definition.capabilities.conversation
  const fields = dynamicFields(data.definition.input_schema)
  const usesDynamicInput = hasDynamicInputSchema(data.definition.input_schema)
  const resetRunForm = () => {
    setTask('')
    setSelectedFile(null)
    setDynamicValues({})
    setSchemaFiles({})
    setAttachmentError(null)
    setSchemaError(null)
    submissionKeyRef.current = null
    retainedFileIdRef.current = null
    retainedSchemaFileIdsRef.current = {}
  }

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
            <Button onClick={() => {
              resetRunForm()
              setRunModalOpen(true)
            }}>发起任务</Button>
          )}
          {canExecuteRuns && published && configurationAvailable && conversationAvailable && (
            <Button
              loading={createConversation.isPending}
              onClick={async () => {
                const conversation = await createConversation.mutateAsync({
                  employeeId: data.id,
                  title: data.name,
                })
                navigate(`/conversations/${conversation.id}`)
              }}
            >
              开始会话
            </Button>
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
      {createConversation.isError && (
        <Alert
          className="employee-detail-card"
          type="error"
          showIcon
          title={getEmployeeApiErrorMessage(createConversation.error, '会话创建失败，请稍后重试')}
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
        okButtonProps={{
          disabled: !usesDynamicInput && !task.trim(),
          loading: submitting || createRun.isPending,
        }}
        onCancel={() => {
          setRunModalOpen(false)
          resetRunForm()
          createRun.reset()
        }}
        onOk={async () => {
          if (submittingRef.current) return
          submittingRef.current = true
          setSubmitting(true)
          let attachmentIds: string[] = []
          let uploadedFileId: string | null = null
          const uploadedSchemaFileFields: Array<[string, string]> = []
          const idempotencyKey = submissionKeyRef.current ?? crypto.randomUUID()
          submissionKeyRef.current = idempotencyKey
          try {
            let input: Record<string, unknown> = { message: task.trim() }
            if (usesDynamicInput) {
              const selectedSchemaFileNames = Object.fromEntries(
                Object.entries(schemaFiles).map(([name, file]) => [name, file.name]),
              )
              const candidate = collectDynamicInput(
                data.definition.input_schema,
                dynamicValues,
                selectedSchemaFileNames,
              )
              if (!candidate.ok || !candidate.input) {
                setSchemaError(candidate.error ?? '任务输入不符合要求')
                return
              }
              setSchemaError(null)
              const uploadedSchemaFileValues: Record<string, string> = {}
              const schemaFileEntries = Object.entries(schemaFiles)
              for (const [name, file] of schemaFileEntries) {
                const retainedFileId = retainedSchemaFileIdsRef.current[name]
                if (retainedFileId) {
                  uploadedSchemaFileValues[name] = retainedFileId
                  continue
                }
                const storedFile = await uploadFile(data.tenant_id, file)
                uploadedSchemaFileFields.push([name, storedFile.id])
                uploadedSchemaFileValues[name] = storedFile.id
              }
              const collected = collectDynamicInput(
                data.definition.input_schema,
                dynamicValues,
                uploadedSchemaFileValues,
              )
              input = collected.input ?? candidate.input
              attachmentIds = schemaFileEntries.map(([name]) => uploadedSchemaFileValues[name])
            } else if (selectedFile) {
              setAttachmentError(null)
              uploadedFileId = retainedFileIdRef.current
                ?? (await uploadFile(data.tenant_id, selectedFile)).id
              attachmentIds = [uploadedFileId]
            }
            const run = await createRun.mutateAsync({
              input,
              attachmentIds,
              idempotencyKey,
            })
            setRunModalOpen(false)
            resetRunForm()
            navigate(`/runs/${run.id}`)
          } catch {
            if (uploadedSchemaFileFields.length > 0) {
              await Promise.all(uploadedSchemaFileFields.map(async ([fieldName, fileId]) => {
                try {
                  const compensation = await deleteUnboundFile(data.tenant_id, fileId)
                  if (compensation.deleted) {
                    delete retainedSchemaFileIdsRef.current[fieldName]
                  } else {
                    retainedSchemaFileIdsRef.current[fieldName] = fileId
                  }
                } catch {
                  // 服务端 TTL 回收会处理暂时无法补偿的未绑定文件。
                  retainedSchemaFileIdsRef.current[fieldName] = fileId
                }
              }))
              setAttachmentError('任务发起失败，已尽量回收本次上传的附件')
            } else if (uploadedFileId) {
              try {
                const compensation = await deleteUnboundFile(data.tenant_id, uploadedFileId)
                retainedFileIdRef.current = compensation.deleted ? null : uploadedFileId
              } catch {
                // 服务端 TTL 回收会处理暂时无法补偿的未绑定文件。
                retainedFileIdRef.current = uploadedFileId
              }
            } else if (selectedFile) {
              setAttachmentError('附件上传失败，请检查文件类型、大小或网络后重试')
            }
          } finally {
            submittingRef.current = false
            setSubmitting(false)
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
        {attachmentError && (
          <Alert
            className="employee-form-error"
            type="error"
            showIcon
            title={attachmentError}
          />
        )}
        {schemaError && (
          <Alert
            className="employee-form-error"
            type="error"
            showIcon
            title={schemaError}
          />
        )}
        <Typography.Paragraph type="secondary">
          输入希望数字员工完成的任务，本次执行将固定使用已发布版本。
        </Typography.Paragraph>
        {usesDynamicInput ? (
          <SchemaRunFields
            fields={fields}
            values={dynamicValues}
            files={schemaFiles}
            selectingFile={selectingFile}
            onChange={(name, value) => {
              submissionKeyRef.current = null
              retainedSchemaFileIdsRef.current = {}
              setSchemaError(null)
              setDynamicValues((current) => ({ ...current, [name]: value }))
            }}
            onSelectFile={async (field) => {
              setSelectingFile(true)
              try {
                setAttachmentError(null)
                setSchemaError(null)
                const file = await getPlatformAdapter().selectFile({
                  extensions: ['txt', 'md', 'json', 'csv', 'pdf', 'png', 'jpg', 'jpeg', 'docx'],
                })
                if (file) {
                  submissionKeyRef.current = null
                  retainedSchemaFileIdsRef.current = {}
                  setSchemaFiles((current) => ({ ...current, [field.name]: file }))
                }
              } catch {
                setAttachmentError('无法读取所选文件，请重新选择或检查文件访问权限')
              } finally {
                setSelectingFile(false)
              }
            }}
          />
        ) : (
          <Input.TextArea
            aria-label="任务内容"
            value={task}
            rows={5}
            placeholder="例如：整理本周项目进展并输出摘要"
            onChange={(event) => {
              submissionKeyRef.current = null
              setTask(event.target.value)
            }}
          />
        )}
        {!usesDynamicInput && data.definition.capabilities.file_upload && (
          <Space className="employee-run-attachment" orientation="vertical" size={4}>
            <Button
              loading={selectingFile}
              onClick={async () => {
                setSelectingFile(true)
                try {
                  setAttachmentError(null)
                  const file = await getPlatformAdapter().selectFile({
                    extensions: ['txt', 'md', 'json', 'csv', 'pdf', 'png', 'jpg', 'jpeg', 'docx'],
                  })
                  if (file) {
                    submissionKeyRef.current = null
                    retainedFileIdRef.current = null
                    setSelectedFile(file)
                  }
                } catch {
                  setAttachmentError('无法读取所选文件，请重新选择或检查文件访问权限')
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

function SchemaRunFields({
  fields,
  values,
  files,
  selectingFile,
  onChange,
  onSelectFile,
}: {
  fields: DynamicField[]
  values: Record<string, unknown>
  files: Record<string, PlatformFile>
  selectingFile: boolean
  onChange: (name: string, value: unknown) => void
  onSelectFile: (field: DynamicField) => Promise<void>
}) {
  return (
    <Space className="employee-run-schema-fields" direction="vertical" size="middle">
      {fields.map((field) => (
        <Space key={field.name} direction="vertical" size={4}>
          <Typography.Text strong>{field.label}</Typography.Text>
          <SchemaRunField
            field={field}
            file={files[field.name]}
            selectingFile={selectingFile}
            value={values[field.name]}
            onChange={(value) => onChange(field.name, value)}
            onSelectFile={() => onSelectFile(field)}
          />
        </Space>
      ))}
    </Space>
  )
}

function SchemaRunField({
  field,
  file,
  selectingFile,
  value,
  onChange,
  onSelectFile,
}: {
  field: DynamicField
  file?: PlatformFile
  selectingFile: boolean
  value: unknown
  onChange: (value: unknown) => void
  onSelectFile: () => Promise<void>
}) {
  if (isFileField(field.schema)) {
    return (
      <Space direction="vertical" size={4}>
        <Button loading={selectingFile} onClick={onSelectFile}>
          选择 {field.label}
        </Button>
        {file && <Typography.Text>{file.name}</Typography.Text>}
      </Space>
    )
  }
  if (Array.isArray(field.schema.enum) && field.schema.enum.length > 0) {
    return (
      <select
        aria-label={field.label}
        value={enumControlValue(value)}
        onChange={(event) => onChange(parseEnumControlValue(event.target.value))}
      >
        <option value="">请选择</option>
        {field.schema.enum.map((item, index) => (
          <option key={`${index}:${enumControlValue(item)}`} value={enumControlValue(item)}>
            {String(item)}
          </option>
        ))}
      </select>
    )
  }
  if (field.schema.type === 'boolean') {
    return (
      <input
        aria-label={field.label}
        checked={Boolean(value)}
        type="checkbox"
        onChange={(event) => onChange(event.target.checked)}
      />
    )
  }
  if (field.schema.type === 'number' || field.schema.type === 'integer') {
    return (
      <Input
        aria-label={field.label}
        type="number"
        value={typeof value === 'string' || typeof value === 'number' ? value : ''}
        onChange={(event) => onChange(event.target.value)}
      />
    )
  }
  if (field.schema.type === 'array') {
    return (
      <Input.TextArea
        aria-label={field.label}
        rows={3}
        value={typeof value === 'string' ? value : ''}
        onChange={(event) => onChange(event.target.value)}
      />
    )
  }
  return (
    <Input
      aria-label={field.label}
      type={field.schema.format === 'date' ? 'date' : undefined}
      value={typeof value === 'string' ? value : ''}
      onChange={(event) => onChange(event.target.value)}
    />
  )
}

function enumControlValue(value: unknown): string {
  if (value === undefined) return ''
  const encoded = JSON.stringify(value)
  return typeof encoded === 'string' ? encoded : ''
}

function parseEnumControlValue(value: string): unknown {
  if (!value) return undefined
  try {
    return JSON.parse(value)
  } catch {
    return value
  }
}
