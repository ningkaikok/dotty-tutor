import { useState } from "react";
import { Badge, Button, ButtonLink, Card, Dialog, EmptyState, Navigation, Notice, PageHeader, PageShell, Skeleton, TextField } from "./index";
/** Development-only playground for state, keyboard and narrow-screen inspection. */
export function ComponentCatalog() {
  const [open, setOpen] = useState(false);
  return <PageShell><PageHeader eyebrow="开发工具" title="Bright 组件目录" description="共享组件的交互状态与可访问性预览。数据为演示，不写入业务记录。" />
    <Navigation label="预览导航" items={[{ to: "/", label: "首页" }, { to: "/learn", label: "学生今日" }, { to: "/teacher", label: "教师工作台" }, { to: "/studio", label: "教材工作室" }]} />
    <div className="ui-catalog-grid"><Card><h2>操作</h2><div className="ui-actions"><Button variant="primary">主操作</Button><Button>次操作</Button><Button variant="accent">继续</Button><Button variant="danger">删除</Button><Button disabled>暂不可用</Button><Button busy>正在保存</Button><ButtonLink to="/studio">打开教材</ButtonLink></div></Card>
    <Card><h2>标签与反馈</h2><div className="ui-actions"><Badge tone="success">已完成</Badge><Badge tone="warning">待复核</Badge><Badge tone="info">参考信息</Badge></div><Notice tone="danger">读取失败，请重试</Notice><Notice tone="success">保存成功</Notice></Card>
    <Card><h2>表单</h2><TextField label="教材名称" hint="使用教材原始标题" placeholder="填写名称" /><TextField label="来源页码" type="number" error="请填写有效页码" /></Card>
    <Card><h2>对话框</h2><Button onClick={() => setOpen(true)}>查看复核说明</Button><Dialog open={open} title="复核说明" onClose={() => setOpen(false)}><p>来源、课程和媒体分别由教师复核，保存来源意见不会自动发布课程。</p></Dialog></Card>
    <EmptyState title="当前没有待处理任务" action={<ButtonLink to="/studio">查看我的教材</ButtonLink>}>错误状态使用明确提示，不能伪装成空列表。</EmptyState><Card><h2>加载状态</h2><Skeleton /></Card></div>
  </PageShell>;
}
