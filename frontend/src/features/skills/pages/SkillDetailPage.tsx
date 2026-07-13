import { Alert, Button, Card, Flex, Modal, Space, Spin, Tag, Typography } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'

import { getApiErrorMessage } from '../../auth/api/errors'
import {
  useAddSkillVersion,
  usePublishSkillVersion,
  useSkill,
  useSkillFile,
  useSkillVersions,
} from '../api/queries'
import './skills.css'

export function SkillDetailPage({ canManageSkills }: { canManageSkills: boolean }) {
  const { skillId = '' } = useParams()
  const skill = useSkill(skillId)
  const versions = useSkillVersions(skillId)
  const addVersion = useAddSkillVersion(skillId)
  const publish = usePublishSkillVersion(skillId)
  const [selectedVersion, setSelectedVersion] = useState<number>()
  const [selectedPath, setSelectedPath] = useState<string>()
  const [uploadOpen, setUploadOpen] = useState(false)
  const [file, setFile] = useState<File>()

  const currentVersion = useMemo(
    () => versions.data?.find((item) => item.version === selectedVersion),
    [selectedVersion, versions.data],
  )
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
            <Tag color={skill.data.status === 'published' ? 'success' : 'default'}>
              {skill.data.status === 'published' ? '已发布' : '草稿'}
            </Tag>
          </Space>
          <Typography.Paragraph type="secondary">{skill.data.description}</Typography.Paragraph>
          {skill.data.published_version && (
            <Typography.Text>已发布版本 {skill.data.published_version}</Typography.Text>
          )}
        </div>
        {canManageSkills && (
          <Button type="primary" onClick={() => setUploadOpen(true)}>上传新版本</Button>
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
                    loading={publish.isPending && publish.variables === version.version}
                    onClick={() => canManageSkills && publish.mutate(version.version)}
                  >
                    发布版本 {version.version}
                  </Button>
                ) : null}
              </Space>
            </Flex>
          ))}
        </div>
      </Card>

      {currentVersion && (
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
