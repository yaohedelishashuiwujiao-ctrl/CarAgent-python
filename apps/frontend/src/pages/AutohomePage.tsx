import { useState } from "react";
import { Alert, Button, Card, Descriptions, Form, Input, InputNumber, Space, Statistic, Table, Tag } from "antd";
import { CloudDownloadOutlined, DatabaseOutlined, FileSearchOutlined } from "@ant-design/icons";

import { AutohomeImportResponse, AutohomeScanResponse, postJson } from "../api";

type FormValues = {
  source_dir?: string;
  max_rows?: number;
  max_specs?: number;
};

const DEFAULT_SOURCE_DIR = "/home/zhaoyunpeng/Projects/汽车之家/output_audited";

export function AutohomePage() {
  const [form] = Form.useForm<FormValues>();
  const [scan, setScan] = useState<AutohomeScanResponse | null>(null);
  const [result, setResult] = useState<AutohomeImportResponse | null>(null);
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function runScan() {
    setLoading("scan");
    setError(null);
    try {
      const values = form.getFieldsValue();
      const payload = {
        source_dir: values.source_dir || DEFAULT_SOURCE_DIR,
        max_rows: values.max_rows || undefined,
      };
      setScan(await postJson<AutohomeScanResponse>("/api/autohome/scan", payload));
    } catch (err) {
      setError(err instanceof Error ? err.message : "扫描失败");
    } finally {
      setLoading(null);
    }
  }

  async function runImport(dryRun: boolean) {
    setLoading(dryRun ? "dry-run" : "import");
    setError(null);
    try {
      const values = form.getFieldsValue();
      const payload = {
        source_dir: values.source_dir || DEFAULT_SOURCE_DIR,
        max_specs: values.max_specs || undefined,
        dry_run: dryRun,
      };
      setResult(await postJson<AutohomeImportResponse>("/api/autohome/import", payload));
    } catch (err) {
      setError(err instanceof Error ? err.message : "导入失败");
    } finally {
      setLoading(null);
    }
  }

  return (
    <div className="page">
      <div className="page-title">
        <div>
          <h2>汽车之家数据源</h2>
          <p>读取汽车之家配置长表，清洗字段，自动生成整车属性元数据，并按车型版本写入实例数据。</p>
        </div>
        <Space>
          <Button icon={<FileSearchOutlined />} loading={loading === "scan"} onClick={runScan}>
            扫描字段
          </Button>
          <Button icon={<CloudDownloadOutlined />} loading={loading === "dry-run"} onClick={() => runImport(true)}>
            导入预演
          </Button>
          <Button type="primary" icon={<DatabaseOutlined />} loading={loading === "import"} onClick={() => runImport(false)}>
            执行导入
          </Button>
        </Space>
      </div>

      {error ? <Alert type="error" message={error} showIcon /> : null}

      <Card title="数据源配置">
        <Form form={form} layout="inline" initialValues={{ source_dir: DEFAULT_SOURCE_DIR }}>
          <Form.Item name="source_dir" label="本地输出目录" style={{ minWidth: 520 }}>
            <Input />
          </Form.Item>
          <Form.Item name="max_rows" label="扫描行数">
            <InputNumber min={100} step={1000} placeholder="全部" />
          </Form.Item>
          <Form.Item name="max_specs" label="导入版本数">
            <InputNumber min={1} placeholder="全部" />
          </Form.Item>
        </Form>
      </Card>

      {scan ? (
        <>
          <div className="metric-grid">
            <Card><Statistic title="车系" value={scan.series_count} /></Card>
            <Card><Statistic title="车型版本" value={scan.spec_count} /></Card>
            <Card><Statistic title="发现属性" value={scan.field_count} /></Card>
            <Card><Statistic title="长表行数" value={scan.row_count} /></Card>
          </div>

          <Card title="元数据发现">
            <Table
              rowKey="attribute_code"
              size="small"
              dataSource={scan.fields}
              pagination={{ pageSize: 12 }}
              columns={[
                { title: "属性编码", dataIndex: "attribute_code", width: 150 },
                { title: "分组", dataIndex: "group", width: 130 },
                { title: "属性名称", dataIndex: "field_name", width: 220 },
                { title: "类型", dataIndex: "attr_type", width: 90, render: (value) => <Tag>{value}</Tag> },
                { title: "单位", dataIndex: "unit", width: 90, render: (value) => value || "-" },
                { title: "非空值", dataIndex: "non_empty_count", width: 100 },
                { title: "样例", dataIndex: "sample_values", render: (values: string[]) => values.join(" / ") || "-" },
              ]}
            />
          </Card>
        </>
      ) : null}

      {result ? (
        <Card title="导入结果">
          <Descriptions bordered column={2}>
            <Descriptions.Item label="状态">{result.status}</Descriptions.Item>
            <Descriptions.Item label="模式">{result.dry_run ? "预演" : "写入数据库"}</Descriptions.Item>
            <Descriptions.Item label="车系">{result.series_count}</Descriptions.Item>
            <Descriptions.Item label="车型版本">{result.spec_count}</Descriptions.Item>
            <Descriptions.Item label="属性字段">{result.field_count}</Descriptions.Item>
            <Descriptions.Item label="新建车型实例">{result.vehicle_created}</Descriptions.Item>
            <Descriptions.Item label="更新车型实例">{result.vehicle_updated}</Descriptions.Item>
            <Descriptions.Item label="新建元数据属性">{result.attribute_created}</Descriptions.Item>
            <Descriptions.Item label="复用元数据属性">{result.attribute_reused}</Descriptions.Item>
            <Descriptions.Item label="写入属性值">{result.value_inserted}</Descriptions.Item>
            <Descriptions.Item label="跳过空值">{result.skipped_values}</Descriptions.Item>
            <Descriptions.Item label="来源目录">{result.source_dir}</Descriptions.Item>
          </Descriptions>
          <div className="panel-block" style={{ marginTop: 16 }}>
            <ul className="bullet-list">
              {result.notes.map((note) => <li key={note}>{note}</li>)}
            </ul>
          </div>
        </Card>
      ) : null}
    </div>
  );
}
