import { Alert, Button, Card, Descriptions, Flex, Modal, Space, Spin, Tag, Typography } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'

import { getApiErrorMessage } from '../../auth/api/errors'
import {
  useAddSkillVersion,
  useDeleteSkill,
  useOfflineSkill,
  usePublishSkillVersion,
  useSkill,
  useSkillFile,
  useSkillUsage,
  useSkillVersionDiff,
  useSkillVersions,
} from '../api/queries'
import type { SkillReviewStatus, SkillStatus } from '../api/skills'
import './skills.css'

const skillStatusLabels: Record<SkillStatus, { label: string, color: string }> = {
  draft: { label: '草稿', color: 'default' },
  published: { label: '已发布', color: 'success' },
  archived: { label: '已下线', color: 'warning' },
  deleted: { label: '已删除', color: 'error' },
}

const reviewStatusLabels: Record<SkillReviewStatus, { label: string, color: string }> = {
  approved: { label: '安全审核通过', color: 'success' },
  blocked: { label: '安全审核阻断', color: 'error' },
}

export function SkillDetailPage({ canManageSkills }: { canManageSkills: boolean }) {
  const { skillId = '' } = useParams()
  const skill = useSkill(skillId)
  const versions = useSkillVersions(skillId)
  const addVersion = useAddSkillVersion(skillId)
  const publish = usePublishSkillVersion(skillId)
  const offline = useOfflineSkill(skillId)
  const deleteSkill = useDeleteSkill(skillId)
  const usage = useSkillUsage(canManageSkills ? skillId : undefined)
  const [selectedVersion, setSelectedVersion] = useState<number>()
  const [selectedPath, setSelectedPath] = useState<string>()
  const [uploadOpen, setUploadOpen] = useState(false)
  const [file, setFile] = useState<File>()

  const currentVersion = useMemo(
    () => versions.data?.find((item) => item.version === selectedVersion),
    [selectedVersion, versions.data],
  )
  const previousVersion = useMemo(() => {
    if (!selectedVersion || !versions.data) return undefined
    return versions.data.find((item) => item.version < selectedVersion)?.version
  }, [selectedVersion, versions.data])
  const diff = useSkillVersionDiff(skillId, previousVersion, selectedVersion)
  const content = useSkillFile(skillId, selectedVersion, selectedPath)

  useEffect(() => {
    const latest = versions.data?.[0]
    if (!latest || selectedVersion) return
    setSelectedVersion(latest.version)
    setSelectedPath('SKILL.md')
  }, [selectedVersion, versions.data])

  const upload = async () => {
    if (!canManageSkills || !file) return
    try {
      const version = await addVersion.mutateAsync(file)
      setSelectedVersion(version.version)
      setSelectedPath('SKILL.md')
      setUploadOpen(false)
      setFile(undefined)
    } catch {
      // Mutation 错误在弹窗内统一展示。
    }
  }

  if (skill.isPending || versions.isPending) {
    return <Flex className="skill-loading" justify="center"><Spin /></Flex>
  }

  if (!skill.data) {
    return <Alert type="error" showIcon title="Skill 不存在或无法访问" />
  }

  return (
    <section className="skill-detail">
      <Flex align="start" justify="space-between" gap={16}>
        <div>
          <Space align="center">
            <Typography.Title level={2}>{skill.data.name}</Typography.Title>
            <Tag color={skillStatusLabels[skill.data.status].color}>
              {skillStatusLabels[skill.data.status].label}
            </Tag>
          </Space>
          <Typography.Paragraph type="secondary">{skill.data.description}</Typography.Paragraph>
          {skill.data.published_version && (
            <Typography.Text>已发布版本 {skill.data.published_version}</Typography.Text>
          )}
        </div>
        {canManageSkills && (
          <Space>
            <Button type="primary" onClick={() => setUploadOpen(true)}>上传新版本</Button>
            {skill.data.status === 'published' && (
              <Button loading={offline.isPending} onClick={() => offline.mutate()}>
                下线 Skill
              </Button>
            )}
            <Button danger loading={deleteSkill.isPending} onClick={() => deleteSkill.mutate()}>
              删除 Skill
            </Button>
          </Space>
        )}
      </Flex>

      <Card className="skill-section" title="版本列表">
        <div className="skill-version-list">
          {versions.data?.map((version) => (
            <Flex key={version.version} className="skill-version" align="center" justify="space-between" gap={16}>
              <div>
                <Typography.Text strong>版本 {version.version}</Typography.Text>
                <Typography.Paragraph type="secondary">{version.description}</Typography.Paragraph>
              </div>
              <Space>
                <Button
                  type={selectedVersion === version.version ? 'primary' : 'default'}
                  onClick={() => {
                    setSelectedVersion(version.version)
                    setSelectedPath('SKILL.md')
                  }}
                >
                  查看版本 {version.version}
                </Button>
                {skill.data.published_version === version.version ? (
                  <Tag color="success">已发布</Tag>
                ) : canManageSkills ? (
                  <Button
                    disabled={version.review_status === 'blocked'}
                    loading={publish.isPending && publish.variables === version.version}
                    onClick={() => canManageSkills && publish.mutate(version.version)}
                  >
                    发布版本 {version.version}
                  </Button>
                ) : null}
                <Tag color={reviewStatusLabels[version.review_status].color}>
                  {reviewStatusLabels[version.review_status].label}
                </Tag>
              </Space>
            </Flex>
          ))}
        </div>
      </Card>

      {currentVersion && (
        <>
          <Card className="skill-section">
            <Typography.Title level={3}>安全审核结果</Typography.Title>
            <Space orientation="vertical" className="skill-upload">
              <Tag color={reviewStatusLabels[currentVersion.review_status].color}>
                {reviewStatusLabels[currentVersion.review_status].label}
              </Tag>
              {currentVersion.security_findings.map((finding) => (
                <div key={`${finding.code}-${finding.path ?? 'bundle'}`}>
                  <Alert
                    type={finding.severity === 'blocker' ? 'error' : finding.severity}
                    showIcon
                    title={finding.message}
                    description={finding.category}
                  />
                  {finding.path && <Typography.Text code>{finding.path}</Typography.Text>}
                </div>
              ))}
            </Space>
          </Card>

          {previousVersion && (
            <Card className="skill-section">
              <Typography.Title level={3}>版本差异</Typography.Title>
              {diff.isPending ? (
                <Spin />
              ) : (
                <Descriptions size="small" column={1}>
                  <Descriptions.Item label={`从版本 ${previousVersion} 到 ${currentVersion.version} 新增`}>
                    {diff.data?.added.length ? diff.data.added.join('、') : '无'}
                  </Descriptions.Item>
                  <Descriptions.Item label="删除">
                    {diff.data?.removed.length ? diff.data.removed.join('、') : '无'}
                  </Descriptions.Item>
                  <Descriptions.Item label="变更">
                    {diff.data?.changed.length ? diff.data.changed.join('、') : '无'}
                  </Descriptions.Item>
                </Descriptions>
              )}
            </Card>
          )}

          {canManageSkills && (
            <Card className="skill-section">
              <Typography.Title level={3}>使用关系</Typography.Title>
              {usage.data?.items.length ? (
                <Space orientation="vertical">
                  {usage.data.items.map((item) => (
                    <Typography.Text key={`${item.employee_id}-${item.relation}-${item.version ?? 'draft'}`}>
                      {item.employee_name} · {item.relation === 'employee_version' ? `已发布员工版本 ${item.version}` : '员工草稿'}
                    </Typography.Text>
                  ))}
                </Space>
              ) : (
                <Typography.Text type="secondary">暂无员工引用</Typography.Text>
              )}
            </Card>
          )}

          <Card className="skill-section" title={`版本 ${currentVersion.version} 文件`}>
            <div className="skill-files-layout">
              <Space orientation="vertical" className="skill-file-list">
                {currentVersion.files.map((path) => (
                  <Button
                    key={path}
                    type={selectedPath === path ? 'primary' : 'text'}
                    onClick={() => setSelectedPath(path)}
                  >
                    {path}
                  </Button>
                ))}
              </Space>
              <div className="skill-file-content">
                <Typography.Title level={5}>{selectedPath}</Typography.Title>
                {content.isPending ? <Spin /> : <pre>{content.data}</pre>}
              </div>
            </div>
          </Card>
        </>
      )}

      <Modal
        title="上传新版本"
        open={uploadOpen}
        okText="上传版本"
        cancelText="取消"
        okButtonProps={{ disabled: !file }}
        confirmLoading={addVersion.isPending}
        onOk={upload}
        onCancel={() => {
          setUploadOpen(false)
          setFile(undefined)
          addVersion.reset()
        }}
      >
        <Space orientation="vertical" size="middle" className="skill-upload">
          {addVersion.isError && (
            <Alert type="error" showIcon title={getApiErrorMessage(addVersion.error, '版本上传失败')} />
          )}
          <input
            aria-label="新版本 ZIP"
            type="file"
            accept=".zip,application/zip"
            onChange={(event) => setFile(event.target.files?.[0])}
          />
        </Space>
      </Modal>
    </section>
  )
}
