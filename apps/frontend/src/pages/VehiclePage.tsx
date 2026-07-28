import { useEffect, useMemo, useState } from "react";
import { Button, Card, Descriptions, Drawer, Form, Input, Modal, Select, Space, Table, Tag, message } from "antd";

import { AttributeValueDetail, ComponentInstance, EntityType, SystemCatalog, VehicleDetailResponse, VehicleInstance, VehicleListResponse, fetchJson, postJson } from "../api";

function formatValue(item: AttributeValueDetail) {
  if (item.value === null || item.value === undefined || item.value === "") return "-";
  return `${String(item.value)}${item.unit ? ` ${item.unit}` : ""}`;
}

export function VehiclePage() {
  const [vehicles, setVehicles] = useState<VehicleInstance[]>([]);
  const [vehicleTotal, setVehicleTotal] = useState(0);
  const [sourceCounts, setSourceCounts] = useState<Record<string, number>>({});
  const [components, setComponents] = useState<ComponentInstance[]>([]);
  const [entityTypes, setEntityTypes] = useState<EntityType[]>([]);
  const [systems, setSystems] = useState<SystemCatalog[]>([]);
  const [vehicleModalOpen, setVehicleModalOpen] = useState(false);
  const [componentModalOpen, setComponentModalOpen] = useState(false);
  const [vehicleForm] = Form.useForm();
  const [componentForm] = Form.useForm();
  const [messageApi, contextHolder] = message.useMessage();
  const [vehicleKeyword, setVehicleKeyword] = useState("");
  const [vehiclePage, setVehiclePage] = useState(1);
  const [vehiclePageSize, setVehiclePageSize] = useState(20);
  const [vehicleLoading, setVehicleLoading] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detail, setDetail] = useState<VehicleDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [fieldKeyword, setFieldKeyword] = useState("");

  const loadVehicles = (page = vehiclePage, pageSize = vehiclePageSize, keyword = vehicleKeyword) => {
    setVehicleLoading(true);
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    if (keyword.trim()) params.set("keyword", keyword.trim());
    void fetchJson<VehicleListResponse>(`/api/assets/vehicles?${params.toString()}`)
      .then((payload) => {
        setVehicles(payload.items);
        setVehicleTotal(payload.total);
        setVehiclePage(payload.page);
        setVehiclePageSize(payload.page_size);
        setSourceCounts(payload.source_counts);
      })
      .finally(() => setVehicleLoading(false));
  };

  const loadData = () => {
    loadVehicles();
    void fetchJson<ComponentInstance[]>("/api/assets/components").then(setComponents);
    void fetchJson<EntityType[]>("/api/metadata/entity-types").then(setEntityTypes);
    void fetchJson<SystemCatalog[]>("/api/metadata/systems").then(setSystems);
  };

  useEffect(() => {
    loadData();
  }, []);

  const vehicleNameById = useMemo(() => new Map(vehicles.map((item) => [item.id, item.vehicle_name])), [vehicles]);
  const systemNameById = useMemo(() => new Map(systems.map((item) => [item.id, item.name])), [systems]);
  const entityTypeById = useMemo(() => new Map(entityTypes.map((item) => [item.id, item])), [entityTypes]);
  const autohomeCount = sourceCounts.autohome || 0;

  const componentEntityOptions = entityTypes
    .filter((item) => item.category === "component")
    .map((item) => ({ value: item.id, label: `${item.name} (${item.code})`, systemId: item.default_system_id }));

  const createVehicle = async () => {
    const values = await vehicleForm.validateFields();
    await postJson<VehicleInstance>("/api/assets/vehicles", {
      vehicle_code: values.vehicle_code,
      vehicle_name: values.vehicle_name,
      values: [
        { attribute_id: 1, attribute_code: "brand", value: values.brand || "" },
        { attribute_id: 2, attribute_code: "model_name", value: values.model_name || values.vehicle_name },
      ],
    });
    messageApi.success("车型已创建，并自动生成四大虚拟系统");
    vehicleForm.resetFields();
    setVehicleModalOpen(false);
    loadVehicles(1, vehiclePageSize, vehicleKeyword);
  };

  const createComponent = async () => {
    const values = await componentForm.validateFields();
    const entityType = entityTypeById.get(values.entity_type_id);
    await postJson<ComponentInstance>("/api/assets/components", {
      vehicle_instance_id: values.vehicle_instance_id,
      system_id: values.system_id,
      entity_type_id: values.entity_type_id,
      component_code: values.component_code,
      component_name: values.component_name || `${vehicleNameById.get(values.vehicle_instance_id)} ${entityType?.name || ""}`,
      values: [],
    });
    messageApi.success("零部件实例已创建");
    componentForm.resetFields();
    setComponentModalOpen(false);
    loadData();
  };

  const openDetail = async (vehicle: VehicleInstance) => {
    setDetailOpen(true);
    setDetail(null);
    setFieldKeyword("");
    setDetailLoading(true);
    try {
      setDetail(await fetchJson<VehicleDetailResponse>(`/api/assets/vehicles/${vehicle.id}`));
    } finally {
      setDetailLoading(false);
    }
  };

  const detailGroups = useMemo(() => {
    if (!detail) return [];
    const keyword = fieldKeyword.trim().toLowerCase();
    if (!keyword) return detail.groups;
    return detail.groups
      .map((group) => ({
        ...group,
        values: group.values.filter((item) => `${item.attribute_name} ${item.attribute_code} ${String(item.value ?? "")}`.toLowerCase().includes(keyword)),
      }))
      .filter((group) => group.values.length > 0);
  }, [detail, fieldKeyword]);

  return (
    <div className="page">
      {contextHolder}
      <div className="page-title">
        <div>
          <h2>车型与实例维护</h2>
          <p>先创建整车实例，平台自动生成四大虚拟系统；再在系统下添加零部件实例并维护动态属性。</p>
        </div>
        <Space>
          <Button href="/api/import-export/templates/vehicle_instance/csv">下载整车模板</Button>
          <Button onClick={() => setComponentModalOpen(true)}>新增零部件</Button>
          <Button type="primary" onClick={() => setVehicleModalOpen(true)}>新增车型</Button>
        </Space>
      </div>

      <Card
        title="整车实例"
        extra={
          <Space>
            <Tag color="blue">汽车之家 {autohomeCount} 个版本</Tag>
            <Tag>总计 {vehicleTotal} 个实例</Tag>
            <Input.Search
              allowClear
              placeholder="搜索车型版本 / specid"
              style={{ width: 280 }}
              onSearch={(value) => {
                setVehicleKeyword(value);
                loadVehicles(1, vehiclePageSize, value);
              }}
              onChange={(event) => {
                setVehicleKeyword(event.target.value);
                if (!event.target.value) loadVehicles(1, vehiclePageSize, "");
              }}
            />
          </Space>
        }
      >
        <Table
          rowKey="id"
          loading={vehicleLoading}
          dataSource={vehicles}
          pagination={{
            current: vehiclePage,
            pageSize: vehiclePageSize,
            total: vehicleTotal,
            showSizeChanger: true,
            showTotal: (total) => `共 ${total} 条`,
            onChange: (page, pageSize) => loadVehicles(page, pageSize, vehicleKeyword),
          }}
          columns={[
            { title: "实例 ID", dataIndex: "id", width: 90 },
            { title: "整车编码", dataIndex: "vehicle_code" },
            { title: "车型名称", dataIndex: "vehicle_name" },
            { title: "虚拟系统", render: () => "4 个" },
            { title: "零部件实例", render: (_, row) => `${components.filter((item) => item.vehicle_instance_id === row.id).length} 个` },
            { title: "来源", dataIndex: "source_type", render: (value) => <Tag>{value}</Tag> },
            { title: "操作", render: (_, row) => <Space><Button size="small" onClick={() => openDetail(row)}>查看详情</Button><Button size="small" href="/tree">结构树</Button></Space> },
          ]}
        />
      </Card>

      <Card title="零部件实例">
        <Table
          rowKey="id"
          dataSource={components}
          columns={[
            { title: "实例 ID", dataIndex: "id", width: 90 },
            { title: "整车实例", dataIndex: "vehicle_instance_id", render: (value) => vehicleNameById.get(value) || value },
            { title: "虚拟系统", dataIndex: "system_id", render: (value) => systemNameById.get(value) || value },
            { title: "实体类型", dataIndex: "entity_type_id", render: (value) => entityTypeById.get(value)?.name || value },
            { title: "实例编码", dataIndex: "component_code" },
            { title: "实例名称", dataIndex: "component_name" },
            { title: "状态", dataIndex: "status", render: (value) => <Tag color="green">{value}</Tag> },
            { title: "操作", render: () => <Space><Button size="small">编辑属性</Button><Button size="small">上传图片</Button></Space> },
          ]}
        />
      </Card>

      <Card title="新增数据流程">
        <Descriptions bordered column={1}>
          <Descriptions.Item label="新增车型">填写整车编码和整车属性，保存后自动生成悬架、制动、转向、动力四个系统 profile。</Descriptions.Item>
          <Descriptions.Item label="新增零部件">选择整车实例和零部件实体类型，平台按实体默认系统挂到对应虚拟系统节点。</Descriptions.Item>
          <Descriptions.Item label="维护属性">根据零部件实体类型的字段定义动态渲染表单，保存到 instance_attribute_value。</Descriptions.Item>
        </Descriptions>
      </Card>

      <Modal title="新增车型" open={vehicleModalOpen} onCancel={() => setVehicleModalOpen(false)} onOk={createVehicle} okText="创建">
        <Form form={vehicleForm} layout="vertical">
          <Form.Item name="vehicle_name" label="车型名称" rules={[{ required: true }]}>
            <Input placeholder="例如：小鹏 X9" />
          </Form.Item>
          <Form.Item name="vehicle_code" label="整车编码" rules={[{ required: true }]}>
            <Input placeholder="例如：XPENG_X9" />
          </Form.Item>
          <Form.Item name="brand" label="品牌">
            <Input placeholder="例如：小鹏" />
          </Form.Item>
          <Form.Item name="model_name" label="车型简称">
            <Input placeholder="例如：X9" />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title={detail?.vehicle.vehicle_name || "车型版本详情"}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        width={920}
      >
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <Input.Search
            allowClear
            placeholder="搜索属性 / 值，例如 轴距、悬架、续航"
            onSearch={setFieldKeyword}
            onChange={(event) => setFieldKeyword(event.target.value)}
          />
          <Descriptions bordered column={2} size="small">
            <Descriptions.Item label="实例编码">{detail?.vehicle.vehicle_code || "-"}</Descriptions.Item>
            <Descriptions.Item label="来源">{detail?.vehicle.source_type || "-"}</Descriptions.Item>
            <Descriptions.Item label="状态">{detail?.vehicle.status || "-"}</Descriptions.Item>
            <Descriptions.Item label="属性值">{detail ? detail.groups.reduce((sum, group) => sum + group.values.length, 0) : "-"}</Descriptions.Item>
          </Descriptions>
          {detailGroups.map((group) => (
            <Card key={group.group_code} size="small" title={`${group.group_name} (${group.values.length})`}>
              <Table
                rowKey="attribute_id"
                loading={detailLoading}
                size="small"
                pagination={{ pageSize: 10, showSizeChanger: false }}
                dataSource={group.values}
                columns={[
                  { title: "属性", dataIndex: "attribute_name", width: 220 },
                  { title: "编码", dataIndex: "attribute_code", width: 180 },
                  { title: "类型", dataIndex: "attr_type", width: 90 },
                  { title: "值", render: (_, row) => formatValue(row) },
                  { title: "来源", dataIndex: "source", width: 100, render: (value) => <Tag>{value}</Tag> },
                ]}
              />
            </Card>
          ))}
        </Space>
      </Drawer>

      <Modal title="新增零部件实例" open={componentModalOpen} onCancel={() => setComponentModalOpen(false)} onOk={createComponent} okText="创建">
        <Form
          form={componentForm}
          layout="vertical"
          onValuesChange={(changed) => {
            if (changed.entity_type_id) {
              const selected = entityTypeById.get(changed.entity_type_id);
              componentForm.setFieldValue("system_id", selected?.default_system_id);
            }
          }}
        >
          <Form.Item name="vehicle_instance_id" label="整车实例" rules={[{ required: true }]}>
            <Select options={vehicles.map((item) => ({ value: item.id, label: `${item.vehicle_name} (${item.vehicle_code})` }))} />
          </Form.Item>
          <Form.Item name="entity_type_id" label="零部件实体类型" rules={[{ required: true }]}>
            <Select options={componentEntityOptions} />
          </Form.Item>
          <Form.Item name="system_id" label="虚拟系统" rules={[{ required: true }]}>
            <Select options={systems.map((item) => ({ value: item.id, label: item.name }))} />
          </Form.Item>
          <Form.Item name="component_code" label="零部件实例编码" rules={[{ required: true }]}>
            <Input placeholder="例如：XPENG_X9_RF_UPPER_ARM" />
          </Form.Item>
          <Form.Item name="component_name" label="零部件实例名称">
            <Input placeholder="例如：小鹏 X9 右前上摆臂" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
