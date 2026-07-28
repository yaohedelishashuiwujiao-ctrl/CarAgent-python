import { useEffect, useMemo, useState } from "react";
import { Button, Card, Descriptions, Form, Input, Modal, Select, Space, Switch, Table, Tag, message } from "antd";

import { Attribute, EntityType, SystemCatalog, fetchJson, postJson, putJson } from "../api";

const attributeTypeOptions = [
  "text",
  "long_text",
  "number",
  "integer",
  "enum",
  "multi_enum",
  "date",
  "datetime",
  "boolean",
  "image",
  "file",
  "json",
  "relation",
].map((value) => ({ value, label: value }));

export function MetadataPage() {
  const [entityTypes, setEntityTypes] = useState<EntityType[]>([]);
  const [systems, setSystems] = useState<SystemCatalog[]>([]);
  const [attributes, setAttributes] = useState<Attribute[]>([]);
  const [selectedEntityId, setSelectedEntityId] = useState<number>(1);
  const [entityModalOpen, setEntityModalOpen] = useState(false);
  const [attributeModalOpen, setAttributeModalOpen] = useState(false);
  const [editingEntity, setEditingEntity] = useState<EntityType>();
  const [editingAttribute, setEditingAttribute] = useState<Attribute>();
  const [entityForm] = Form.useForm();
  const [attributeForm] = Form.useForm();
  const [messageApi, contextHolder] = message.useMessage();

  const loadData = () => {
    void fetchJson<EntityType[]>("/api/metadata/entity-types").then((items) => {
      setEntityTypes(items);
      if (!items.some((item) => item.id === selectedEntityId)) {
        setSelectedEntityId(items[0]?.id || 1);
      }
    });
    void fetchJson<SystemCatalog[]>("/api/metadata/systems").then(setSystems);
    void fetchJson<Attribute[]>("/api/metadata/attributes").then(setAttributes);
  };

  useEffect(() => {
    loadData();
  }, []);

  const selectedEntity = entityTypes.find((item) => item.id === selectedEntityId);
  const selectedAttributes = attributes.filter((item) => item.entity_type_id === selectedEntityId);

  const systemNameById = useMemo(() => {
    return new Map(systems.map((item) => [item.id, item.name]));
  }, [systems]);

  const openCreateEntity = () => {
    setEditingEntity(undefined);
    entityForm.resetFields();
    setEntityModalOpen(true);
  };

  const openEditEntity = (entity: EntityType) => {
    setEditingEntity(entity);
    entityForm.setFieldsValue(entity);
    setEntityModalOpen(true);
  };

  const openCreateAttribute = () => {
    setEditingAttribute(undefined);
    attributeForm.resetFields();
    attributeForm.setFieldsValue({ entity_type_id: selectedEntityId, is_importable: true, is_exportable: true });
    setAttributeModalOpen(true);
  };

  const openEditAttribute = (attribute: Attribute) => {
    setEditingAttribute(attribute);
    attributeForm.setFieldsValue(attribute);
    setAttributeModalOpen(true);
  };

  const saveEntity = async () => {
    const values = await entityForm.validateFields();
    if (editingEntity) {
      await putJson<EntityType>(`/api/metadata/entity-types/${editingEntity.id}`, {
        name: values.name,
        description: values.description,
        default_system_id: values.default_system_id,
      });
      messageApi.success("实体类型已更新");
    } else {
      const created = await postJson<EntityType>("/api/metadata/entity-types", {
        category: "component",
        code: values.code,
        name: values.name,
        description: values.description,
        default_system_id: values.default_system_id,
      });
      setSelectedEntityId(created.id);
      messageApi.success("零部件实体已创建");
    }
    entityForm.resetFields();
    setEntityModalOpen(false);
    loadData();
  };

  const saveAttribute = async () => {
    const values = await attributeForm.validateFields();
    const payload = {
      entity_type_id: values.entity_type_id,
      code: values.code,
      name: values.name,
      attr_type: values.attr_type,
      unit: values.unit,
      is_required: Boolean(values.is_required),
      is_searchable: Boolean(values.is_searchable),
      is_importable: Boolean(values.is_importable ?? true),
      is_exportable: Boolean(values.is_exportable ?? true),
    };
    if (editingAttribute) {
      await putJson<Attribute>(`/api/metadata/attributes/${editingAttribute.id}`, payload);
      messageApi.success("属性字段已更新");
    } else {
      await postJson<Attribute>("/api/metadata/attributes", payload);
      messageApi.success("属性字段已创建");
    }
    attributeForm.resetFields();
    setAttributeModalOpen(false);
    loadData();
  };

  return (
    <div className="page">
      {contextHolder}
      <div className="page-title">
        <div>
          <h2>动态元数据管理</h2>
          <p>先维护实体类型，再维护该实体的属性字段；导入模板、表单和结构树都由这套字典驱动。</p>
        </div>
        <Space>
          <Button onClick={openCreateEntity}>新增零部件实体</Button>
          <Button type="primary" onClick={openCreateAttribute} disabled={!selectedEntity}>新增属性字段</Button>
        </Space>
      </div>

      <div className="two-column">
        <Card title="实体字典">
          <Table
            rowKey="id"
            size="small"
            pagination={false}
            dataSource={entityTypes}
            rowClassName={(record) => record.id === selectedEntityId ? "selected-row" : ""}
            onRow={(record) => ({ onClick: () => setSelectedEntityId(record.id) })}
            columns={[
              { title: "ID", dataIndex: "id", width: 64 },
              {
                title: "实体",
                render: (_, row) => (
                  <Space direction="vertical" size={0}>
                    <span>{row.name}</span>
                    <small>{row.code}</small>
                  </Space>
                ),
              },
              {
                title: "分类",
                dataIndex: "category",
                width: 90,
                render: (value) => <Tag color={value === "vehicle" ? "green" : "blue"}>{value === "vehicle" ? "整车" : "零部件"}</Tag>,
              },
            ]}
          />
        </Card>

        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <Card
            title="实体定义"
            extra={<Button size="small" disabled={!selectedEntity || selectedEntity.category === "vehicle"} onClick={() => selectedEntity && openEditEntity(selectedEntity)}>编辑实体</Button>}
          >
            {selectedEntity && (
              <Descriptions bordered column={2}>
                <Descriptions.Item label="实体 ID">{selectedEntity.id}</Descriptions.Item>
                <Descriptions.Item label="分类">{selectedEntity.category === "vehicle" ? "整车" : "零部件"}</Descriptions.Item>
                <Descriptions.Item label="编码">{selectedEntity.code}</Descriptions.Item>
                <Descriptions.Item label="名称">{selectedEntity.name}</Descriptions.Item>
                <Descriptions.Item label="默认系统">{selectedEntity.default_system_id ? systemNameById.get(selectedEntity.default_system_id) : "-"}</Descriptions.Item>
                <Descriptions.Item label="属性数量">{selectedAttributes.length}</Descriptions.Item>
                <Descriptions.Item label="说明" span={2}>{selectedEntity.description || "-"}</Descriptions.Item>
              </Descriptions>
            )}
          </Card>

          <Card title="属性字段字典">
            <Table
              rowKey="id"
              dataSource={selectedAttributes}
              columns={[
                { title: "ID", dataIndex: "id", width: 70 },
                { title: "字段编码", dataIndex: "code" },
                { title: "字段名称", dataIndex: "name" },
                { title: "类型", dataIndex: "attr_type" },
                { title: "单位", dataIndex: "unit", render: (value) => value || "-" },
                { title: "必填", dataIndex: "is_required", render: (value) => value ? "是" : "否" },
                { title: "搜索", dataIndex: "is_searchable", render: (value) => value ? "是" : "否" },
                { title: "导入", dataIndex: "is_importable", render: (value) => value ? "是" : "否" },
                { title: "导出", dataIndex: "is_exportable", render: (value) => value ? "是" : "否" },
                { title: "操作", render: (_, row) => <Button size="small" onClick={() => openEditAttribute(row)}>编辑</Button> },
              ]}
            />
          </Card>

          <Card title="字典规则">
            <Descriptions bordered column={1}>
              <Descriptions.Item label="实体类型">整车和具体零部件是实体类型。新增零部件时必须选择默认虚拟系统。</Descriptions.Item>
              <Descriptions.Item label="属性字段">属性字段挂在实体类型 ID 上，新增字段不修改实例表结构。</Descriptions.Item>
              <Descriptions.Item label="导入模板">模板由当前实体类型的属性字段实时生成，字段编码用于解析。</Descriptions.Item>
            </Descriptions>
          </Card>
        </Space>
      </div>

      <Modal title={editingEntity ? "编辑零部件实体" : "新增零部件实体"} open={entityModalOpen} onCancel={() => setEntityModalOpen(false)} onOk={saveEntity} okText="保存">
        <Form form={entityForm} layout="vertical">
          <Form.Item name="name" label="实体名称" rules={[{ required: true }]}>
            <Input placeholder="例如：上控制臂/上摆臂" />
          </Form.Item>
          <Form.Item name="code" label="实体编码" rules={[{ required: true }]}>
            <Input disabled={Boolean(editingEntity)} placeholder="例如：upper_control_arm" />
          </Form.Item>
          <Form.Item name="default_system_id" label="默认虚拟系统" rules={[{ required: true }]}>
            <Select options={systems.map((item) => ({ value: item.id, label: item.name }))} />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title={editingAttribute ? "编辑属性字段" : "新增属性字段"} open={attributeModalOpen} onCancel={() => setAttributeModalOpen(false)} onOk={saveAttribute} okText="保存">
        <Form form={attributeForm} layout="vertical">
          <Form.Item name="entity_type_id" label="归属实体类型" rules={[{ required: true }]}>
            <Select disabled options={entityTypes.map((item) => ({ value: item.id, label: `${item.name} (${item.code})` }))} />
          </Form.Item>
          <Form.Item name="name" label="字段名称" rules={[{ required: true }]}>
            <Input placeholder="例如：衬套硬度" />
          </Form.Item>
          <Form.Item name="code" label="字段编码" rules={[{ required: true }]}>
            <Input disabled={Boolean(editingAttribute)} placeholder="例如：bushing_hardness" />
          </Form.Item>
          <Form.Item name="attr_type" label="字段类型" rules={[{ required: true }]}>
            <Select options={attributeTypeOptions} />
          </Form.Item>
          <Form.Item name="unit" label="单位">
            <Input placeholder="例如：kg / mm / HRC" />
          </Form.Item>
          <Space>
            <Form.Item name="is_required" label="必填" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item name="is_searchable" label="可搜索" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item name="is_importable" label="可导入" valuePropName="checked">
              <Switch defaultChecked />
            </Form.Item>
            <Form.Item name="is_exportable" label="可导出" valuePropName="checked">
              <Switch defaultChecked />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </div>
  );
}
