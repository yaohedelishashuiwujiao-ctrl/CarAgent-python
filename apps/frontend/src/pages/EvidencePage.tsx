import { useEffect, useMemo, useState } from "react";
import { Card, Input, Select, Statistic, Table, Tag, Typography, message } from "antd";

import { EvidenceItem, EvidenceSummary, fetchJson } from "../api";

const statusColor: Record<string, string> = {
  reviewed: "green",
  candidate: "gold",
  draft: "gold",
  needs_review: "orange",
  rejected: "red",
};

export function EvidencePage() {
  const [items, setItems] = useState<EvidenceItem[]>([]);
  const [summary, setSummary] = useState<EvidenceSummary>();
  const [keyword, setKeyword] = useState("");
  const [status, setStatus] = useState<string>();
  const [messageApi, contextHolder] = message.useMessage();

  useEffect(() => {
    Promise.all([
      fetchJson<EvidenceItem[]>("/api/evidence/items"),
      fetchJson<EvidenceSummary>("/api/evidence/summary"),
    ])
      .then(([itemsResponse, summaryResponse]) => {
        setItems(itemsResponse);
        setSummary(summaryResponse);
      })
      .catch((error) => messageApi.error(error instanceof Error ? error.message : "证据加载失败"));
  }, [messageApi]);

  const filtered = useMemo(() => {
    const normalized = keyword.trim().toLowerCase();
    return items.filter((item) => {
      const matchesKeyword = !normalized || `${item.title} ${item.content} ${item.source_type}`.toLowerCase().includes(normalized);
      const matchesStatus = !status || item.review_status === status;
      return matchesKeyword && matchesStatus;
    });
  }, [items, keyword, status]);

  return (
    <div className="page">
      {contextHolder}
      <div className="page-title">
        <div>
          <h2>证据中心</h2>
          <p>统一管理结构化数据、图片标注、视觉识别和线上采集结果，Agent 只基于这些可追溯证据生成报告。</p>
        </div>
      </div>

      <div className="metric-grid">
        <Card><Statistic title="证据总数" value={summary?.total_count ?? 0} suffix="条" /></Card>
        <Card><Statistic title="已审核" value={summary?.reviewed_count ?? 0} suffix="条" /></Card>
        <Card><Statistic title="候选/待复核" value={summary?.candidate_count ?? 0} suffix="条" /></Card>
        <Card><Statistic title="低置信度" value={summary?.low_confidence_count ?? 0} suffix="条" /></Card>
      </div>

      <Card title="证据检索">
        <div className="toolbar">
          <Input.Search
            allowClear
            placeholder="搜索车型、系统、零部件、来源或证据内容"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
          />
          <Select
            allowClear
            placeholder="审核状态"
            value={status}
            onChange={setStatus}
            style={{ minWidth: 180 }}
            options={[
              { value: "reviewed", label: "已审核" },
              { value: "candidate", label: "候选" },
              { value: "draft", label: "草稿" },
              { value: "needs_review", label: "待复核" },
              { value: "rejected", label: "已拒绝" },
            ]}
          />
        </div>
        <Table
          rowKey="id"
          dataSource={filtered}
          columns={[
            { title: "证据类型", dataIndex: "evidence_type", render: (value) => <Tag>{value}</Tag> },
            { title: "标题", dataIndex: "title", width: 220 },
            { title: "来源", dataIndex: "source_type" },
            {
              title: "状态",
              dataIndex: "review_status",
              render: (value) => <Tag color={statusColor[value] || "default"}>{value}</Tag>,
            },
            {
              title: "置信度",
              dataIndex: "confidence",
              render: (value) => (typeof value === "number" ? value.toFixed(2) : "-"),
            },
            {
              title: "内容",
              dataIndex: "content",
              render: (value) => <Typography.Text>{value}</Typography.Text>,
            },
          ]}
        />
      </Card>
    </div>
  );
}
