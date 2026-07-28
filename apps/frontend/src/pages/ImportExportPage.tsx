import { useEffect, useState } from "react";
import { Button, Card, Descriptions, Space, Steps, Table, Tag, Upload } from "antd";
import { DownloadOutlined, InboxOutlined } from "@ant-design/icons";

import { fetchJson } from "../api";

type ImportTemplate = {
  template_type: string;
  name: string;
  description: string;
  fixed_columns: string[];
  dynamic_columns: string[];
};

export function ImportExportPage() {
  const [templates, setTemplates] = useState<ImportTemplate[]>([]);

  useEffect(() => {
    void fetchJson<ImportTemplate[]>("/api/import-export/templates").then(setTemplates);
  }, []);

  return (
    <div className="page">
      <div className="page-title">
        <div>
          <h2>批量导入导出</h2>
          <p>围绕模板下载、字段解析、数据校验、错误回写和确认入库设计，模板由当前元数据实时生成。</p>
        </div>
        <Space>
          <Button>导出当前查询结果</Button>
          <Button type="primary">导出结构树</Button>
        </Space>
      </div>

      <Card title="导入流程">
        <Steps
          current={0}
          items={[
            { title: "下载模板", description: "按实体类型生成表头" },
            { title: "上传文件", description: "Excel/CSV" },
            { title: "字段解析", description: "固定列 + 动态列" },
            { title: "数据校验", description: "类型、枚举、必填、重复" },
            { title: "确认入库", description: "写入实例和属性值" },
          ]}
        />
      </Card>

      <Card title="模板下载">
        <Table
          rowKey="template_type"
          pagination={false}
          dataSource={templates}
          columns={[
            { title: "模板", dataIndex: "name", width: 220 },
            { title: "说明", dataIndex: "description" },
            {
              title: "固定列",
              dataIndex: "fixed_columns",
              render: (columns: string[]) => columns.map((item) => <Tag key={item}>{item}</Tag>),
            },
            {
              title: "动态列",
              dataIndex: "dynamic_columns",
              render: (columns: string[]) => columns.length ? `${columns.length} 个` : "-",
            },
            {
              title: "操作",
              dataIndex: "template_type",
              render: (value: string) => (
                <Button icon={<DownloadOutlined />} href={`/api/import-export/templates/${value}/csv`}>
                  下载 CSV
                </Button>
              ),
            },
          ]}
        />
      </Card>

      <Card title="上传导入文件">
        <Upload.Dragger beforeUpload={() => false}>
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">点击或拖拽 Excel/CSV 到此区域</p>
          <p className="ant-upload-hint">后续接入 import_job、字段映射、错误行下载和确认入库。</p>
        </Upload.Dragger>
      </Card>

      <Card title="校验规则">
        <Descriptions bordered column={1}>
          <Descriptions.Item label="ID 关联">用户模板使用 code，系统入库时解析为 entity_type_id、system_id、vehicle_instance_id、attribute_id。</Descriptions.Item>
          <Descriptions.Item label="固定列">用于定位实例和关系，例如 vehicle_code、system_code、entity_type_code、component_code。</Descriptions.Item>
          <Descriptions.Item label="动态列">由 entity_attribute 生成，格式为 属性名称[属性编码|类型|单位]。</Descriptions.Item>
          <Descriptions.Item label="错误回写">校验失败的行应导出错误文件，包含 row_number、字段名、错误原因。</Descriptions.Item>
        </Descriptions>
      </Card>
    </div>
  );
}
