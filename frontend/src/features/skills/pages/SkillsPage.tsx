import { Alert, Button, Card, Empty, Flex, Modal, Space, Tag, Typography } from 'antd'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { getApiErrorMessage } from '../../auth/api/errors'
import { useCreateSkill, useSkills } from '../api/queries'
import type { SkillStatus } from '../api/skills'
import './skills.css'

const skillStatusLabels: Record<SkillStatus, { label: string, color: string }> = {
  draft: { label: '草稿', color: 'default' },
  published: { label: '已发布', color: 'success' },
  archived: { label: '已下线', color: 'warning' },
  deleted: { label: '已删除', color: 'error' },
}

export function SkillsPage({ canManageSkills }: { canManageSkills: boolean }) {
  const skills = useSkills()
  const create = useCreateSkill()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [file, setFile] = useState<File>()

  const submit = async () => {
    if (!canManageSkills || !file) return
    try {
      const skill = await create.mutateAsync(file)
      setOpen(false)
      setFile(undefined)
      navigate(`/skills/${skill.id}`)
    } catch {
      // Mutation 错误在弹窗内统一展示。
    }
  }

  return (
    <section>
      <Flex align="center" justify="space-between" gap={16}>
        <div>
          <Typography.Title level={2}>Skill 中心</Typography.Title>
          <Typography.Text type="secondary">管理企业 Skill、版本和发布状态</Typography.Text>
        </div>
        {canManageSkills && (
          <Button type="primary" onClick={() => setOpen(true)}>上传 Skill</Button>
        )}
      </Flex>
      {skills.data?.length ? (
        <div className="skill-grid">
          {skills.data.map((skill) => (
            <Card
              key={skill.id}
              hoverable
              title={skill.name}
              onClick={() => navigate(`/skills/${skill.id}`)}
              extra={(
                <Tag color={skillStatusLabels[skill.status].color}>
                  {skillStatusLabels[skill.status].label}
                </Tag>
              )}
            >
              <Typography.Paragraph>{skill.description}</Typography.Paragraph>
              <Typography.Text type="secondary">
                最新版本 {skill.latest_version}
                {skill.published_version ? ` · 已发布版本 ${skill.published_version}` : ''}
              </Typography.Text>
            </Card>
          ))}
        </div>
      ) : (
        <Card className="skill-empty"><Empty description="还没有 Skill" /></Card>
      )}
      <Modal
        title="上传 Skill"
        open={open}
        okText="创建 Skill"
        cancelText="取消"
        okButtonProps={{ disabled: !file }}
        confirmLoading={create.isPending}
        onOk={submit}
        onCancel={() => {
          setOpen(false)
          setFile(undefined)
          create.reset()
        }}
      >
        <Space orientation="vertical" size="middle" className="skill-upload">
          {create.isError && (
            <Alert type="error" showIcon title={getApiErrorMessage(create.error, 'Skill 上传失败')} />
          )}
          <input
            aria-label="Skill ZIP"
            type="file"
            accept=".zip,application/zip"
            onChange={(event) => setFile(event.target.files?.[0])}
          />
          <Typography.Text type="secondary">ZIP 根目录必须包含 SKILL.md</Typography.Text>
        </Space>
      </Modal>
    </section>
  )
}
