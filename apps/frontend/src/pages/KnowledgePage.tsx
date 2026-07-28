import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Collapse,
  Descriptions,
  Divider,
  Input,
  List,
  Select,
  Space,
  Spin,
  Steps,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import {
  FileSearchOutlined,
  ReloadOutlined,
  SearchOutlined,
} from "@ant-design/icons";

import {
  KnowledgeSearchResponse,
  KnowledgeWorkspaceStatus,
  fetchJson,
} from "../api";

const DEFAULT_QUERY = "制动液";
const SAMPLE_QUERIES = ["制动液", "胎压监测", "遥控泊车", "儿童锁", "悬架"];

function statusColor(status: string) {
  if (status === "ready") return "green";
  if (status === "partial") return "gold";
  if (status === "degraded") return "red";
  return "default";
}

function statusLabel(status: string) {
  if (status === "ready") return "已就绪";
  if (status === "partial") return "部分完成";
  if (status === "degraded") return "降级";
  return "待接入";
}

function formatBytes(value?: number | null) {
  if (!value || value <= 0) return "-";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  return `${Math.round(value / (1024 * 1024))} MB`;
}

function formatDateTime(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function KnowledgePage() {
  const [workspace, setWorkspace] = useState<KnowledgeWorkspaceStatus>();
  const [query, setQuery] = useState(DEFAULT_QUERY);
  const [topK, setTopK] = useState(5);
  const [searchResult, setSearchResult] = useState<KnowledgeSearchResponse>();
  const [loadingWorkspace, setLoadingWorkspace] = useState(false);
  const [loadingSearch, setLoadingSearch] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const loadWorkspace = async () => {
    setLoadingWorkspace(true);
    try {
      const response = await fetchJson<KnowledgeWorkspaceStatus>("/api/knowledge/status");
      setWorkspace(response);
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : "加载知识工作台失败");
    } finally {
      setLoadingWorkspace(false);
    }
  };

  const runSearch = async (nextQuery = query, nextTopK = topK) => {
    const trimmed = nextQuery.trim();
    if (!trimmed) {
      messageApi.warning("请输入检索词");
      return;
    }
    setQuery(nextQuery);
    setTopK(nextTopK);
    setLoadingSearch(true);
    try {
      const response = await fetchJson<KnowledgeSearchResponse>(`/api/knowledge/test?query=${encodeURIComponent(trimmed)}&top_k=${nextTopK}`);
      setSearchResult(response);
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : "检索预览失败");
    } finally {
      setLoadingSearch(false);
    }
  };

  useEffect(() => {
    void loadWorkspace();
    void runSearch(DEFAULT_QUERY, 5);
  }, []);

  const stageItems = useMemo(
    () =>
      (workspace?.stages || []).map((stage) => ({
        title: (
          <Space size={8}>
            <span>{stage.name}</span>
            <Tag color={statusColor(stage.status)}>{statusLabel(stage.status)}</Tag>
          </Space>
        ),
        description: (
          <div className="knowledge-step-description">
            <div>{stage.summary}</div>
            <Space wrap size={6} className="knowledge-step-metrics">
              {stage.metrics.map((metric) => (
                <Tag key={`${stage.key}-${metric.label}`}>{metric.label}: {metric.value}</Tag>
              ))}
            </Space>
          </div>
        ),
      })),
    [workspace?.stages],
  );

  const sourceColumns = [
    { title: "ID", dataIndex: "id", width: 180, render: (value: string) => <Typography.Text code>{value}</Typography.Text> },
    { title: "品牌/车型", render: (_: unknown, row: KnowledgeWorkspaceStatus["source_samples"][number]) => `${row.brand} / ${row.model}` },
    { title: "年份", dataIndex: "year", width: 90 },
    { title: "来源类型", dataIndex: "source_type", width: 140, render: (value: string) => <Tag>{value}</Tag> },
    { title: "入口 URL", dataIndex: "official_url", ellipsis: true, render: (value: string) => <Typography.Text code>{value}</Typography.Text> },
  ];

  const artifactColumns = [
    { title: "ID", dataIndex: "id", width: 180, render: (value: string) => <Typography.Text code>{value}</Typography.Text> },
    { title: "品牌/车型", render: (_: unknown, row: KnowledgeWorkspaceStatus["artifact_samples"][number]) => `${row.brand} / ${row.model}` },
    { title: "来源类型", dataIndex: "source_type", width: 140, render: (value: string) => <Tag>{value}</Tag> },
    { title: "状态", dataIndex: "status", width: 100, render: (value: string) => <Tag color={statusColor(value)}>{statusLabel(value)}</Tag> },
    { title: "大小", dataIndex: "bytes", width: 100, render: (value: number | null) => formatBytes(value) },
    { title: "工件路径", dataIndex: "artifact_path", ellipsis: true, render: (value: string | null) => <Typography.Text code>{value || "-"}</Typography.Text> },
  ];

  const sourceCollapseItems = [
    {
      key: "source-pool",
      label: "来源池",
      children: (
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="snapshot">{workspace?.snapshot_name || "-"}</Descriptions.Item>
            <Descriptions.Item label="生成时间">{formatDateTime(workspace?.generated_at)}</Descriptions.Item>
          </Descriptions>
          <Table rowKey="id" size="small" pagination={false} columns={sourceColumns} dataSource={workspace?.source_samples || []} />
        </Space>
      ),
    },
    {
      key: "discovery",
      label: "抓取与发现",
      children: (
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Alert
            type="info"
            showIcon
            message="这一步把官网入口、手册页和直链 PDF 变成可追踪的本地快照。"
          />
          <Table rowKey="id" size="small" pagination={false} columns={artifactColumns} dataSource={(workspace?.artifact_samples || []).slice(0, 4)} />
          <Table
            rowKey="id"
            size="small"
            pagination={false}
            columns={artifactColumns}
            dataSource={(workspace?.artifact_samples || []).slice(4, 8)}
          />
        </Space>
      ),
    },
    {
      key: "indexing",
      label: "文本抽取与索引准备",
      children: (
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Alert
            type="success"
            showIcon
            message="当前检索使用与 Agent 相同的本地 RAG 索引，返回可追溯的文档 chunk 和官方来源。"
          />
          <Table
            rowKey="id"
            size="small"
            pagination={false}
            columns={artifactColumns}
            dataSource={(workspace?.artifact_samples || []).slice(8, 12)}
          />
          <Typography.Paragraph className="knowledge-note">
            后续可将当前稀疏向量索引升级为 dense embedding、向量库和 rerank，而不改变 Agent 调用方式。
          </Typography.Paragraph>
        </Space>
      ),
    },
    {
      key: "release",
      label: "发布版本",
      children: (
        <List
          size="small"
          dataSource={workspace?.versions || []}
          renderItem={(item) => (
            <List.Item>
              <List.Item.Meta
                title={
                  <Space size={8}>
                    <Typography.Text strong>{item.name}</Typography.Text>
                    <Tag>{item.state}</Tag>
                  </Space>
                }
                description={item.detail}
              />
            </List.Item>
          )}
        />
      ),
    },
  ];

  return (
    <div className="page knowledge-page">
      {contextHolder}
      <div className="page-title">
        <div>
          <h2>知识工作台</h2>
          <p>把语料来源、抓取、抽取、索引、检索和版本放在同一页。后续新增文档也沿着这条流水线走。</p>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={loadWorkspace} loading={loadingWorkspace}>
            刷新状态
          </Button>
          <Button type="primary" icon={<SearchOutlined />} onClick={() => void runSearch(query, topK)} loading={loadingSearch}>
            检索预览
          </Button>
        </Space>
      </div>

      <Alert
        type="info"
        showIcon
        message="当前页面展示真实语料流水线与 Agent 共用的本地 RAG 召回结果，不是写死的单场景预览。"
      />

      <div className="metric-grid">
        {(workspace?.metrics || []).map((metric) => (
          <Card key={metric.label} className="knowledge-metric-card">
            <Typography.Text type="secondary">{metric.label}</Typography.Text>
            <div className="knowledge-metric-value">{metric.value}</div>
            <div className="knowledge-metric-hint">{metric.hint || " "}</div>
          </Card>
        ))}
      </div>

      <Card title="流水线阶段" className="knowledge-workflow-card">
        <Spin spinning={loadingWorkspace}>
          <Steps
            current={Math.max(0, (workspace?.stages || []).findIndex((stage) => stage.status !== "ready"))}
            items={stageItems}
          />
        </Spin>
      </Card>

      <div className="knowledge-grid">
        <Card title="语料分层" extra={<Tag color="blue">{workspace?.snapshot_name || "manual-corpus"}</Tag>}>
          <Collapse accordion items={sourceCollapseItems} />
        </Card>

        <Card
          title="检索预览"
          extra={<Tag color="green">{searchResult ? `${searchResult.total_matches} 条命中` : "等待输入"}</Tag>}
        >
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Space.Compact style={{ width: "100%" }}>
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onPressEnter={() => void runSearch(query, topK)}
                placeholder="输入检索词，例如制动液、胎压监测、遥控泊车"
              />
              <Select
                value={topK}
                onChange={(value) => setTopK(value)}
                style={{ width: 110 }}
                options={[3, 5, 8, 10].map((value) => ({ value, label: `Top ${value}` }))}
              />
              <Button type="primary" icon={<FileSearchOutlined />} onClick={() => void runSearch(query, topK)} loading={loadingSearch}>
                检索
              </Button>
            </Space.Compact>

            <Space wrap>
              {SAMPLE_QUERIES.map((item) => (
                <Button key={item} size="small" onClick={() => void runSearch(item, topK)}>
                  {item}
                </Button>
              ))}
            </Space>

            <Divider style={{ margin: "8px 0" }} />

            {searchResult?.hits?.length ? (
              <List
                itemLayout="vertical"
                dataSource={searchResult.hits}
                renderItem={(item) => (
                  <List.Item className="knowledge-hit">
                    <List.Item.Meta
                      title={
                        <Space size={8} wrap>
                          <Typography.Text strong>{item.title}</Typography.Text>
                          <Tag color="green">score {item.score}</Tag>
                          <Tag>{item.source_type}</Tag>
                        </Space>
                      }
                      description={
                        <Space direction="vertical" size={4} style={{ width: "100%" }}>
                          <div className="knowledge-hit-meta">
                            {item.brand} / {item.model} / {item.year}
                          </div>
                          <Typography.Paragraph className="knowledge-hit-excerpt">{item.excerpt}</Typography.Paragraph>
                          <Space wrap size={6}>
                            {item.matched_terms.map((term) => (
                              <Tag key={`${item.id}-${term}`}>{term}</Tag>
                            ))}
                          </Space>
                          <Typography.Text className="knowledge-path" code>
                            {item.artifact_path}
                          </Typography.Text>
                          {item.text_path ? (
                            <Typography.Text className="knowledge-path" code>
                              {item.text_path}
                            </Typography.Text>
                          ) : null}
                        </Space>
                      }
                    />
                    <a href={item.official_url} target="_blank" rel="noreferrer">
                      打开来源
                    </a>
                  </List.Item>
                )}
              />
            ) : (
              <div className="chart-placeholder">输入检索词后查看命中片段、来源和正文路径</div>
            )}
          </Space>
        </Card>
      </div>

      <Card title="阶段说明">
        <div className="knowledge-notes">
          {(workspace?.stages || []).map((stage) => (
            <div key={stage.key} className="knowledge-stage-note">
              <Space size={8} wrap>
                <Typography.Text strong>{stage.name}</Typography.Text>
                <Tag color={statusColor(stage.status)}>{statusLabel(stage.status)}</Tag>
              </Space>
              <div>{stage.summary}</div>
              <ul>
                {stage.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </Card>

      <Card title="当前快照说明">
        <Typography.Paragraph className="knowledge-note">
          {workspace?.notes?.join(" ")}
        </Typography.Paragraph>
        <Typography.Text type="secondary">生成时间：{formatDateTime(workspace?.generated_at)}</Typography.Text>
      </Card>
    </div>
  );
}
