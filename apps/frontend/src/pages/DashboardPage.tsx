import { useEffect, useState } from "react";
import { Card, Statistic, Table, Tag } from "antd";

import { RuntimeStatus, fetchJson } from "../api";

const recentRows = [
  { key: 1, name: "小鹏 X9 底盘结构", type: "整车", source: "手工录入", status: "已入库" },
  { key: 2, name: "四大虚拟系统维度", type: "系统维度", source: "平台内置", status: "已发布" },
  { key: 3, name: "左前上摆臂材料数据", type: "零部件实例", source: "导入", status: "待校验" },
];

export function DashboardPage() {
  const [runtime, setRuntime] = useState<RuntimeStatus>();

  useEffect(() => {
    void fetchJson<RuntimeStatus>("/api/runtime/status").then(setRuntime);
  }, []);

  return (
    <div className="page">
      <div className="page-title">
        <div>
          <h2>数据概览</h2>
          <p>关注整车实例、零部件实体类型、虚拟系统维度、导入任务和 AI 分析报告的总体状态。</p>
        </div>
      </div>
      <div className="metric-grid">
        <Card><Statistic title="整车实例" value={1} suffix="个" /></Card>
        <Card><Statistic title="虚拟系统" value={4} suffix="个" /></Card>
        <Card><Statistic title="零部件实体类型" value={5} suffix="类" /></Card>
        <Card><Statistic title="动态字段" value={10} suffix="项" /></Card>
      </div>
      <Card title="最近数据活动">
        <Table
          pagination={false}
          dataSource={recentRows}
          columns={[
            { title: "名称", dataIndex: "name" },
            { title: "类型", dataIndex: "type" },
            { title: "来源", dataIndex: "source" },
            { title: "状态", dataIndex: "status", render: (value) => <Tag color={value === "待校验" ? "gold" : "green"}>{value}</Tag> },
          ]}
        />
      </Card>
      <Card title="生产能力检查">
        <Table
          rowKey="key"
          pagination={false}
          dataSource={runtime?.capabilities ?? []}
          columns={[
            { title: "能力", dataIndex: "name", width: 180 },
            {
              title: "状态",
              dataIndex: "status",
              width: 140,
              render: (value) => {
                const color = value === "ready" ? "green" : value === "demo" ? "orange" : "red";
                const label = value === "ready" ? "已具备" : value === "demo" ? "演示" : "待接入";
                return <Tag color={color}>{label}</Tag>;
              },
            },
            { title: "级别", dataIndex: "level", width: 180, render: (value) => <Tag>{value}</Tag> },
            { title: "说明", dataIndex: "detail" },
          ]}
        />
      </Card>
      <Card title="视觉后端">
        <Table
          pagination={false}
          dataSource={[
            { key: "active", name: "当前主后端", value: runtime?.vision_backend?.active ?? "demo" },
            { key: "primary", name: "主服务 URL", value: runtime?.vision_backend?.primary ?? "unset" },
            { key: "secondary", name: "回退服务 URL", value: runtime?.vision_backend?.secondary ?? "unset" },
          ]}
          columns={[
            { title: "项目", dataIndex: "name", width: 180 },
            { title: "值", dataIndex: "value", render: (value) => <Tag>{String(value)}</Tag> },
          ]}
        />
      </Card>
    </div>
  );
}
